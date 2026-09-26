"""Streaming STFT and weighted overlap-add on the grid of contracts/grid.yaml; mirror of dsp_spec/stft.h."""

from __future__ import annotations

import numpy as np

from srpipe.dsp.spec import fft
from srpipe.dsp.spec.window import sqrt_hann
from srpipe.generated import grid


def _hop(samples: np.ndarray) -> np.ndarray:
    samples = np.asarray(samples, dtype=np.float32)
    if samples.shape != (grid.HOP_SAMPLES,):
        raise ValueError(f"a hop is {grid.HOP_SAMPLES} samples, got shape {samples.shape}")
    return samples


class Stft:
    """One hop in, the N_BINS bins of the window that ends with it out; starts from silence."""

    def __init__(self) -> None:
        self._window = sqrt_hann(grid.FFT_SIZE)
        self._buffer = np.zeros(grid.FFT_SIZE, dtype=np.float32)

    def reset(self) -> None:
        """Forget buffered samples, as after a gap in the frame sequence."""
        self._buffer[:] = 0.0

    def analyze(self, hop: np.ndarray) -> np.ndarray:
        """Push HOP_SAMPLES samples and return the complex64 bins of the latest window."""
        self._buffer[: -grid.HOP_SAMPLES] = self._buffer[grid.HOP_SAMPLES :]
        self._buffer[-grid.HOP_SAMPLES :] = _hop(hop)
        return fft.forward(self._buffer * self._window)


class Istft:
    """One spectrum in, the next HOP_SAMPLES output samples out; output lags input by one hop."""

    def __init__(self) -> None:
        self._window = sqrt_hann(grid.FFT_SIZE)
        self._overlap = np.zeros(grid.FFT_SIZE, dtype=np.float32)

    def reset(self) -> None:
        """Clear the overlap buffer."""
        self._overlap[:] = 0.0

    def synthesize(self, bins: np.ndarray) -> np.ndarray:
        """Take N_BINS bins and return HOP_SAMPLES finished samples."""
        self._overlap += fft.inverse(bins, grid.FFT_SIZE) * self._window
        out = self._overlap[: grid.HOP_SAMPLES].copy()
        self._overlap[: -grid.HOP_SAMPLES] = self._overlap[grid.HOP_SAMPLES :]
        self._overlap[-grid.HOP_SAMPLES :] = 0.0
        return out


def analyze_signal(x: np.ndarray) -> np.ndarray:
    """Run a whole signal, a multiple of HOP_SAMPLES long, through one Stft: (hops, N_BINS) complex64."""
    x = np.asarray(x, dtype=np.float32)
    if x.ndim != 1 or x.size % grid.HOP_SAMPLES:
        raise ValueError(f"signal of shape {x.shape} is not a whole number of hops")
    st = Stft()
    return np.stack([st.analyze(h) for h in x.reshape(-1, grid.HOP_SAMPLES)])


def synthesize_signal(spectra: np.ndarray) -> np.ndarray:
    """Run (hops, N_BINS) spectra through one Istft and concatenate the hops it returns."""
    ist = Istft()
    return np.concatenate([ist.synthesize(s) for s in np.asarray(spectra, dtype=np.complex64)])
