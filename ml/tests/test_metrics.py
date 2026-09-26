"""Every measure of srpipe.metrics against a value known in advance (TASKS E4-T5)."""

from __future__ import annotations

import numpy as np
import pytest

from srpipe.generated import grid
from srpipe.metrics.det import det_curve, miss_rate_at
from srpipe.metrics.doa_err import ANGLE_UNKNOWN_DEG, doa_score
from srpipe.metrics.erle import erle_db, erle_track_db
from srpipe.metrics.pesq import PESQ_WB_MAX, pesq_wb
from srpipe.metrics.sisdr import si_sdr_db, si_sdr_improvement_db
from srpipe.metrics.stoi import stoi_score

FS = grid.SAMPLE_RATE_HZ


def voiced(seconds: float = 4.0, seed: int = 0) -> np.ndarray:
    """A gliding 120 Hz harmonic tone in two syllables per second: speech enough for PESQ and STOI."""
    t = np.arange(round(seconds * FS)) / FS
    phase = 2 * np.pi * np.cumsum(120 + 20 * np.sin(2 * np.pi * 0.5 * t)) / FS
    tone = sum(np.sin(k * phase) / k for k in range(1, 30))
    envelope = np.clip(np.sin(2 * np.pi * 2.0 * t), 0, None) ** 2
    return 0.3 * tone * envelope + 1e-4 * np.random.default_rng(seed).standard_normal(t.size)


def orthogonal_noise(reference: np.ndarray, power_ratio: float, seed: int = 1) -> np.ndarray:
    """Noise with no component along reference whose power is power_ratio times the reference power."""
    ref = reference - reference.mean()
    noise = np.random.default_rng(seed).standard_normal(ref.size)
    noise -= noise.mean()
    noise -= (noise @ ref) / (ref @ ref) * ref
    return noise * np.sqrt(power_ratio * (ref @ ref) / (noise @ noise))


def test_si_sdr_matches_the_power_ratio_of_orthogonal_noise() -> None:
    ref = voiced()
    est = 3.0 * ref + orthogonal_noise(3.0 * ref, 0.01)
    assert si_sdr_db(est, ref) == pytest.approx(20.0, abs=1e-6)


def test_si_sdr_ignores_scale_and_is_infinite_for_a_copy() -> None:
    ref = voiced()
    assert si_sdr_db(0.25 * ref, ref) == float("inf")


def test_si_sdr_improvement_is_the_difference_of_the_two_scores() -> None:
    ref = voiced()
    mixture = ref + orthogonal_noise(ref, 1.0)
    estimate = ref + orthogonal_noise(ref, 0.1, seed=2)
    assert si_sdr_improvement_db(estimate, mixture, ref) == pytest.approx(10.0, abs=1e-6)


def test_si_sdr_refuses_a_silent_reference_or_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="silent"):
        si_sdr_db(np.ones(10), np.zeros(10))
    with pytest.raises(ValueError, match="one length"):
        si_sdr_db(np.ones(10), np.ones(11))


def test_stoi_of_a_copy_is_one_and_noise_lowers_it() -> None:
    ref = voiced()
    noisy = ref + 0.05 * np.random.default_rng(3).standard_normal(ref.size)
    assert stoi_score(ref, ref) == pytest.approx(1.0, abs=1e-6)
    assert stoi_score(noisy, ref) < stoi_score(ref, ref)


def test_pesq_of_a_copy_reaches_the_wideband_ceiling_and_noise_lowers_it() -> None:
    ref = voiced()
    noisy = ref + 0.05 * np.random.default_rng(4).standard_normal(ref.size)
    assert pesq_wb(ref, ref) == pytest.approx(PESQ_WB_MAX, abs=1e-3)
    assert pesq_wb(noisy, ref) < 2.0


def test_erle_is_twenty_db_when_the_residual_is_a_tenth_of_the_echo() -> None:
    mic = voiced()
    assert erle_db(0.1 * mic, mic) == pytest.approx(20.0, abs=1e-9)


def test_erle_track_follows_a_canceller_that_converges_and_skips_silence() -> None:
    mic = np.random.default_rng(5).standard_normal(FS)
    residual = mic * np.repeat([1.0, 0.1, 0.01], [FS // 2, FS // 4, FS - FS // 2 - FS // 4])
    mic[-1600:] = 0.0
    track = erle_track_db(residual, mic)
    assert track.size == 10
    assert track[:5] == pytest.approx(np.zeros(5), abs=1e-9)
    assert track[5:7] == pytest.approx([20.0, 20.0], abs=1e-9)
    assert np.isnan(track[-1])


def test_doa_score_counts_missing_estimates_against_the_share_only() -> None:
    true = np.array([90, 90, 90, 90, 20])
    est = np.array([92, 85, 110, ANGLE_UNKNOWN_DEG, 25])
    score = doa_score(est, true)
    assert (score.frames, score.estimated) == (5, 4)
    assert score.mean_abs_error_deg == pytest.approx((2 + 5 + 20 + 5) / 4)
    assert score.within_pct == pytest.approx(60.0)


def test_doa_score_restricted_to_the_endfire_band() -> None:
    true = np.array([90, 90, 10, 20])
    est = np.array([90, 90, 40, 20])
    score = doa_score(est, true, true_range_deg=(0, 30))
    assert (score.frames, score.mean_abs_error_deg, score.within_pct) == (2, 15.0, 50.0)


def test_det_curve_at_hand_counted_thresholds() -> None:
    curve = det_curve([0.9, 0.8, 0.4], [0.85, 0.3], negative_hours=2.0)
    pairs = zip(curve.miss_rate, curve.false_alarms_per_hour, strict=True)
    points = dict(zip(curve.thresholds.tolist(), pairs, strict=True))
    assert points[0.3] == pytest.approx((0.0, 1.0))
    assert points[0.8] == pytest.approx((1 / 3, 0.5))
    assert points[0.9] == pytest.approx((2 / 3, 0.0))
    assert points[float("inf")] == pytest.approx((1.0, 0.0))


def test_miss_rate_at_a_false_alarm_budget_takes_the_lowest_allowed_threshold() -> None:
    curve = det_curve([0.9, 0.8, 0.4], [0.85, 0.3], negative_hours=2.0)
    assert miss_rate_at(curve, 0.5) == pytest.approx((0.0, 0.4))
    assert miss_rate_at(curve, 0.0) == pytest.approx((2 / 3, 0.9))
