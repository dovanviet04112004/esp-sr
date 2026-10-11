"""Front end of the speaker network ReDimNet2 b0, mirror of dsp_spec/spk_fbank.h (KEHOACH 3.17).

TFMelBanks of ReDimNet at the pin: the window normalised and pre-emphasised, framed by a symmetric Hamming, the radix-4
FFT of dsp_spec, TensorFlow's mel weights, the module's log, each band's mean over time taken off. Every step is float32
in the C's order but the window's mean and variance, summed in double, so the C matches bit for bit (KEHOACH 3.14).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numba
import numpy as np

from srpipe.dsp.spec import fft
from srpipe.dsp.spec.mel import ln_f32
from srpipe.generated import grid, speaker

MAX_BANDS = 128
PCM_FULL_SCALE = np.float32(32768.0)
HAMMING_ALPHA = 0.54  # scipy's general_hamming, the rest 1 - alpha
HTK_MEL_SCALE = 2595.0
HTK_MEL_BREAK_HZ = 700.0


@dataclass(frozen=True)
class SpkFbankConfig:
    """Mirror of dsp_spec_spk_fbank_config_t; the values come from contracts/speaker.yaml, never from here."""

    window_samples: int
    frame_samples: int
    hop_samples: int
    fft_size: int
    n_bands: int
    f_min_hz: float
    f_max_hz: float
    preemphasis: float
    eps: float


def contract_config() -> SpkFbankConfig:
    """The front end of speaker.yaml over its window."""
    return SpkFbankConfig(window_samples=speaker.WINDOW_SAMPLES, **speaker.FEATURES)


def n_frames(cfg: SpkFbankConfig) -> int:
    """Frames of a window padded by half a hop at both ends, as TFMelBanks's conv1d strides it."""
    padded = cfg.window_samples + 2 * (cfg.hop_samples // 2)
    return 1 + (padded - cfg.frame_samples) // cfg.hop_samples


def check_config(cfg: SpkFbankConfig) -> None:
    """Refuse what dsp_spec_spk_fbank_workspace_bytes refuses."""
    fft.check_points(cfg.fft_size)
    nyquist = grid.SAMPLE_RATE_HZ / 2
    if not 2 <= cfg.frame_samples <= cfg.fft_size or cfg.hop_samples < 1:
        raise ValueError(f"frame {cfg.frame_samples}, hop {cfg.hop_samples} for an FFT of {cfg.fft_size}")
    if cfg.window_samples < 2 or cfg.window_samples + 2 * (cfg.hop_samples // 2) < cfg.frame_samples:
        raise ValueError(f"a window of {cfg.window_samples} samples holds no frame")
    if not 1 <= cfg.n_bands <= MAX_BANDS:
        raise ValueError(f"n_bands {cfg.n_bands} outside 1..{MAX_BANDS}")
    if not 0.0 <= cfg.f_min_hz < cfg.f_max_hz <= nyquist:
        raise ValueError(f"band edges {cfg.f_min_hz}..{cfg.f_max_hz} Hz not inside 0..{nyquist} Hz")
    if not 0.0 <= cfg.preemphasis < 1.0 or not cfg.eps > 0.0:
        raise ValueError(f"preemphasis {cfg.preemphasis} or eps {cfg.eps} out of range")


def linspace(start: float, stop: float, num: int) -> list[float]:
    """numpy.linspace in double: i times the step plus start, the last point exactly stop."""
    step = (stop - start) / (num - 1)
    return [i * step + start for i in range(num - 1)] + [stop]


def hamming(n: int) -> np.ndarray:
    """Symmetric Hamming of n points as scipy builds it, alpha plus (1 - alpha) cos of a linspace from -pi to pi, each
    in double rounded once to float32."""
    rest = 1.0 - HAMMING_ALPHA
    return np.array([HAMMING_ALPHA + rest * math.cos(a) for a in linspace(-math.pi, math.pi, n)], dtype=np.float32)


def hz_to_htk_mel(hz: float) -> float:
    """2595 log10(1 + hz / 700), in double."""
    return HTK_MEL_SCALE * math.log10(1 + hz / HTK_MEL_BREAK_HZ)


def tf_mel_weights(cfg: SpkFbankConfig, tf_spacing: bool = True) -> np.ndarray:
    """(fft_size / 2, n_bands) float32 triangles of ReDimNet's get_filterbanks, built in double once like the C init.
    Bin k sits at the mel of k times nyquist / (fft_size / 2 - 1) Hz, TensorFlow's spacing, not at its own
    frequency; bin 0 weighs nothing. tf_spacing off puts each bin at its own frequency, a negative control."""
    check_config(cfg)
    n_bins = cfg.fft_size // 2
    points = linspace(hz_to_htk_mel(cfg.f_min_hz), hz_to_htk_mel(cfg.f_max_hz), cfg.n_bands + 2)
    hz = linspace(0.0, float(grid.SAMPLE_RATE_HZ // 2), n_bins)
    if not tf_spacing:
        hz = [k * grid.SAMPLE_RATE_HZ / cfg.fft_size for k in range(n_bins)]
    bins_mel = [hz_to_htk_mel(f) for f in hz]
    weights = np.zeros((n_bins, cfg.n_bands), dtype=np.float64)
    for k in range(1, n_bins):
        for b in range(cfg.n_bands):
            lower, centre, upper = points[b], points[b + 1], points[b + 2]
            rising = (bins_mel[k] - lower) / (centre - lower)
            falling = (upper - bins_mel[k]) / (upper - centre)
            weights[k, b] = max(0.0, min(rising, falling))
    return weights.astype(np.float32)


@numba.njit
def _moments(x: np.ndarray) -> tuple[float, float]:
    """Mean and biased variance of float32 samples, summed one by one in double."""
    total = 0.0
    for v in x:
        total += np.float64(v)
    mean = total / len(x)
    squares = 0.0
    for v in x:
        d = np.float64(v) - mean
        squares += d * d
    return mean, squares / len(x)


@numba.njit
def _emphasised(x: np.ndarray, mean: np.float32, denom: np.float32, coef: np.float32) -> np.ndarray:
    """Each sample less the mean over denom, then less coef times the one before; the first reads the second."""
    n = len(x)
    normal = np.empty(n, dtype=np.float32)
    for i in range(n):
        normal[i] = (x[i] - mean) / denom
    out = np.empty(n, dtype=np.float32)
    out[0] = normal[0] - coef * normal[1]
    for i in range(1, n):
        out[i] = normal[i] - coef * normal[i - 1]
    return out


@numba.njit
def _frames(y: np.ndarray, window: np.ndarray, hop: int, count: int, fft_size: int) -> np.ndarray:
    """Windowed frames padded with zeros, half a hop ahead of the first sample and past the last."""
    out = np.zeros((count, fft_size), dtype=np.float32)
    for t in range(count):
        start = t * hop - hop // 2
        for n in range(len(window)):
            j = start + n
            if 0 <= j < len(y):
                out[t, n] = y[j] * window[n]
    return out


@numba.njit
def _bands(power: np.ndarray, weights: np.ndarray, lo: np.float32, hi: np.float32) -> np.ndarray:
    """Each band's weights times the power, summed bin by bin in float32 as spk_fbank.c sums, then clipped."""
    count, n_bins = power.shape
    out = np.empty((count, weights.shape[1]), dtype=np.float32)
    for t in range(count):
        for b in range(weights.shape[1]):
            acc = np.float32(0.0)
            for k in range(n_bins):
                acc += weights[k, b] * power[t, k]
            out[t, b] = min(max(acc, lo), hi)
    return out


@numba.njit
def _centred(log_bands: np.ndarray) -> np.ndarray:
    """Each band less its mean over the frames, the sum in float32 frame by frame."""
    count, n_bands = log_bands.shape
    out = np.empty_like(log_bands)
    for b in range(n_bands):
        total = np.float32(0.0)
        for t in range(count):
            total += log_bands[t, b]
        mean = total / np.float32(count)
        for t in range(count):
            out[t, b] = log_bands[t, b] - mean
    return out


class SpkFbank:
    """Tables built once, as dsp_spec_spk_fbank_init builds them; every window is float32 arithmetic."""

    def __init__(self, cfg: SpkFbankConfig) -> None:
        check_config(cfg)
        self.cfg = cfg
        self.n_frames = n_frames(cfg)
        self.window = hamming(cfg.frame_samples)
        self.weights = tf_mel_weights(cfg)

    def log_bands(self, pcm: np.ndarray) -> np.ndarray:
        """(n_frames, n_bands) log band energies of window_samples int16 clean samples, ahead of the time mean."""
        pcm = np.asarray(pcm)
        if pcm.dtype != np.int16 or pcm.shape != (self.cfg.window_samples,):
            raise ValueError(f"want {self.cfg.window_samples} int16 samples, got {pcm.dtype} {pcm.shape}")
        eps = np.float32(self.cfg.eps)
        lo, hi = eps, np.float32(1.0) / eps
        x = pcm.astype(np.float32) / PCM_FULL_SCALE
        mean, variance = _moments(x)
        denom = np.sqrt(np.float32(variance)) + eps
        y = _emphasised(x, np.float32(mean), denom, np.float32(self.cfg.preemphasis))
        frames = _frames(y, self.window, self.cfg.hop_samples, self.n_frames, self.cfg.fft_size)
        bins = fft.forward(frames)[:, : self.cfg.fft_size // 2]
        power = np.clip(bins.real * bins.real + bins.imag * bins.imag, lo, hi)
        return ln_f32(_bands(np.ascontiguousarray(power), self.weights, lo, hi) + eps)

    def run(self, pcm: np.ndarray) -> np.ndarray:
        """(n_frames, n_bands) features of window_samples int16 clean samples, the network's input frame by frame."""
        return _centred(self.log_bands(pcm))


def tail(samples: np.ndarray, n_samples: int) -> np.ndarray:
    """The last n_samples int16 samples of a window, zeros ahead of a shorter one, as svc_listen hands them over."""
    last = np.asarray(samples, dtype=np.int16)[-n_samples:]
    return np.pad(last, (n_samples - len(last), 0))
