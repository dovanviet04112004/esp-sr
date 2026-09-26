"""srpipe.metrics.mic_pair against delays, gains and phases set by construction (E2-T4, E2-T7)."""

from __future__ import annotations

import numpy as np
import pytest

from srpipe.generated import array, grid
from srpipe.metrics import mic_pair

FS = grid.SAMPLE_RATE_HZ


def burst(seconds: float = 3.0, seed: int = 0) -> np.ndarray:
    """One second of near silence, white noise for `seconds`, one second of near silence."""
    rng = np.random.default_rng(seed)
    quiet = 1e-5 * rng.standard_normal(FS)
    return np.concatenate([quiet, 0.1 * rng.standard_normal(round(seconds * FS)), quiet])


def shifted(x: np.ndarray, lead_samples: float, gain: float = 1.0, phase_deg: float = 0.0) -> np.ndarray:
    """x advanced by lead_samples (fractional, circular), scaled and given a constant phase at every frequency."""
    spectrum = np.fft.rfft(x)
    omega = 2 * np.pi * np.fft.rfftfreq(len(x))
    spectrum = spectrum * gain * np.exp(1j * (omega * lead_samples + np.radians(phase_deg)))
    spectrum[0] = spectrum[0].real
    return np.fft.irfft(spectrum, n=len(x))


@pytest.mark.parametrize("lead", [1.7, -2.3, 0.0])
def test_gcc_phat_finds_a_fractional_delay_with_the_sign_of_the_contract(lead: float) -> None:
    x0 = burst()
    x1 = shifted(x0, lead)
    delay = mic_pair.gcc_phat_delay(mic_pair.pair_stats(x0, x1), max_lag_samples=4.0)
    assert delay.tau_samples == pytest.approx(lead, abs=0.05)
    assert delay.peak > 0.9
    assert delay.frames > 0


def test_the_spacing_follows_from_the_endfire_delay() -> None:
    tau = mic_pair.expected_tau_samples(0.0)
    assert tau == pytest.approx(array.MAX_DELAY_SAMPLES, rel=1e-6)
    assert mic_pair.spacing_from_tau_m(tau, 0.0) == pytest.approx(array.SPACING_M)
    assert mic_pair.spacing_from_tau_m(-tau, 180.0) == pytest.approx(array.SPACING_M)


def test_level_and_phase_per_band_after_removing_the_delay() -> None:
    x0 = burst()
    x1 = shifted(x0, 1.5, gain=0.5, phase_deg=20.0)
    frames = mic_pair.pair_stats(x0, x1)
    tau, phase0 = mic_pair.linear_phase_fit(frames)
    assert (tau, phase0) == pytest.approx((1.5, 20.0), abs=0.02)
    bands = mic_pair.pair_bands(frames, tau)
    assert len(bands) == len(mic_pair.PAIR_BANDS_HZ)
    for band in bands:
        assert band.level_diff_db == pytest.approx(-6.02, abs=0.05)
        assert band.coherence == pytest.approx(1.0, abs=5e-3)
    assert all(b.phase_after_delay_deg == pytest.approx(20.0, abs=1.0) for b in bands[1:])
    assert bands[4].phase_diff_deg == pytest.approx(20.0 + np.degrees(2 * np.pi * 1200 / FS * 1.5), abs=15.0)


def test_a_one_khz_sine_has_its_power_with_or_without_a_weighting() -> None:
    t = np.arange(4 * FS) / FS
    tone = 0.1 * np.sin(2 * np.pi * 1000 * t)
    frames = mic_pair.pair_stats(tone, tone)
    assert mic_pair.noise_floor_dbfs(frames, 0, weighted=False) == pytest.approx(-23.01, abs=0.05)
    assert mic_pair.noise_floor_dbfs(frames, 1, weighted=True) == pytest.approx(-23.01, abs=0.1)


def test_the_floor_ignores_frames_with_the_source() -> None:
    x = burst()
    floor = mic_pair.noise_floor_dbfs(mic_pair.pair_stats(x, x), 0, weighted=False)
    assert floor == pytest.approx(10 * np.log10(1e-10), abs=1.0)


def test_a_weighting_matches_the_iec_table() -> None:
    assert mic_pair.a_weighting_db(np.array([100.0, 1000.0, 4000.0])) == pytest.approx([-19.1, 0.0, 1.0], abs=0.1)


def test_silence_alone_has_no_source_to_measure() -> None:
    quiet = 1e-5 * np.random.default_rng(1).standard_normal(3 * FS)
    frames = mic_pair.pair_stats(quiet, quiet)
    with pytest.raises(ValueError, match="source"):
        mic_pair.gcc_phat_delay(frames, max_lag_samples=4.0)


def test_chunking_leaves_every_sum_unchanged() -> None:
    x0 = burst(seconds=2.0, seed=3)
    x1 = shifted(x0, 0.7, gain=0.8)
    whole = mic_pair.pair_stats(x0, x1)
    pieces = mic_pair.pair_stats(x0, x1, chunk_frames=7)
    assert (pieces.active_frames, pieces.quiet_frames) == (whole.active_frames, whole.quiet_frames)
    for name in ("s00", "s11", "s10", "q00", "q11"):
        assert np.allclose(getattr(pieces, name), getattr(whole, name), rtol=1e-12, atol=0)
