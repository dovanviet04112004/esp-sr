"""score checks the board's clean channel against srpipe's chain and measures the microphone pair against a label."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest
from srpipe.dsp.afe.chain import Chain, ChainConfig
from srpipe.dsp.emit_golden import plane_wave
from srpipe.generated import array

from srhost import score
from srhost.generated import grid

HOP = grid.HOP_SAMPLES
HOPS = 40


def board_session(tmp_path: Path) -> tuple[Path, dict[str, np.ndarray]]:
    """A mode 5 session whose clean channel comes from one uninterrupted chain, as on the board."""
    rng = np.random.default_rng(7)
    mics = rng.integers(-3000, 3000, size=(HOPS * HOP, 2), dtype=np.int16)
    chain = Chain(cfg=ChainConfig(modules=()))
    clean = np.concatenate([chain.process(mics[k * HOP : (k + 1) * HOP].reshape(-1)).pcm for k in range(HOPS)])
    return tmp_path, {"ch0": mics[:, 0].copy(), "ch1": mics[:, 1].copy(), "clean": clean}


def write(
    session: Path,
    channels: dict[str, np.ndarray],
    gap_offsets: tuple[int, ...] = (),
    doa_deg: int | None = None,
    pcm_shift: int = 16,
) -> Path:
    for name, pcm in channels.items():
        with wave.open(str(session / f"{name}.wav"), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(grid.SAMPLE_RATE_HZ)
            wav.writeframes(pcm.astype("<i2").tobytes())
    rows = "".join(f"{offset}\t0\t0\n" for offset in gap_offsets)
    (session / "gaps.txt").write_text("offset_samples\texpected_seq\tgot_seq\n" + rows, encoding="utf-8")
    meta = {
        "session": session.name,
        "board": "board_without_calib",
        "kind": "probe",
        "fw": "0.1.0+test",
        "pcm_shift": pcm_shift,
        "seq_gaps": len(gap_offsets),
        "doa_deg": doa_deg,
    }
    (session / "session.json").write_text(json.dumps(meta), encoding="utf-8")
    return session


def test_a_clean_channel_from_the_same_chain_matches_exactly(tmp_path: Path) -> None:
    session, channels = board_session(tmp_path)
    result = score.score(write(session, channels))
    parity = result.parity
    assert [f.name for f in result.channels] == ["ch0", "ch1", "clean"]
    assert result.pair is None
    assert (parity.hops_compared, parity.hops_skipped, parity.max_abs_lsb, parity.over_tolerance) == (38, 2, 0, 0)


def test_hops_after_a_gap_are_skipped_and_the_rest_still_match(tmp_path: Path) -> None:
    session, channels = board_session(tmp_path)
    lost = slice(10 * HOP, 13 * HOP)
    kept = {name: np.delete(pcm, np.arange(lost.start, lost.stop)) for name, pcm in channels.items()}
    parity = score.score(write(session, kept, gap_offsets=(10 * HOP,))).parity
    assert (parity.hops_compared, parity.hops_skipped, parity.max_abs_lsb) == (33, 4, 0)


def test_one_sample_off_by_more_than_the_tolerance_is_caught(tmp_path: Path) -> None:
    session, channels = board_session(tmp_path)
    channels["clean"][20 * HOP + 5] += 5
    parity = score.score(write(session, channels)).parity
    assert (parity.max_abs_lsb, parity.over_tolerance) == (5, 1)


def test_a_session_without_clean_gets_channel_figures_only(tmp_path: Path) -> None:
    session, channels = board_session(tmp_path)
    del channels["clean"]
    channels["ch0"][:3] = 32767
    result = score.score(write(session, channels))
    assert result.parity is None
    assert result.channels[0].clipped == 3 and result.channels[0].peak_lsb == 32767
    assert "| ch0 |" in score.table(result)


def tone_hops(amplitude: float, hops: int) -> np.ndarray:
    t = np.arange(hops * HOP) / grid.SAMPLE_RATE_HZ
    return np.rint(amplitude * np.sin(2 * np.pi * 1000.0 * t)).astype(np.int16)


# The ns floor learns a steady tone as noise, so the arithmetic of the level is checked without it.
BEFORE_NS = ("hpf", "balance", "vad")


def test_the_level_into_agc_is_the_mean_of_the_microphones_after_the_high_pass() -> None:
    x = tone_hops(3000.0, HOPS)
    front = score.front_level(x, x, [], None, BEFORE_NS)
    want_dbfs = 20 * np.log10(3000.0 / np.sqrt(2) / 32768.0)
    assert not front.balanced
    assert abs(front.percentiles_dbfs[1] - want_dbfs) <= 1


def test_balance_scales_ch1_before_the_mean() -> None:
    x = tone_hops(3000.0, HOPS)
    plain = score.front_level(x, x, [], None, BEFORE_NS)
    halved = score.front_level(x, x, [], np.full(grid.N_BINS, 0.5, dtype=np.complex64), BEFORE_NS)
    assert halved.balanced
    assert abs(plain.percentiles_dbfs[1] - halved.percentiles_dbfs[1] - 20 * np.log10(1 / 0.75)) <= 1


def test_the_level_into_agc_is_taken_after_the_product_s_ns_floor() -> None:
    x = tone_hops(3000.0, HOPS)
    assert "ns_omlsa" in score.INTO_AGC_MODULES and "agc" not in score.INTO_AGC_MODULES
    before, after = score.front_level(x, x, [], None, BEFORE_NS), score.front_level(x, x, [], None)
    assert before.percentiles_dbfs[1] - after.percentiles_dbfs[1] > 6


def test_a_board_without_a_balance_file_is_scored_without_balance(tmp_path: Path) -> None:
    session, channels = board_session(tmp_path)
    result = score.score(write(session, channels))
    assert result.front is not None and not result.front.balanced
    assert "level into agc, without balance" in score.table(result)


def test_a_directory_without_wav_is_refused(tmp_path: Path) -> None:
    (tmp_path / "session.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="no WAV"):
        score.score(tmp_path)


def clap_session(tmp_path: Path, source_deg: float) -> dict[str, np.ndarray]:
    """Near silence, then claps from a far source at source_deg: ch1 hears them tau samples before ch0."""
    rng = np.random.default_rng(11)
    x = 2.0 * rng.standard_normal(3 * grid.SAMPLE_RATE_HZ)
    for start in range(grid.SAMPLE_RATE_HZ, 2 * grid.SAMPLE_RATE_HZ, 4000):
        x[start : start + 400] += 3000.0 * rng.standard_normal(400) * np.exp(-np.arange(400) / 80.0)
    tau = array.SPACING_M * np.cos(np.radians(source_deg)) / array.SPEED_OF_SOUND_M_S * grid.SAMPLE_RATE_HZ
    spectrum = np.fft.rfft(x)
    lead = np.fft.irfft(spectrum * np.exp(2j * np.pi * np.fft.rfftfreq(len(x)) * tau), n=len(x))
    return {"ch0": np.round(x).astype(np.int16), "ch1": np.round(lead).astype(np.int16)}


@pytest.mark.parametrize("doa_deg", [0, 180])
def test_claps_at_either_end_give_the_contract_spacing_and_sign(tmp_path: Path, doa_deg: int) -> None:
    result = score.score(write(tmp_path, clap_session(tmp_path, doa_deg), doa_deg=doa_deg))
    pair = result.pair
    assert pair.sign_matches is True
    assert pair.spacing_m == pytest.approx(array.SPACING_M, rel=0.03)
    assert pair.delay.tau_samples == pytest.approx(pair.expected_tau_samples, abs=0.06)
    assert "sign matches the label" in score.table(result)


def test_a_label_on_the_wrong_side_is_reported(tmp_path: Path) -> None:
    result = score.score(write(tmp_path, clap_session(tmp_path, 180), doa_deg=0))
    assert result.pair.sign_matches is False
    assert "OPPOSITE" in score.table(result)


def test_broadside_judges_neither_sign_nor_spacing(tmp_path: Path) -> None:
    pair = score.score(write(tmp_path, clap_session(tmp_path, 90), doa_deg=90)).pair
    assert (pair.sign_matches, pair.spacing_m) == (None, None)
    assert all(abs(b.level_diff_db) < 0.1 for b in pair.bands[2:])


def test_channels_of_unequal_length_are_scored_on_their_common_part(tmp_path: Path) -> None:
    session, channels = board_session(tmp_path)
    channels["ch1"] = channels["ch1"][:-HOP]
    result = score.score(write(session, channels))
    assert result.trimmed_samples == HOP
    assert result.parity.hops_compared == HOPS - 1 - score.WARMUP_HOPS
    assert f"last {HOP} samples" in score.table(result)


def test_a_larger_shift_floors_every_sample_as_the_board_shifts(tmp_path: Path) -> None:
    x = np.array([-32768, -9, -8, -1, 0, 1, 7, 8, 32767], dtype=np.int16)
    assert score.floored(x, 14, 13).tolist() == [-16384, -5, -4, -1, 0, 0, 3, 4, 16383]
    assert score.floored(x, 13, 13).tolist() == x.tolist()
    with pytest.raises(ValueError, match="cannot come back"):
        score.floored(x, 12, 13)
    session, channels = board_session(tmp_path)
    result = score.score(write(session, channels, pcm_shift=13), shift=15)
    assert result.parity is None and result.meta["pcm_shift"] == "15 (floored from 13)"
    shifted = channels["ch1"] >> 2
    assert {f.name: f.peak_lsb for f in result.channels}["ch1"] == max(-int(shifted.min()), int(shifted.max()))


def test_ns_takes_off_steady_noise_and_reports_it_by_vad(tmp_path: Path) -> None:
    rng = np.random.default_rng(11)
    noise = (rng.standard_normal((400 * HOP, 2)) * 300).astype(np.int16)
    effect = score.ns_effect(noise[:, 0], noise[:, 1], [], None)
    assert effect.pause_hops + effect.speech_hops == 400 - score.WARMUP_HOPS
    assert effect.pause_hops > 9 * effect.speech_hops and effect.pause_db >= 10


def test_doa_is_read_on_speech_hops_against_the_label() -> None:
    rng = np.random.default_rng(5)
    pair = plane_wave(rng.uniform(-0.3, 0.3, 4 * HOPS * HOP), 45.0)
    pcm = np.rint(pair * 32768.0).astype(np.int16)
    front = score.front_level(pcm[:, 0], pcm[:, 1], [], None, ("hpf", "doa", "vad"), doa_label_deg=45)
    assert front.doa_hops > 0 and abs(front.doa_percentiles_deg[1] - 45) <= 2
    assert front.doa_within_pct == 100.0
    silent = score.front_level(pcm[:, 0], pcm[:, 1], [], None, ("hpf", "vad"), doa_label_deg=45)
    assert silent.doa_hops == 0 and silent.doa_within_pct is None
