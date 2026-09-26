"""balance of dsp_afe: one complex gain per bin on ch1 after the STFT, and its estimate (KEHOACH 3.4).

apply is the reference the firmware module of E7-T2 must match, in float32. estimate runs on the host from
frontal white noise sessions: magnitude from pooled auto spectra over 1/3 octave, phase from the pooled phase line.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np

from srpipe.generated import array, grid
from srpipe.metrics import mic_pair

SMOOTH_MIN_BINS = 5
SMOOTH_OCTAVE_FRACTION = 3
# Twice the nominal reach, as host/score searches, so a wrong spacing still shows as a delay.
MAX_LAG_SAMPLES = 2.0 * array.MAX_DELAY_SAMPLES


@dataclass(frozen=True)
class Balance:
    """gains[k] multiplies ch1 bin k; tau_samples and phase0_deg are the phase line they carry."""

    gains: np.ndarray
    tau_samples: float
    phase0_deg: float
    active_frames: int


def smoothed_ratio(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    """Per bin, sum(num) / sum(den) over a 1/3-octave window around it, never fewer than SMOOTH_MIN_BINS bins.

    Summing power before dividing keeps a notch of the room in one channel from dominating its window.
    """
    k = np.arange(len(num), dtype=np.float64)
    half_width = k * (2.0 ** (0.5 / SMOOTH_OCTAVE_FRACTION) - 1.0)
    reach = np.maximum(np.ceil(half_width), SMOOTH_MIN_BINS // 2).astype(int)
    lo = np.clip(np.arange(len(num)) - reach, 0, len(num) - 1)
    hi = np.clip(np.arange(len(num)) + reach, 0, len(num) - 1)
    num_sum = np.concatenate([[0.0], np.cumsum(num)])
    den_sum = np.concatenate([[0.0], np.cumsum(den)])
    return (num_sum[hi + 1] - num_sum[lo]) / (den_sum[hi + 1] - den_sum[lo])


def estimate(sessions: Sequence[mic_pair.PairStats]) -> Balance:
    """Gains that make ch1 match ch0 over the pooled sessions, each measured on the firmware grid."""
    if any(s.frame_samples != grid.FFT_SIZE for s in sessions):
        raise ValueError(f"balance needs spectra of {grid.FFT_SIZE} points, the firmware grid")
    stats = mic_pair.pooled(sessions)
    stats.need_source()
    if np.any(stats.s00 <= 0) or np.any(stats.s11 <= 0):
        raise ValueError("a bin carries no energy on one channel")
    magnitude = np.sqrt(smoothed_ratio(stats.s00, stats.s11))
    start = mic_pair.gcc_phat_delay(stats, MAX_LAG_SAMPLES).tau_samples
    tau, phase0_deg = mic_pair.linear_phase_fit(stats, start)
    omega = 2.0 * np.pi * np.arange(grid.N_BINS) / grid.FFT_SIZE
    phase = -(np.radians(phase0_deg) + omega * tau)
    phase[0] = phase[-1] = 0.0
    gains = (magnitude * np.exp(1j * phase)).astype(np.complex64)
    return Balance(gains, tau, phase0_deg, stats.active_frames)


def apply(bins_ch1: np.ndarray, gains: np.ndarray) -> np.ndarray:
    """ch1 spectrum times the gains, bin by bin, over any leading axes of hops.

    Four float32 products and two float32 sums per bin, each rounded once, in the order balance.c takes them;
    a complex64 product would leave the rounding to whatever SIMD path numpy picks.
    """
    x = np.asarray(bins_ch1, dtype=np.complex64)
    g = np.asarray(gains, dtype=np.complex64)
    if x.shape[-1] != grid.N_BINS or g.shape != (grid.N_BINS,):
        raise ValueError(f"bins and gains must end in {grid.N_BINS} bins")
    out = np.empty(x.shape, dtype=np.complex64)
    out.real = g.real * x.real - g.imag * x.imag
    out.imag = g.real * x.imag + g.imag * x.real
    return out


def compensated(stats: mic_pair.PairStats, gains: np.ndarray) -> mic_pair.PairStats:
    """The sums a session would have had with ch1 through the gains, for checking an estimate on it."""
    g = np.asarray(gains, dtype=np.complex128)
    if stats.frame_samples != grid.FFT_SIZE or g.shape != stats.s11.shape:
        raise ValueError(f"gains and spectra must both have {grid.N_BINS} bins")
    power = np.abs(g) ** 2
    return replace(stats, s11=stats.s11 * power, s10=stats.s10 * g, q11=stats.q11 * power)
