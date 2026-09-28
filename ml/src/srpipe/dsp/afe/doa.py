"""doa of dsp_afe: GCC-PHAT on the smoothed cross-spectrum, searched over an angle grid (KEHOACH 3.6).

Mirror of doa.c in float32: the band's cross-spectrum is smoothed every hop, the grid searched only when asked. Each
angle's phasor turns bin by bin and shares its pass with the mirror 180 - theta, whose delay is the opposite. Tables
come from double series of basic arithmetic and 1 / sqrt is ns_omlsa's Newton form: the C matches on any libm.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from srpipe.dsp.afe.ns_omlsa import PI, cos_series, exp_series, rsqrt_f32
from srpipe.generated import afe, array, grid

f32 = np.float32
ANGLE_UNKNOWN_DEG = -1
CONFIDENCE_MAX = 255
SIN_SERIES_TERMS = 30
# Below the smallest normal float the Newton 1 / sqrt has no valid guess; such a bin carries no phase anyway.
POWER_MIN = f32(np.finfo(np.float32).tiny)


def sin_series(x: float) -> float:
    """sin x for |x| <= 2 pi by its series, in double with basic arithmetic only."""
    term = x
    total = x
    for k in range(1, SIN_SERIES_TERMS + 1):
        term = term * -x * x / ((2 * k) * (2 * k + 1))
        total = total + term
    return total


@dataclass(frozen=True)
class DoaConfig:
    """Field for field as dsp_afe_doa_config_t; band_max_hz of the chain is the alias frequency of array.yaml."""

    spacing_m: float = array.SPACING_M
    speed_of_sound_m_s: float = array.SPEED_OF_SOUND_M_S
    band_min_hz: float = afe.DOA_BAND_MIN_HZ
    band_max_hz: float = afe.DOA_BAND_MAX_HZ if afe.DOA_BAND_MAX_HZ > 0 else array.ALIAS_HZ
    grid_step_deg: float = afe.DOA_GRID_STEP_DEG
    smooth_tau_s: float = afe.DOA_SMOOTH_TAU_S


@dataclass(frozen=True)
class DoaResult:
    angle_deg: int
    confidence: int  # 255 (1 - mean / peak) of R lifted to 0..1


def band_bins(cfg: DoaConfig) -> tuple[int, int]:
    """First and last bin of the band, DC left out: it carries no phase."""
    bin_hz = f32(grid.SAMPLE_RATE_HZ) / f32(grid.FFT_SIZE)
    first = max(1, int(np.ceil(f32(cfg.band_min_hz) / bin_hz)))
    last = min(grid.N_BINS - 1, int(np.floor(f32(cfg.band_max_hz) / bin_hz)))
    return first, last


def grid_steps(cfg: DoaConfig) -> int:
    """Steps of the grid over doa_range_deg; the step must divide the range, so every angle has its mirror."""
    span = f32(array.DOA_RANGE_DEG[1] - array.DOA_RANGE_DEG[0])
    step = f32(cfg.grid_step_deg)
    n = round(float(span / step)) if step > 0 else 0
    if n < 1 or f32(n) * step != span:
        raise ValueError(f"grid step {cfg.grid_step_deg} deg does not divide {float(span)} deg")
    return n


def check(cfg: DoaConfig) -> None:
    """The checks of doa.c config_ok, then a band holding at least one bin."""
    fields = [f32(v) for v in (cfg.spacing_m, cfg.speed_of_sound_m_s, cfg.band_min_hz, cfg.band_max_hz)]
    spacing, speed, low, high = fields
    ok = spacing > 0 and speed > 0 and low >= 0 and low < high <= f32(grid.SAMPLE_RATE_HZ) / f32(2.0)
    if not (ok and f32(cfg.grid_step_deg) > 0 and f32(cfg.smooth_tau_s) > 0):
        raise ValueError(f"bad doa configuration {cfg}")
    grid_steps(cfg)
    first, last = band_bins(cfg)
    if first > last:
        raise ValueError(f"doa band {cfg.band_min_hz} .. {cfg.band_max_hz} Hz holds no bin")


def phasors(cfg: DoaConfig) -> tuple[np.ndarray, np.ndarray]:
    """(start, step) per angle of the first half of the grid: exp(j w tau) at the band's first bin and at bin 1."""
    first, _ = band_bins(cfg)
    n_angles = grid_steps(cfg) + 1
    bin_hz = grid.SAMPLE_RATE_HZ / grid.FFT_SIZE
    step_deg, spacing, speed = (float(f32(v)) for v in (cfg.grid_step_deg, cfg.spacing_m, cfg.speed_of_sound_m_s))
    start = np.empty((n_angles + 1) // 2, dtype=np.complex64)
    step = np.empty_like(start)
    for i in range(len(start)):
        theta_rad = (array.DOA_RANGE_DEG[0] + i * step_deg) * PI / 180.0
        tau_s = spacing * cos_series(theta_rad) / speed
        per_bin = 2.0 * PI * bin_hz * tau_s
        at_first = per_bin * first
        step[i] = complex(f32(cos_series(per_bin)), f32(sin_series(per_bin)))
        start[i] = complex(f32(cos_series(at_first)), f32(sin_series(at_first)))
    return start, step


class Doa:
    """One searcher; starts with a zero cross-spectrum and no estimate."""

    def __init__(self, cfg: DoaConfig | None = None) -> None:
        self.cfg = cfg or DoaConfig()
        check(self.cfg)
        self.first, self.last = band_bins(self.cfg)
        self.n_angles = grid_steps(self.cfg) + 1
        self.angles_deg = [
            round(float(f32(array.DOA_RANGE_DEG[0]) + f32(i) * f32(self.cfg.grid_step_deg)))
            for i in range(self.n_angles)
        ]
        start, step = phasors(self.cfg)
        self.start_re, self.start_im = start.real.copy(), start.imag.copy()
        self.step_re, self.step_im = step.real.copy(), step.imag.copy()
        hop_s = grid.HOP_SAMPLES / grid.SAMPLE_RATE_HZ
        self.keep = f32(exp_series(-hop_s / float(f32(self.cfg.smooth_tau_s))))
        self.take = f32(1.0) - self.keep
        n = self.last - self.first + 1
        self.cross_re = np.zeros(n, dtype=np.float32)
        self.cross_im = np.zeros(n, dtype=np.float32)
        self.response = np.zeros(self.n_angles, dtype=np.float32)
        self.result = DoaResult(ANGLE_UNKNOWN_DEG, 0)

    def fold(self, x0: np.ndarray, x1: np.ndarray) -> None:
        """Smooth X0 conj(X1) of this hop into the cross-spectrum of the band."""
        band = slice(self.first, self.last + 1)
        a0, b0 = x0.real[band].astype(np.float32), x0.imag[band].astype(np.float32)
        a1, b1 = x1.real[band].astype(np.float32), x1.imag[band].astype(np.float32)
        cross_re = a0 * a1 + b0 * b1
        cross_im = b0 * a1 - a0 * b1
        self.cross_re = self.keep * self.cross_re + self.take * cross_re
        self.cross_im = self.keep * self.cross_im + self.take * cross_im

    def search(self) -> DoaResult | None:
        """Steered PHAT response over the grid; None when no bin of the band carries power."""
        power = self.cross_re * self.cross_re + self.cross_im * self.cross_im
        live = power >= POWER_MIN
        if not live.any():
            return None
        inv = np.where(live, rsqrt_f32(np.where(live, power, f32(1.0))), f32(0.0))
        a, b = self.cross_re * inv, self.cross_im * inv
        sum_c = np.zeros(len(self.start_re), dtype=np.float32)
        sum_s = np.zeros_like(sum_c)
        rot_re, rot_im = self.start_re.copy(), self.start_im.copy()
        for k in range(len(a)):
            sum_c = sum_c + a[k] * rot_re
            sum_s = sum_s + b[k] * rot_im
            rot_re, rot_im = (
                rot_re * self.step_re - rot_im * self.step_im,
                rot_re * self.step_im + rot_im * self.step_re,
            )
        half = len(sum_c)
        self.response[:half] = sum_c - sum_s
        mirrors = np.arange(self.n_angles - half)
        self.response[self.n_angles - 1 - mirrors] = (sum_c + sum_s)[mirrors]
        best = int(np.argmax(self.response))
        peak = self.response[best]
        mean = np.add.accumulate(self.response)[-1] / f32(self.n_angles)
        lifted = peak + f32(int(live.sum()))
        confidence = 0
        if lifted > 0:
            confidence = int(np.clip(np.rint(f32(CONFIDENCE_MAX) * (peak - mean) / lifted), 0, CONFIDENCE_MAX))
        return DoaResult(self.angles_deg[best], confidence)

    def process(self, x0: np.ndarray, x1: np.ndarray, update: bool) -> DoaResult:
        """Fold one hop of both channels' bins; search the grid only when update is set, else keep the last estimate."""
        self.fold(np.asarray(x0, dtype=np.complex64), np.asarray(x1, dtype=np.complex64))
        if update and (found := self.search()) is not None:
            self.result = found
        return self.result
