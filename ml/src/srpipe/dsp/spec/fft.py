"""Real FFT pair of dsp_spec/fft.h: unscaled forward, 1 / n inverse, float32 throughout (KEHOACH 3.14)."""

from __future__ import annotations

import numpy as np

MIN_POINTS = 64
MAX_POINTS = 2048


def check_points(n_points: int) -> None:
    """Refuse the lengths dsp_spec_fft_workspace_bytes refuses: a power of two from 64 to 2048."""
    if not (MIN_POINTS <= n_points <= MAX_POINTS) or n_points & (n_points - 1):
        raise ValueError(f"{n_points} points is not a power of two in [{MIN_POINTS}, {MAX_POINTS}]")


def forward(x: np.ndarray) -> np.ndarray:
    """Spectrum of n real samples into n / 2 + 1 complex64 bins, DC first, unscaled."""
    x = np.asarray(x, dtype=np.float32)
    check_points(x.shape[-1])
    return np.fft.rfft(x).astype(np.complex64)


def inverse(bins: np.ndarray, n_points: int) -> np.ndarray:
    """Real float32 signal of n_points from n_points / 2 + 1 bins, scaled by 1 / n_points.

    The imaginary parts of the DC and Nyquist bins are ignored, as in dsp_spec_fft_inverse.
    """
    check_points(n_points)
    bins = np.array(bins, dtype=np.complex64)
    if bins.shape[-1] != n_points // 2 + 1:
        raise ValueError(f"{bins.shape[-1]} bins do not describe {n_points} points")
    bins[..., 0] = bins[..., 0].real
    bins[..., -1] = bins[..., -1].real
    return np.fft.irfft(bins, n_points).astype(np.float32)
