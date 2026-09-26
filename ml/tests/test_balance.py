"""balance recovers a mismatch built by construction and keeps only what every placement agrees on (E2-T6)."""

from __future__ import annotations

import numpy as np
import pytest

from srpipe.dsp.afe import balance
from srpipe.generated import grid
from srpipe.metrics import mic_pair

FS = grid.SAMPLE_RATE_HZ
LEAD = 0.2
PHASE0_DEG = 5.0


def burst(seconds: float = 6.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    quiet = 1e-5 * rng.standard_normal(FS)
    return np.concatenate([quiet, 0.1 * rng.standard_normal(round(seconds * FS)), quiet])


def response(freqs_hz: np.ndarray) -> np.ndarray:
    """A smooth ch1 over ch0 level, about +11 dB like board B, rising 3 dB towards 3 kHz."""
    return 3.5 * (1.0 + 0.4 * np.exp(-(((freqs_hz - 3000.0) / 2400.0) ** 2)))


def through(x: np.ndarray, extra_phase_deg_above_1600: float = 0.0) -> np.ndarray:
    """x as ch1 hears it: the level of response, a lead of LEAD samples, PHASE0_DEG, and a placement phase."""
    spectrum = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1.0 / FS)
    phase = 2 * np.pi * freqs / FS * LEAD + np.radians(PHASE0_DEG)
    phase = phase + np.where(freqs > 1600.0, np.radians(extra_phase_deg_above_1600), 0.0)
    shaped = spectrum * response(freqs) * np.exp(1j * phase)
    shaped[0] = shaped[0].real
    return np.fft.irfft(shaped, n=len(x))


def stats(x0: np.ndarray, x1: np.ndarray) -> mic_pair.PairStats:
    return mic_pair.pair_stats(x0, x1, frame_samples=grid.FFT_SIZE)


def test_a_built_mismatch_is_inverted() -> None:
    x0 = burst()
    est = balance.estimate([stats(x0, through(x0))])
    freqs = np.arange(grid.N_BINS) * FS / grid.FFT_SIZE
    inner = slice(8, grid.N_BINS - 8)
    assert (est.tau_samples, est.phase0_deg) == pytest.approx((LEAD, PHASE0_DEG), abs=0.02)
    level_db = 20 * np.log10(np.abs(est.gains))
    assert level_db[inner] == pytest.approx(-20 * np.log10(response(freqs[inner])), abs=0.1)
    expected_phase = -(np.radians(PHASE0_DEG) + 2 * np.pi * np.arange(grid.N_BINS) / grid.FFT_SIZE * LEAD)
    assert np.angle(est.gains[1:-1] * np.exp(-1j * expected_phase[1:-1])) == pytest.approx(0.0, abs=np.radians(0.5))
    assert est.gains.dtype == np.complex64
    assert est.gains[0].imag == 0 and est.gains[-1].imag == 0


def test_placements_that_disagree_on_high_phase_leave_the_level_exact() -> None:
    x0, y0 = burst(seed=1), burst(seed=2)
    front, rear = stats(x0, through(x0, +40.0)), stats(y0, through(y0, -10.0))
    est = balance.estimate([front, rear])
    for session in (front, rear):
        bands = mic_pair.pair_bands(balance.compensated(session, est.gains))
        assert all(abs(b.level_diff_db) < 0.1 for b in bands[1:])
        assert all(abs(b.phase_diff_deg) < 1.0 for b in bands[2:5])


def test_an_estimate_from_one_placement_checks_out_on_the_other() -> None:
    x0, y0 = burst(seed=3), burst(seed=4)
    est = balance.estimate([stats(x0, through(x0, +40.0))])
    check = mic_pair.pair_bands(balance.compensated(stats(y0, through(y0, -10.0)), est.gains))
    assert max(abs(b.level_diff_db) for b in check[1:]) < 0.1


def test_apply_multiplies_bin_by_bin_in_complex64() -> None:
    rng = np.random.default_rng(5)
    bins = (rng.standard_normal(grid.N_BINS) + 1j * rng.standard_normal(grid.N_BINS)).astype(np.complex64)
    gains = (rng.standard_normal(grid.N_BINS) + 1j * rng.standard_normal(grid.N_BINS)).astype(np.complex64)
    out = balance.apply(bins, gains)
    assert out.dtype == np.complex64
    assert out == pytest.approx(bins * gains, rel=1e-6)


def test_spectra_off_the_firmware_grid_are_refused() -> None:
    x0 = burst(seconds=2.0)
    with pytest.raises(ValueError, match=str(grid.FFT_SIZE)):
        balance.estimate([mic_pair.pair_stats(x0, through(x0), frame_samples=1024)])


def test_a_room_notch_in_one_bin_barely_moves_the_smoothed_level() -> None:
    num = np.ones(grid.N_BINS)
    den = np.ones(grid.N_BINS)
    den[150] = 1e-3
    ratio_db = 10 * np.log10(balance.smoothed_ratio(num, den))
    assert ratio_db[150] < 0.3
    assert ratio_db[:100] == pytest.approx(np.zeros(100))
