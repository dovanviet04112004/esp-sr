"""score rebuilds the board's clean channel with srpipe's chain and flags any sample off by more than the tolerance."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest
from srpipe.dsp.afe.chain import Chain

from srhost import score
from srhost.generated import grid

HOP = grid.HOP_SAMPLES
HOPS = 40


def board_session(tmp_path: Path) -> tuple[Path, dict[str, np.ndarray]]:
    """A mode 5 session whose clean channel comes from one uninterrupted chain, as on the board."""
    rng = np.random.default_rng(7)
    mics = rng.integers(-3000, 3000, size=(HOPS * HOP, 2), dtype=np.int16)
    chain = Chain()
    clean = np.concatenate([chain.process(mics[k * HOP : (k + 1) * HOP].reshape(-1)).pcm for k in range(HOPS)])
    return tmp_path, {"ch0": mics[:, 0].copy(), "ch1": mics[:, 1].copy(), "clean": clean}


def write(session: Path, channels: dict[str, np.ndarray], gap_offsets: tuple[int, ...] = ()) -> Path:
    for name, pcm in channels.items():
        with wave.open(str(session / f"{name}.wav"), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(grid.SAMPLE_RATE_HZ)
            wav.writeframes(pcm.astype("<i2").tobytes())
    rows = "".join(f"{offset}\t0\t0\n" for offset in gap_offsets)
    (session / "gaps.txt").write_text("offset_samples\texpected_seq\tgot_seq\n" + rows, encoding="utf-8")
    meta = {"session": session.name, "kind": "probe", "fw": "0.1.0+test", "pcm_shift": 16, "seq_gaps": len(gap_offsets)}
    (session / "session.json").write_text(json.dumps(meta), encoding="utf-8")
    return session


def test_a_clean_channel_from_the_same_chain_matches_exactly(tmp_path: Path) -> None:
    session, channels = board_session(tmp_path)
    _, figures, parity = score.score(write(session, channels))
    assert [f.name for f in figures] == ["ch0", "ch1", "clean"]
    assert (parity.hops_compared, parity.hops_skipped, parity.max_abs_lsb, parity.over_tolerance) == (38, 2, 0, 0)


def test_hops_after_a_gap_are_skipped_and_the_rest_still_match(tmp_path: Path) -> None:
    session, channels = board_session(tmp_path)
    lost = slice(10 * HOP, 13 * HOP)
    kept = {name: np.delete(pcm, np.arange(lost.start, lost.stop)) for name, pcm in channels.items()}
    _, _, parity = score.score(write(session, kept, gap_offsets=(10 * HOP,)))
    assert (parity.hops_compared, parity.hops_skipped, parity.max_abs_lsb) == (33, 4, 0)


def test_one_sample_off_by_more_than_the_tolerance_is_caught(tmp_path: Path) -> None:
    session, channels = board_session(tmp_path)
    channels["clean"][20 * HOP + 5] += 5
    _, _, parity = score.score(write(session, channels))
    assert (parity.max_abs_lsb, parity.over_tolerance) == (5, 1)


def test_a_session_without_clean_gets_channel_figures_only(tmp_path: Path) -> None:
    session, channels = board_session(tmp_path)
    del channels["clean"]
    channels["ch0"][:3] = 32767
    meta, figures, parity = score.score(write(session, channels))
    assert parity is None
    assert figures[0].clipped == 3 and figures[0].peak_lsb == 32767
    assert "| ch0 |" in score.table(meta, figures, parity)


def test_a_directory_without_wav_is_refused(tmp_path: Path) -> None:
    (tmp_path / "session.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="no WAV"):
        score.score(tmp_path)
