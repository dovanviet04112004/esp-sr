"""Log-mel filterbank and MFCC of dsp_spec/mel.h: Slaney mel scale, area-normalised triangles (KEHOACH 3.11).

The log is the module's own float32 function, as mel.c computes it, so the C matches bit for bit (KEHOACH 3.14).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numba
import numpy as np

from srpipe.generated import grid

MAX_BANDS = 80
_SLANEY_LINEAR_HZ_PER_MEL = 200.0 / 3.0
_SLANEY_KNEE_HZ = 1000.0
_SLANEY_KNEE_MEL = _SLANEY_KNEE_HZ / _SLANEY_LINEAR_HZ_PER_MEL
_SLANEY_LOG_STEP = np.log(6.4) / 27.0
_LN_2 = np.float32(math.log(2.0))
_SQRT_HALF = np.float32(math.sqrt(0.5))
# ln((1 + t) / (1 - t)) = 2 atanh t: odd powers 1 .. 9, |t| <= 0.172 on [sqrt(1/2), sqrt(2)).
_LN_COEFFS = np.array([2.0 / k for k in (1, 3, 5, 7, 9)], dtype=np.float32)


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


def cos_table(n_bands: int) -> np.ndarray:
    """cos(pi m / (2 n_bands)) for m < 4 n_bands, one period of the DCT-II kernel, each in double rounded once."""
    return np.array([math.cos(math.pi * m / (2.0 * n_bands)) for m in range(4 * n_bands)], dtype=np.float32)


@numba.njit
def _mfcc(table: np.ndarray, log_mel: np.ndarray, n_ceps: int, first: np.float32, rest: np.float32) -> np.ndarray:
    n = len(log_mel)
    out = np.empty(n_ceps, dtype=np.float32)
    for k in range(n_ceps):
        acc = np.float32(0.0)
        for i in range(n):
            acc += table[(k * (2 * i + 1)) % (4 * n)] * log_mel[i]
        out[k] = (first if k == 0 else rest) * acc
    return out


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


@numba.njit
def _ln(x: np.ndarray) -> np.ndarray:
    out = np.empty(len(x), dtype=np.float32)
    one, two = np.float32(1.0), np.float32(2.0)
    for k in range(len(x)):
        m, exponent = math.frexp(x[k])
        mantissa = np.float32(m)
        if mantissa < _SQRT_HALF:
            mantissa = mantissa * two
            exponent -= 1
        t = (mantissa - one) / (mantissa + one)
        t2 = t * t
        c = _LN_COEFFS
        series = t * (c[0] + t2 * (c[1] + t2 * (c[2] + t2 * (c[3] + t2 * c[4]))))
        out[k] = np.float32(exponent) * _LN_2 + series
    return out


def ln_f32(x: np.ndarray) -> np.ndarray:
    """Natural log of positive normal float32 values as mel.c takes it: exponent and mantissa by frexp, the mantissa
    by an atanh series; error under 2e-6 absolute."""
    a = np.asarray(x, dtype=np.float32)
    return _ln(np.ascontiguousarray(a).reshape(-1)).reshape(a.shape)


class Mel:
    """Filters and DCT built once, as dsp_spec_mel_init does; every frame is float32 arithmetic."""

    def __init__(self, cfg: MelConfig) -> None:
        self.cfg = cfg
        self.filters = filterbank(cfg)
        self._cos = cos_table(cfg.n_bands)
        n = np.float32(cfg.n_bands)
        self._first, self._rest = np.sqrt(np.float32(1.0) / n), np.sqrt(np.float32(2.0) / n)

    def log(self, bins: np.ndarray) -> np.ndarray:
        """Natural log of each band's power, plus log_floor inside the log, from N_BINS complex bins."""
        bins = np.asarray(bins, dtype=np.complex64)
        if bins.shape != (grid.N_BINS,):
            raise ValueError(f"want {grid.N_BINS} bins, got shape {bins.shape}")
        power = bins.real * bins.real + bins.imag * bins.imag
        # Summed bin by bin, as mel.c sums; a BLAS product splits the sum its own way on each CPU.
        energy = _band_energy(self.filters, power)
        return _ln(energy + np.float32(self.cfg.log_floor))

    def mfcc(self, log_mel: np.ndarray, n_ceps: int) -> np.ndarray:
        """First n_ceps orthonormal DCT-II coefficients of n_bands log energies, summed and scaled as mel.c does."""
        if not 1 <= n_ceps <= self.cfg.n_bands:
            raise ValueError(f"n_ceps {n_ceps} outside 1..{self.cfg.n_bands}")
        x = np.ascontiguousarray(log_mel, dtype=np.float32)
        return _mfcc(self._cos, x, n_ceps, self._first, self._rest)
