"""Log-mel filterbank and MFCC of dsp_spec/mel.h: Slaney mel scale, area-normalised triangles (KEHOACH 3.11)."""

from __future__ import annotations

from dataclasses import dataclass

import numba
import numpy as np

from srpipe.generated import grid

MAX_BANDS = 80
_SLANEY_LINEAR_HZ_PER_MEL = 200.0 / 3.0
_SLANEY_KNEE_HZ = 1000.0
_SLANEY_KNEE_MEL = _SLANEY_KNEE_HZ / _SLANEY_LINEAR_HZ_PER_MEL
_SLANEY_LOG_STEP = np.log(6.4) / 27.0


@dataclass(frozen=True)
class MelConfig:
    """Mirror of dsp_spec_mel_config_t; the values come from the feature config of a model, never from here."""

    n_bands: int
    f_min_hz: float
    f_max_hz: float
    log_floor: float


def hz_to_mel(hz: np.ndarray | float) -> np.ndarray:
    """Slaney mel: linear below 1 kHz at 200/3 Hz per mel, logarithmic above."""
    hz = np.asarray(hz, dtype=np.float64)
    linear = hz / _SLANEY_LINEAR_HZ_PER_MEL
    log = _SLANEY_KNEE_MEL + np.log(np.maximum(hz, _SLANEY_KNEE_HZ) / _SLANEY_KNEE_HZ) / _SLANEY_LOG_STEP
    return np.where(hz < _SLANEY_KNEE_HZ, linear, log)


def mel_to_hz(mel: np.ndarray | float) -> np.ndarray:
    """Inverse of hz_to_mel."""
    mel = np.asarray(mel, dtype=np.float64)
    linear = mel * _SLANEY_LINEAR_HZ_PER_MEL
    log = _SLANEY_KNEE_HZ * np.exp(_SLANEY_LOG_STEP * (np.maximum(mel, _SLANEY_KNEE_MEL) - _SLANEY_KNEE_MEL))
    return np.where(mel < _SLANEY_KNEE_MEL, linear, log)


def check_config(cfg: MelConfig) -> None:
    """Refuse what dsp_spec_mel_workspace_bytes refuses."""
    nyquist = grid.SAMPLE_RATE_HZ / 2
    if not 1 <= cfg.n_bands <= MAX_BANDS:
        raise ValueError(f"n_bands {cfg.n_bands} outside 1..{MAX_BANDS}")
    if not 0.0 <= cfg.f_min_hz < cfg.f_max_hz <= nyquist:
        raise ValueError(f"band edges {cfg.f_min_hz}..{cfg.f_max_hz} Hz not inside 0..{nyquist} Hz")
    if not cfg.log_floor > 0.0:
        raise ValueError("log_floor must be positive")


def filterbank(cfg: MelConfig) -> np.ndarray:
    """(n_bands, N_BINS) float32 triangles on the grid's bins, each scaled by 2 / its width in Hz.

    Built in float64 once, like the C init, then stored as float32.
    """
    check_config(cfg)
    bin_hz = np.arange(grid.N_BINS, dtype=np.float64) * grid.SAMPLE_RATE_HZ / grid.FFT_SIZE
    edges = mel_to_hz(np.linspace(hz_to_mel(cfg.f_min_hz), hz_to_mel(cfg.f_max_hz), cfg.n_bands + 2))
    lower, centre, upper = edges[:-2, None], edges[1:-1, None], edges[2:, None]
    rising = (bin_hz - lower) / (centre - lower)
    falling = (upper - bin_hz) / (upper - centre)
    triangles = np.maximum(0.0, np.minimum(rising, falling))
    return (triangles * (2.0 / (upper - lower))).astype(np.float32)


def dct_matrix(n_bands: int) -> np.ndarray:
    """Orthonormal DCT-II as an (n_bands, n_bands) float32 matrix, row k being coefficient k."""
    k = np.arange(n_bands, dtype=np.float64)[:, None]
    n = np.arange(n_bands, dtype=np.float64)[None, :]
    basis = np.sqrt(2.0 / n_bands) * np.cos(np.pi * k * (2.0 * n + 1.0) / (2.0 * n_bands))
    basis[0] /= np.sqrt(2.0)
    return basis.astype(np.float32)


@numba.njit
def _band_energy(filters: np.ndarray, power: np.ndarray) -> np.ndarray:
    """Each band's filter times the power, summed bin by bin in float32 as mel.c sums."""
    out = np.empty(filters.shape[0], dtype=np.float32)
    for b in range(filters.shape[0]):
        acc = np.float32(0.0)
        for k in range(filters.shape[1]):
            acc += filters[b, k] * power[k]
        out[b] = acc
    return out


class Mel:
    """Filters and DCT built once, as dsp_spec_mel_init does; every frame is float32 arithmetic."""

    def __init__(self, cfg: MelConfig) -> None:
        self.cfg = cfg
        self.filters = filterbank(cfg)
        self._dct = dct_matrix(cfg.n_bands)

    def log(self, bins: np.ndarray) -> np.ndarray:
        """Natural log of each band's power, plus log_floor inside the log, from N_BINS complex bins."""
        bins = np.asarray(bins, dtype=np.complex64)
        if bins.shape != (grid.N_BINS,):
            raise ValueError(f"want {grid.N_BINS} bins, got shape {bins.shape}")
        power = bins.real * bins.real + bins.imag * bins.imag
        # Summed bin by bin, as mel.c sums; a BLAS product splits the sum its own way on each CPU.
        energy = _band_energy(self.filters, power)
        floored = (energy + np.float32(self.cfg.log_floor)).astype(np.float64)
        return np.log(floored).astype(np.float32)

    def mfcc(self, log_mel: np.ndarray, n_ceps: int) -> np.ndarray:
        """First n_ceps orthonormal DCT-II coefficients of n_bands log energies."""
        if not 1 <= n_ceps <= self.cfg.n_bands:
            raise ValueError(f"n_ceps {n_ceps} outside 1..{self.cfg.n_bands}")
        terms = self._dct[:n_ceps] * np.asarray(log_mel, dtype=np.float32)
        return np.cumsum(terms, axis=-1, dtype=np.float32)[:, -1]
