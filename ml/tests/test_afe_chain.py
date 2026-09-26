"""Check srpipe.dsp.afe.chain, the facade with every module off, against what the plain chain must do."""

from __future__ import annotations

import numpy as np
import pytest

from srpipe.dsp.afe import chain
from srpipe.generated import array, grid

HOPS = 32
AMPLITUDE = 12000.0
TONE_HZ = 440.0
MAX_ERROR_LSB = 1


def tone(n_samples: int) -> np.ndarray:
    t = np.arange(n_samples) / grid.SAMPLE_RATE_HZ
    return np.rint(AMPLITUDE * np.sin(2 * np.pi * TONE_HZ * t)).astype(np.int16)


def interleave(*channels: np.ndarray) -> np.ndarray:
    return np.stack(channels, axis=-1).reshape(-1, grid.HOP_SAMPLES * len(channels))


def run(ch: chain.Chain, hops: np.ndarray) -> list[chain.Frame]:
    return [ch.process(h) for h in hops]


def test_identical_microphones_come_back_one_hop_late() -> None:
    x = tone(HOPS * grid.HOP_SAMPLES)
    frames = run(chain.Chain("MM"), interleave(x, x))
    out = np.concatenate([f.pcm for f in frames])
    assert np.max(np.abs(out[grid.HOP_SAMPLES :].astype(int) - x[: -grid.HOP_SAMPLES])) <= MAX_ERROR_LSB
    assert [f.seq for f in frames] == list(range(HOPS))
    assert all(f.flags == 0 and f.doa_deg == -1 and f.vad == 0 and f.gain_db == 0 for f in frames)


def test_opposite_microphones_cancel_in_the_mean() -> None:
    x = tone(HOPS * grid.HOP_SAMPLES)
    frames = run(chain.Chain("MM"), interleave(x, -x))
    assert max(int(np.max(np.abs(f.pcm.astype(int)))) for f in frames) <= MAX_ERROR_LSB


def test_reset_marks_only_the_next_hop_and_seq_runs_on() -> None:
    ch = chain.Chain("MM")
    silence = np.zeros(grid.HOP_SAMPLES * array.N_MICS, dtype=np.int16)
    first = ch.process(silence)
    ch.reset()
    after, later = ch.process(silence), ch.process(silence)
    assert (first.flags, after.flags, later.flags) == (0, chain.FLAG_GAP, 0)
    assert (after.seq, later.seq) == (1, 2)


def test_full_scale_on_either_microphone_flags_a_clip() -> None:
    ch = chain.Chain("MM")
    hop = np.zeros(grid.HOP_SAMPLES * array.N_MICS, dtype=np.int16)
    hop[1] = np.iinfo(np.int16).min
    assert ch.process(hop).flags == chain.FLAG_CLIPPED
    assert ch.process(np.zeros_like(hop)).flags == 0


def test_level_is_int8_dbfs_with_silence_at_the_floor() -> None:
    assert chain.level_dbfs(np.zeros(grid.HOP_SAMPLES, dtype=np.float32)) == chain.LEVEL_MIN_DBFS
    assert chain.level_dbfs(np.full(grid.HOP_SAMPLES, 0.1, dtype=np.float32)) == -20
    assert chain.level_dbfs(np.full(grid.HOP_SAMPLES, 1.0, dtype=np.float32)) == 0


def test_to_pcm_rounds_half_to_even_and_saturates() -> None:
    hop = np.array([0.5, 1.5, -2.5, 40000.0, -40000.0], dtype=np.float32) / chain.PCM_FULL_SCALE
    assert chain.to_pcm(hop).tolist() == [0, 2, -2, 32767, -32768]


def test_formats_the_default_build_cannot_run_are_refused() -> None:
    with pytest.raises(ValueError):
        chain.Chain("XY")
    with pytest.raises(NotImplementedError):
        chain.Chain("MMR")
