"""dsp_spec/pitch mirror: the distance transform is the exact Viterbi minimum, tones of known F0 come back, a glide is
followed, silence stays finite, and the whole-signal function is the streaming tracker from a reset."""

from __future__ import annotations

import numpy as np
import pytest

from srpipe.core.config import CONFIGS, load_yaml
from srpipe.dsp.spec import pitch
from srpipe.generated import grid

RATE = grid.SAMPLE_RATE_HZ


def config() -> pitch.PitchConfig:
    return pitch.PitchConfig(**load_yaml(CONFIGS / "scenes" / "device.yaml")["pitch"])


def harmonics(f0_hz: np.ndarray, seconds: float, level: float = 0.3) -> np.ndarray:
    """Five harmonics of an F0 that may change sample by sample, like a voiced vowel."""
    n = int(seconds * RATE)
    phase = 2.0 * np.pi * np.cumsum(np.broadcast_to(f0_hz, (n,))) / RATE
    return (level * sum(np.sin(k * phase) / k for k in range(1, 6))).astype(np.float32)


def test_the_distance_transform_is_the_brute_force_minimum() -> None:
    rng = np.random.default_rng(20260930)
    factor = np.float32(np.log(1.005) ** 2 * 0.1)
    for trial in range(200):
        n = int(rng.integers(2, 60)) if trial % 2 else 417
        prev = (rng.standard_normal(n) * rng.choice([1e-3, 1.0, 30.0])).astype(np.float32)
        if trial % 7 == 0:
            prev = np.zeros(n, dtype=np.float32)
        values, best = pitch.distance_transform(prev, factor)
        d = np.arange(n)[:, None] - np.arange(n)[None, :]
        brute = ((d * d).astype(np.float32) * factor + prev[None, :]).astype(np.float32)
        assert np.all(values == brute[np.arange(n), best])
        np.testing.assert_allclose(values, brute.min(axis=1), rtol=1e-6, atol=1e-7)


def test_the_distance_transform_misses_the_minimum_when_a_parabola_is_dropped() -> None:
    prev = np.array([0.0, 5.0, 5.0, 0.0], dtype=np.float32)
    factor = np.float32(1.0)
    values, _ = pitch.distance_transform(prev, factor)
    truncated, _ = pitch.distance_transform(prev[:3], factor)
    assert values[3] == 0.0 and truncated[2] > 0.0


@pytest.mark.parametrize("f0_hz", [80.0, 120.0, 180.0, 260.0, 350.0])
def test_a_steady_tone_comes_back_at_its_f0_voiced(f0_hz: float) -> None:
    _, raw = pitch.pitch_features(harmonics(np.array(f0_hz), 1.5), config())
    settled = raw[pitch.LEAD_HOPS + 10 :]
    assert np.median(np.abs(settled[:, 1] / f0_hz - 1.0)) < 0.01
    assert np.min(settled[:, 0]) > 0.9


def test_a_glide_is_followed_and_its_delta_rises() -> None:
    n = int(2.0 * RATE)
    f0 = np.linspace(110.0, 220.0, n)
    feats, raw = pitch.pitch_features(harmonics(f0, 2.0), config())
    hops = np.arange(len(raw))
    frame_centre = ((hops - pitch.LEAD_HOPS) * grid.HOP_SAMPLES + 0.5 * (182 * 4)).astype(int)
    expected = f0[np.clip(frame_centre, 0, n - 1)]
    settled = slice(pitch.LEAD_HOPS + 10, len(raw) - 2)
    assert np.median(np.abs(raw[settled, 1] / expected[settled] - 1.0)) < 0.03
    assert np.median(feats[settled, 2]) > 0.0


def test_silence_stays_finite_and_unvoiced() -> None:
    feats, raw = pitch.pitch_features(np.zeros(RATE, dtype=np.float32), config())
    assert np.all(np.isfinite(feats)) and np.all(raw[pitch.LEAD_HOPS :, 0] == 0.0)
    # The weighted mean of equal log pitches differs from them only by float32 rounding.
    assert np.max(np.abs(feats[pitch.LEAD_HOPS :, 1:])) < 1e-5


def test_a_steady_tone_normalises_to_zero_and_starts_after_the_lead_hops() -> None:
    feats, _ = pitch.pitch_features(harmonics(np.array(150.0), 1.5), config())
    assert np.all(feats[: pitch.LEAD_HOPS] == 0.0) and np.any(feats[pitch.LEAD_HOPS] != 0.0)
    settled = feats[pitch.LEAD_HOPS + 50 :]
    assert np.max(np.abs(settled[:, 1])) < 0.02 and np.max(np.abs(settled[:, 2])) < 0.02


def test_the_whole_signal_function_is_the_streaming_tracker_and_reset_starts_afresh() -> None:
    cfg = config()
    rng = np.random.default_rng(7)
    x = (harmonics(np.array(140.0), 1.0) + 0.05 * rng.standard_normal(RATE)).astype(np.float32)
    feats, _ = pitch.pitch_features(x, cfg)
    tracker = pitch.PitchTracker(cfg)
    tracker.step(np.ones(grid.HOP_SAMPLES, dtype=np.float32))
    tracker.reset()
    stepped = np.array([tracker.step(x[h * grid.HOP_SAMPLES : (h + 1) * grid.HOP_SAMPLES]) for h in range(len(feats))])
    assert np.array_equal(stepped, feats)


def test_a_rate_that_does_not_divide_the_grid_is_refused() -> None:
    cfg = config()
    with pytest.raises(ValueError, match="resample_hz"):
        pitch.PitchTracker(pitch.PitchConfig(**{**cfg.__dict__, "resample_hz": 3000.0}))
