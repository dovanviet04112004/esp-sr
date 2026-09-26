"""hpf mirrors the esp-dsp biquad the firmware calls and does what a Butterworth high-pass must (E7-T1)."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import freqz, lfilter

from srpipe.dsp.afe.hpf import Hpf, coefficients
from srpipe.generated import afe, grid

FS = grid.SAMPLE_RATE_HZ
LSB = 1.0 / 32768.0


def exact(cutoff_hz: float) -> tuple[np.ndarray, np.ndarray]:
    """The RBJ Butterworth high-pass in float64, the reference the float32 mirror is judged against."""
    w0 = 2 * np.pi * cutoff_hz / FS
    alpha = np.sin(w0) / (2 / np.sqrt(2))
    b = np.array([(1 + np.cos(w0)) / 2, -(1 + np.cos(w0)), (1 + np.cos(w0)) / 2]) / (1 + alpha)
    return b, np.array([1.0, -2 * np.cos(w0) / (1 + alpha), (1 - alpha) / (1 + alpha)])


def test_the_default_cutoff_comes_from_the_contract() -> None:
    assert Hpf().coef == pytest.approx(coefficients(afe.HPF_CUTOFF_HZ))


def test_coefficients_are_the_rbj_design_rounded_to_float32() -> None:
    b, a = exact(80.0)
    assert coefficients(80.0).dtype == np.float32
    assert coefficients(80.0) == pytest.approx(np.r_[b, a[1:]], abs=2e-7)


@pytest.mark.parametrize("cutoff_hz", [5.0, 5000.0])
def test_a_cutoff_outside_the_supported_range_is_refused(cutoff_hz: float) -> None:
    with pytest.raises(ValueError, match="cutoff"):
        coefficients(cutoff_hz)


@pytest.mark.parametrize(("freq_hz", "expected_db"), [(80.0, -3.01), (100.0, None), (200.0, None), (1000.0, None)])
def test_a_tone_comes_out_at_the_level_of_the_design(freq_hz: float, expected_db: float | None) -> None:
    b, a = exact(80.0)
    _, h = freqz(b, a, worN=[2 * np.pi * freq_hz / FS])
    design_db = 20 * np.log10(abs(h[0]))
    t = np.arange(2 * FS) / FS
    y = Hpf(80.0, 1).process(0, 0.5 * np.sin(2 * np.pi * freq_hz * t))
    measured_db = 20 * np.log10(np.sqrt(2 * np.mean(y[FS:] ** 2)) / 0.5)
    assert measured_db == pytest.approx(design_db, abs=0.01)
    if expected_db is not None:
        assert design_db == pytest.approx(expected_db, abs=0.02)


def test_dc_is_removed() -> None:
    y = Hpf(80.0, 1).process(0, np.full(2 * FS, -0.5 * LSB, dtype=np.float32))
    assert 20 * np.log10(abs(y[FS:].mean()) + 1e-30) < -140


def test_hops_in_a_row_equal_one_long_call() -> None:
    x = np.random.default_rng(0).standard_normal(8 * grid.HOP_SAMPLES).astype(np.float32) * 0.1
    whole = Hpf(80.0, 1).process(0, x)
    per_hop = Hpf(80.0, 1)
    hops = [per_hop.process(0, x[k : k + grid.HOP_SAMPLES]) for k in range(0, len(x), grid.HOP_SAMPLES)]
    assert np.array_equal(np.concatenate(hops), whole)


def test_channels_keep_separate_state() -> None:
    x = np.random.default_rng(1).standard_normal(grid.HOP_SAMPLES).astype(np.float32) * 0.1
    two = Hpf(80.0, 2)
    two.process(0, np.ones(grid.HOP_SAMPLES, dtype=np.float32))
    assert np.array_equal(two.process(1, x), Hpf(80.0, 1).process(0, x))


def test_float32_error_stays_under_one_lsb_on_speech_with_a_dc_offset() -> None:
    t = np.arange(2 * FS) / FS
    rng = np.random.default_rng(2)
    x = 0.03 * np.sin(2 * np.pi * 150 * t) * np.clip(np.sin(2 * np.pi * 2 * t), 0, None) - 0.5 * LSB
    x = x + 1e-4 * rng.standard_normal(t.size)
    b, a = exact(80.0)
    y = Hpf(80.0, 1).process(0, x).astype(np.float64)
    assert np.abs(y - lfilter(b, a, x)).max() < LSB
