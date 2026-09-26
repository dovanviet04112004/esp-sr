"""Check srpipe.dsp.spec against its own contracts and against float64 references (KEHOACH 3.1, 3.11)."""

from __future__ import annotations

import numpy as np
import pytest
import scipy.fft

from srpipe.dsp.spec import fft, mel, stft
from srpipe.dsp.spec.window import sqrt_hann
from srpipe.generated import grid

RNG = np.random.default_rng(20260926)
# float32 carries 24 bits; one forward and one inverse transform keep well over 100 dB of that.
MIN_ROUND_TRIP_SNR_DB = 110.0
FEATURE = mel.MelConfig(n_bands=40, f_min_hz=20.0, f_max_hz=7600.0, log_floor=1e-6)


def snr_db(reference: np.ndarray, test: np.ndarray) -> float:
    noise = np.sum((reference.astype(np.float64) - test) ** 2)
    return float(10.0 * np.log10(np.sum(reference.astype(np.float64) ** 2) / max(noise, 1e-300)))


def test_squared_window_overlap_adds_to_one_at_half_length() -> None:
    w = sqrt_hann(grid.FFT_SIZE)
    half = grid.FFT_SIZE // 2
    assert w.dtype == np.float32 and w[0] == 0.0
    assert np.max(np.abs(w[:half] ** 2 + w[half:] ** 2 - 1.0)) < 1e-6


def test_forward_matches_a_float64_transform_and_stays_float32() -> None:
    x = RNG.uniform(-1.0, 1.0, grid.FFT_SIZE).astype(np.float32)
    got = fft.forward(x)
    want = np.fft.rfft(x.astype(np.float64))
    assert got.dtype == np.complex64 and got.shape == (grid.N_BINS,)
    assert np.max(np.abs(got - want)) / np.max(np.abs(want)) < 1e-5


def test_inverse_undoes_forward_and_ignores_dc_and_nyquist_imaginary_parts() -> None:
    x = RNG.uniform(-1.0, 1.0, grid.FFT_SIZE).astype(np.float32)
    bins = fft.forward(x)
    bins[0] += 5j
    bins[-1] -= 3j
    back = fft.inverse(bins, grid.FFT_SIZE)
    assert back.dtype == np.float32
    assert np.max(np.abs(back - x)) < 1e-6


@pytest.mark.parametrize("n", [32, 100, 4096])
def test_lengths_the_c_side_refuses_are_refused(n: int) -> None:
    with pytest.raises(ValueError):
        fft.forward(np.zeros(n, dtype=np.float32))


def signals() -> dict[str, np.ndarray]:
    n = grid.HOP_SAMPLES * 64
    t = np.arange(n) / grid.SAMPLE_RATE_HZ
    clicks = np.zeros(n)
    clicks[:: grid.SAMPLE_RATE_HZ // 120] = 0.9
    return {
        "white noise": RNG.uniform(-0.5, 0.5, n),
        "440 Hz tone": 0.5 * np.sin(2 * np.pi * 440.0 * t),
        "chirp 50 Hz to 7.9 kHz": 0.5 * np.sin(2 * np.pi * (50.0 * t + (7900.0 - 50.0) / 2.0 * t**2)),
        "120 Hz click train": clicks,
    }


@pytest.mark.parametrize("name", list(signals()))
def test_analysis_then_synthesis_returns_the_input_one_hop_late(name: str) -> None:
    x = signals()[name].astype(np.float32)
    y = stft.synthesize_signal(stft.analyze_signal(x))
    hop = grid.HOP_SAMPLES
    assert y.dtype == np.float32 and y.shape == x.shape
    assert np.max(np.abs(y[:hop])) < 1e-6
    snr = snr_db(x[:-hop], y[hop:])
    print(f"MEASURE stft round trip, {name}: {snr:.1f} dB, max error {np.max(np.abs(x[:-hop] - y[hop:])):.2e}")
    assert snr > MIN_ROUND_TRIP_SNR_DB


def test_reset_brings_back_the_state_of_a_fresh_analyser() -> None:
    hops = RNG.uniform(-1.0, 1.0, (4, grid.HOP_SAMPLES)).astype(np.float32)
    used = stft.Stft()
    for h in hops:
        used.analyze(h)
    used.reset()
    np.testing.assert_array_equal(used.analyze(hops[0]), stft.Stft().analyze(hops[0]))


def test_slaney_scale_is_linear_to_1_khz_and_invertible() -> None:
    assert float(mel.hz_to_mel(1000.0)) == pytest.approx(15.0)
    assert float(mel.hz_to_mel(500.0)) == pytest.approx(7.5)
    hz = np.linspace(0.0, 8000.0, 97)
    np.testing.assert_allclose(mel.mel_to_hz(mel.hz_to_mel(hz)), hz, atol=1e-9)


def test_filters_are_nonnegative_triangles_inside_the_band_with_unit_area() -> None:
    fb = mel.filterbank(FEATURE)
    bin_hz = np.arange(grid.N_BINS) * grid.SAMPLE_RATE_HZ / grid.FFT_SIZE
    assert fb.dtype == np.float32 and fb.shape == (FEATURE.n_bands, grid.N_BINS)
    assert np.all(fb >= 0.0)
    assert np.all(fb[:, (bin_hz < FEATURE.f_min_hz) | (bin_hz > FEATURE.f_max_hz)] == 0.0)
    peaks = bin_hz[np.argmax(fb, axis=1)]
    assert np.all(np.diff(peaks) >= 0.0)
    wide = np.count_nonzero(fb, axis=1) >= 4
    area = fb[wide].sum(axis=1) * grid.SAMPLE_RATE_HZ / grid.FFT_SIZE
    np.testing.assert_allclose(area, 1.0, rtol=0.1)


def test_log_mel_takes_the_natural_log_of_band_power_plus_the_floor() -> None:
    m = mel.Mel(FEATURE)
    silence = m.log(np.zeros(grid.N_BINS, dtype=np.complex64))
    np.testing.assert_allclose(silence, np.log(np.float32(FEATURE.log_floor)), rtol=1e-6)
    bins = fft.forward(RNG.uniform(-1.0, 1.0, grid.FFT_SIZE).astype(np.float32))
    power = np.abs(bins.astype(np.complex128)) ** 2
    want = np.log(m.filters.astype(np.float64) @ power + FEATURE.log_floor)
    got = m.log(bins)
    assert got.dtype == np.float32
    np.testing.assert_allclose(got, want, atol=1e-5)


def test_mfcc_is_the_orthonormal_dct_ii() -> None:
    m = mel.Mel(FEATURE)
    log_mel = RNG.normal(size=FEATURE.n_bands).astype(np.float32)
    want = scipy.fft.dct(log_mel.astype(np.float64), type=2, norm="ortho")[:13]
    got = m.mfcc(log_mel, 13)
    assert got.dtype == np.float32
    np.testing.assert_allclose(got, want, atol=1e-5)


@pytest.mark.parametrize(
    "cfg",
    [
        mel.MelConfig(0, 20.0, 7600.0, 1e-6),
        mel.MelConfig(81, 20.0, 7600.0, 1e-6),
        mel.MelConfig(40, 7600.0, 20.0, 1e-6),
        mel.MelConfig(40, 20.0, 9000.0, 1e-6),
        mel.MelConfig(40, 20.0, 7600.0, 0.0),
    ],
)
def test_configurations_the_c_side_refuses_are_refused(cfg: mel.MelConfig) -> None:
    with pytest.raises(ValueError):
        mel.filterbank(cfg)
