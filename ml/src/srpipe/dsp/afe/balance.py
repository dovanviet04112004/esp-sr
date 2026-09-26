"""balance of dsp_afe: one complex gain per bin on ch1 after the STFT, and its estimate (KEHOACH 3.4).

apply is the reference the firmware module of E7-T2 must match, in float32. estimate runs on the host from
frontal white noise sessions: magnitude per bin from pooled auto spectra, phase from the pooled phase line.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np

from srpipe.generated import array, grid
from srpipe.metrics import mic_pair

SMOOTH_BINS = 5
# Twice the nominal reach, as host/score searches, so a wrong spacing still shows as a delay.
MAX_LAG_SAMPLES = 2.0 * array.MAX_DELAY_SAMPLES


@dataclass(frozen=True)
class Balance:
    """gains[k] multiplies ch1 bin k; tau_samples and phase0_deg are the phase line they carry."""

    gains: np.ndarray
    tau_samples: float
    phase0_deg: float
    active_frames: int


def smooth_db(level_db: np.ndarray, width: int = SMOOTH_BINS) -> np.ndarray:
    """Moving average over width bins; the ends average over the bins that exist."""
    kernel = np.ones(width)
    total = np.convolve(level_db, kernel, mode="same")
    count = np.convolve(np.ones_like(level_db), kernel, mode="same")
    return total / count


def estimate(sessions: Sequence[mic_pair.PairStats]) -> Balance:
    """Gains that make ch1 match ch0 over the pooled sessions, each measured on the firmware grid."""
    if any(s.frame_samples != grid.FFT_SIZE for s in sessions):
        raise ValueError(f"balance needs spectra of {grid.FFT_SIZE} points, the firmware grid")
    stats = mic_pair.pooled(sessions)
    stats.need_source()
    if np.any(stats.s00 <= 0) or np.any(stats.s11 <= 0):
        raise ValueError("a bin carries no energy on one channel")
    magnitude = 10.0 ** (smooth_db(10.0 * np.log10(stats.s00 / stats.s11)) / 20.0)
    start = mic_pair.gcc_phat_delay(stats, MAX_LAG_SAMPLES).tau_samples
    tau, phase0_deg = mic_pair.linear_phase_fit(stats, start)
    omega = 2.0 * np.pi * np.arange(grid.N_BINS) / grid.FFT_SIZE
    phase = -(np.radians(phase0_deg) + omega * tau)
    phase[0] = phase[-1] = 0.0
    gains = (magnitude * np.exp(1j * phase)).astype(np.complex64)
    return Balance(gains, tau, phase0_deg, stats.active_frames)


def apply(bins_ch1: np.ndarray, gains: np.ndarray) -> np.ndarray:
    """ch1 spectrum times the gains, bin by bin, in complex64 as the firmware computes it."""
    return (np.asarray(bins_ch1, dtype=np.complex64) * np.asarray(gains, dtype=np.complex64)).astype(np.complex64)


def compensated(stats: mic_pair.PairStats, gains: np.ndarray) -> mic_pair.PairStats:
    """The sums a session would have had with ch1 through the gains, for checking an estimate on it."""
    g = np.asarray(gains, dtype=np.complex128)
    if stats.frame_samples != grid.FFT_SIZE or g.shape != stats.s11.shape:
        raise ValueError(f"gains and spectra must both have {grid.N_BINS} bins")
    power = np.abs(g) ** 2
    return replace(stats, s11=stats.s11 * power, s10=stats.s10 * g, q11=stats.q11 * power)
