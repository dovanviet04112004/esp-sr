"""Two-microphone figures of an array measurement: delay, level and phase difference, noise floor (KEHOACH 2.3, 4.6).

Signals are int16-scaled or float; levels are dB against a full-scale square wave, as level_dbfs of the
firmware. A full-scale sine, the 0 dBFS of the INMP441 datasheet, sits 3.01 dB lower on this scale.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from srpipe.generated import array, grid

FRAME_SAMPLES = 1024
FLOOR_PERCENTILE = 10.0
ACTIVE_ABOVE_FLOOR_DB = 10.0
QUIET_WITHIN_FLOOR_DB = 3.0
DELAY_BAND_HZ = (200.0, 7000.0)
UPSAMPLE = 32
FIT_COHERENCE_MIN = 0.5
FIT_COHERENCE_CAP = 0.999999
FIT_ROUNDS = 2
PAIR_BANDS_HZ = ((50, 100), (100, 200), (200, 400), (400, 800), (800, 1600), (1600, 3200), (3200, 6400), (6400, 8000))
PCM_FULL_SCALE = 32768.0
CHUNK_FRAMES = 2048


@dataclass(frozen=True)
class PairStats:
    """Hann-windowed spectra summed over frames, each frame's mean removed: auto and cross over active frames
    (the source), auto over quiet frames (the floor). Frames in between, such as those overlapping the edge of
    a clap, count as neither, so the floor stays clean.
    """

    s00: np.ndarray
    s11: np.ndarray
    s10: np.ndarray
    q00: np.ndarray
    q11: np.ndarray
    active_frames: int
    quiet_frames: int
    freqs_hz: np.ndarray

    @property
    def frame_samples(self) -> int:
        return 2 * (len(self.freqs_hz) - 1)

    def need_source(self) -> None:
        if self.active_frames == 0:
            raise ValueError("no frame rises above the floor: is a source playing?")

    def coherence(self) -> np.ndarray:
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.abs(self.s10) ** 2 / (self.s00 * self.s11)


@dataclass(frozen=True)
class PairDelay:
    """tau_samples = t0 - t1, positive when sound reaches ch1 first; peak is the PHAT peak, 1 for a clean delay."""

    tau_samples: float
    peak: float
    frames: int

    @property
    def tau_s(self) -> float:
        return self.tau_samples / grid.SAMPLE_RATE_HZ


@dataclass(frozen=True)
class BandFigures:
    """ch1 against ch0 in one band; phase_after_delay_deg removes the measured pure delay first."""

    low_hz: float
    high_hz: float
    level_diff_db: float
    phase_diff_deg: float
    phase_after_delay_deg: float
    coherence: float


def full_scale(x: np.ndarray) -> float:
    return PCM_FULL_SCALE if x.dtype == np.int16 else 1.0


def frame_rows(x: np.ndarray, first: int, count: int, frame_samples: int) -> np.ndarray:
    hop = frame_samples // 2
    index = np.arange(frame_samples)[None, :] + hop * np.arange(first, first + count)[:, None]
    rows = x[index].astype(np.float64) / full_scale(x)
    return (rows - rows.mean(axis=1, keepdims=True)) * np.hanning(frame_samples)


def pair_stats(
    x0: np.ndarray, x1: np.ndarray, frame_samples: int = FRAME_SAMPLES, chunk_frames: int = CHUNK_FRAMES
) -> PairStats:
    """Two passes over half-overlapped frames, chunk by chunk: frame energies set the floor (FLOOR_PERCENTILE),
    then spectra are summed into the active and quiet sets; memory does not grow with the recording.
    """
    a, b = np.asarray(x0), np.asarray(x1)
    if a.ndim != 1 or a.shape != b.shape:
        raise ValueError(f"need two 1-D channels of one length, got {a.shape} and {b.shape}")
    n_frames = (len(a) - frame_samples) // (frame_samples // 2) + 1
    if n_frames < 1:
        raise ValueError(f"need at least {frame_samples} samples, got {len(a)}")
    chunks = [(first, min(chunk_frames, n_frames - first)) for first in range(0, n_frames, chunk_frames)]
    energy = np.concatenate(
        [
            np.sum(frame_rows(a, f, c, frame_samples) ** 2 + frame_rows(b, f, c, frame_samples) ** 2, axis=1)
            for f, c in chunks
        ]
    )
    floor = np.percentile(energy, FLOOR_PERCENTILE)
    active = energy > floor * 10.0 ** (ACTIVE_ABOVE_FLOOR_DB / 10.0)
    quiet = energy <= floor * 10.0 ** (QUIET_WITHIN_FLOOR_DB / 10.0)
    n_bins = frame_samples // 2 + 1
    s00, s11, q00, q11 = (np.zeros(n_bins) for _ in range(4))
    s10 = np.zeros(n_bins, dtype=np.complex128)
    for first, count in chunks:
        f0 = np.fft.rfft(frame_rows(a, first, count, frame_samples), axis=1)
        f1 = np.fft.rfft(frame_rows(b, first, count, frame_samples), axis=1)
        on, off = active[first : first + count], quiet[first : first + count]
        s00 += np.sum(np.abs(f0[on]) ** 2, axis=0)
        s11 += np.sum(np.abs(f1[on]) ** 2, axis=0)
        s10 += np.sum(f1[on] * np.conj(f0[on]), axis=0)
        q00 += np.sum(np.abs(f0[off]) ** 2, axis=0)
        q11 += np.sum(np.abs(f1[off]) ** 2, axis=0)
    freqs = np.fft.rfftfreq(frame_samples, 1.0 / grid.SAMPLE_RATE_HZ)
    return PairStats(s00, s11, s10, q00, q11, int(active.sum()), int(quiet.sum()), freqs)


def pooled(parts: Sequence[PairStats]) -> PairStats:
    """Several recordings on one frame grid summed as if they were one, e.g. two loudspeaker placements."""
    if not parts:
        raise ValueError("nothing to pool")
    grid_freqs = parts[0].freqs_hz
    if any(p.freqs_hz.shape != grid_freqs.shape for p in parts):
        raise ValueError("recordings on different frame sizes cannot be pooled")
    return PairStats(
        s00=sum(p.s00 for p in parts),
        s11=sum(p.s11 for p in parts),
        s10=sum(p.s10 for p in parts),
        q00=sum(p.q00 for p in parts),
        q11=sum(p.q11 for p in parts),
        active_frames=sum(p.active_frames for p in parts),
        quiet_frames=sum(p.quiet_frames for p in parts),
        freqs_hz=grid_freqs,
    )


def gcc_phat_delay(stats: PairStats, max_lag_samples: float, band_hz: tuple[float, float] = DELAY_BAND_HZ) -> PairDelay:
    """Delay from the PHAT-weighted cross-spectrum summed over active frames, searched within +-max_lag_samples."""
    stats.need_source()
    cross = np.conj(stats.s10)
    in_band = (stats.freqs_hz >= band_hz[0]) & (stats.freqs_hz <= band_hz[1])
    magnitude = np.abs(cross)
    weighted = np.where(in_band & (magnitude > 0), cross / np.where(magnitude > 0, magnitude, 1.0), 0.0)
    n_fft = stats.frame_samples
    corr = np.fft.irfft(weighted, n=n_fft * UPSAMPLE) * UPSAMPLE * n_fft / max(int(in_band.sum()), 1) / 2
    reach = int(np.ceil(max_lag_samples * UPSAMPLE))
    lags = np.arange(-reach, reach + 1)
    values = corr[lags % len(corr)]
    best = int(np.argmax(values))
    offset = 0.0
    if 0 < best < len(values) - 1:
        left, mid, right = values[best - 1], values[best], values[best + 1]
        curvature = left - 2 * mid + right
        offset = 0.5 * (left - right) / curvature if curvature != 0 else 0.0
    return PairDelay((lags[best] + offset) / UPSAMPLE, float(values[best]), stats.active_frames)


def linear_phase_fit(
    stats: PairStats, start_tau_samples: float = 0.0, band_hz: tuple[float, float] = (200.0, array.ALIAS_HZ)
) -> tuple[float, float]:
    """Pure delay and constant phase that best explain the cross-spectrum phase below spatial aliasing.

    Returns (tau_samples, phase0_deg) of phase(f) = phase0 + 2 pi f tau / fs. The phase is turned back by
    start_tau_samples first so the residual never wraps, then fitted on coherent bins with weights 1/variance.
    """
    stats.need_source()
    coherence = stats.coherence()
    pick = (stats.freqs_hz >= band_hz[0]) & (stats.freqs_hz <= band_hz[1]) & (coherence >= FIT_COHERENCE_MIN)
    if pick.sum() < 2:
        raise ValueError(f"fewer than two bins with coherence >= {FIT_COHERENCE_MIN} in {band_hz} Hz")
    omega = 2.0 * np.pi * stats.freqs_hz[pick] / grid.SAMPLE_RATE_HZ
    gamma2 = np.minimum(coherence[pick], FIT_COHERENCE_CAP)
    weight = np.sqrt(gamma2 / (1.0 - gamma2))
    design = np.column_stack([np.ones_like(omega), omega]) * weight[:, None]
    tau, phase0 = start_tau_samples, 0.0
    for _ in range(FIT_ROUNDS):
        residual = np.angle(stats.s10[pick] * np.exp(-1j * omega * tau))
        (phase0, step), *_ = np.linalg.lstsq(design, residual * weight, rcond=None)
        tau += step
    return float(tau), float(np.degrees(phase0))


def pair_bands(
    stats: PairStats, tau_samples: float = 0.0, bands_hz: tuple[tuple[float, float], ...] = PAIR_BANDS_HZ
) -> list[BandFigures]:
    """Level and phase of ch1 against ch0 per band, from spectra summed over active frames."""
    stats.need_source()
    s00, s11, s10 = stats.s00, stats.s11, stats.s10
    omega = 2.0 * np.pi * stats.freqs_hz / grid.SAMPLE_RATE_HZ
    s10_aligned = s10 * np.exp(-1j * omega * tau_samples)
    coherence = stats.coherence()
    out = []
    for low, high in bands_hz:
        pick = (stats.freqs_hz >= low) & (stats.freqs_hz < high)
        if not pick.any():
            continue
        out.append(
            BandFigures(
                low_hz=float(low),
                high_hz=float(high),
                level_diff_db=float(10.0 * np.log10(s11[pick].sum() / s00[pick].sum())),
                phase_diff_deg=float(np.degrees(np.angle(s10[pick].sum()))),
                phase_after_delay_deg=float(np.degrees(np.angle(s10_aligned[pick].sum()))),
                coherence=float(np.nanmean(coherence[pick])),
            )
        )
    return out


def expected_tau_samples(doa_deg: float) -> float:
    """tau of a far source at doa_deg for the spacing of contracts/array.yaml (0 deg is the ch1 side)."""
    return array.SPACING_M * np.cos(np.radians(doa_deg)) / array.SPEED_OF_SOUND_M_S * grid.SAMPLE_RATE_HZ


def spacing_from_tau_m(tau_samples: float, doa_deg: float) -> float:
    """Spacing that explains a measured tau for a source at doa_deg; only well posed near the ends of the array."""
    return tau_samples / grid.SAMPLE_RATE_HZ * array.SPEED_OF_SOUND_M_S / np.cos(np.radians(doa_deg))


def a_weighting_db(freqs_hz: np.ndarray) -> np.ndarray:
    """IEC 61672-1 A-weighting in dB, 0 dB at 1 kHz; -inf at 0 Hz."""
    f2 = np.asarray(freqs_hz, dtype=np.float64) ** 2
    num = 12194.0**2 * f2**2
    den = (f2 + 20.6**2) * np.sqrt((f2 + 107.7**2) * (f2 + 737.9**2)) * (f2 + 12194.0**2)
    with np.errstate(divide="ignore"):
        return 20.0 * np.log10(num / den) + 2.00


def noise_floor_dbfs(stats: PairStats, channel: int, weighted: bool = True) -> float:
    """Level of one channel over the quiet frames, DC left out, A-weighted by default."""
    frame_samples = stats.frame_samples
    power = (stats.q00, stats.q11)[channel] / max(stats.quiet_frames, 1)
    power[1:-1] *= 2.0
    power[0] = 0.0
    gain = 10.0 ** (a_weighting_db(stats.freqs_hz) / 10.0) if weighted else np.ones_like(power)
    mean_square = float(np.sum(power * gain)) / (frame_samples * np.sum(np.hanning(frame_samples) ** 2))
    return 10.0 * np.log10(mean_square) if mean_square > 0 else float("-inf")
