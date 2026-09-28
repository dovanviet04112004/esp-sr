"""gsc of dsp_afe: a two-microphone sidelobe canceller in the STFT domain, steered by doa (KEHOACH 3.7).

Mirror of gsc.c in float32: ch1 turned onto ch0 by the steered delay, the beam their mean, the block their difference,
one weight per bin learning by leaky NLMS only when told to, its norm capped. Steering comes from double series turned
bin by bin, the division and the cap from ns_omlsa's Newton forms, so the C matches bit for bit on any libm.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from srpipe.dsp.afe.doa import sin_series
from srpipe.dsp.afe.ns_omlsa import PI, cos_series, recip_f32, rsqrt_f32
from srpipe.generated import afe, array, grid

f32 = np.float32
HALF = f32(0.5)


@dataclass(frozen=True)
class GscConfig:
    """Field for field as dsp_afe_gsc_config_t."""

    spacing_m: float = array.SPACING_M
    speed_of_sound_m_s: float = array.SPEED_OF_SOUND_M_S
    step_size: float = afe.GSC_STEP_SIZE
    leakage: float = afe.GSC_LEAKAGE
    weight_max: float = afe.GSC_WEIGHT_MAX


def check(cfg: GscConfig) -> None:
    """The checks of gsc.c config_ok."""
    spacing, speed, mu, leak, cap = (
        f32(v) for v in (cfg.spacing_m, cfg.speed_of_sound_m_s, cfg.step_size, cfg.leakage, cfg.weight_max)
    )
    if not (spacing > 0 and speed > 0 and mu > 0 and leak >= 0 and cap > 0 and f32(1.0) - mu * leak > 0):
        raise ValueError(f"bad gsc configuration {cfg}")


def steering(cfg: GscConfig, angle_deg: float) -> tuple[np.ndarray, np.ndarray]:
    """exp(-j w_k tau) for every bin, tau the delay of angle_deg: bin 0 is 1, each next bin turns the last once."""
    bin_hz = grid.SAMPLE_RATE_HZ / grid.FFT_SIZE
    theta_rad = float(f32(angle_deg)) * PI / 180.0
    tau_s = float(f32(cfg.spacing_m)) * cos_series(theta_rad) / float(f32(cfg.speed_of_sound_m_s))
    per_bin = 2.0 * PI * bin_hz * tau_s
    turn_re, turn_im = f32(cos_series(per_bin)), f32(-sin_series(per_bin))
    re = np.empty(grid.N_BINS, dtype=np.float32)
    im = np.empty(grid.N_BINS, dtype=np.float32)
    c, s = f32(1.0), f32(0.0)
    for k in range(grid.N_BINS):
        re[k], im[k] = c, s
        c, s = c * turn_re - s * turn_im, c * turn_im + s * turn_re
    return re, im


class Gsc:
    """One canceller; starts with zero weights, so its first output is the steered mean."""

    def __init__(self, cfg: GscConfig | None = None) -> None:
        self.cfg = cfg or GscConfig()
        check(self.cfg)
        self.mu = f32(self.cfg.step_size)
        self.keep = f32(1.0) - self.mu * f32(self.cfg.leakage)
        self.cap = f32(self.cfg.weight_max)
        self.cap_power = self.cap * self.cap
        self.floor = f32(afe.GSC_POWER_FLOOR)
        self.w_re = np.zeros(grid.N_BINS, dtype=np.float32)
        self.w_im = np.zeros(grid.N_BINS, dtype=np.float32)
        self.angle_deg: np.float32 | None = None
        self.turn_re = self.turn_im = None

    def _beam(self, x0: np.ndarray, x1: np.ndarray, angle_deg: float):
        if self.angle_deg is None or f32(angle_deg) != self.angle_deg:
            self.angle_deg = f32(angle_deg)
            self.turn_re, self.turn_im = steering(self.cfg, float(self.angle_deg))
        a0, b0 = x0.real.astype(np.float32), x0.imag.astype(np.float32)
        a1, b1 = x1.real.astype(np.float32), x1.imag.astype(np.float32)
        s_re, s_im = self.turn_re, self.turn_im
        al_re = a1 * s_re - b1 * s_im
        al_im = a1 * s_im + b1 * s_re
        f_re, f_im = HALF * (a0 + al_re), HALF * (b0 + al_im)
        k_re, k_im = a0 - al_re, b0 - al_im
        y_re = f_re - (self.w_re * k_re + self.w_im * k_im)
        y_im = f_im - (self.w_re * k_im - self.w_im * k_re)
        return (k_re, k_im), (y_re, y_im)

    def apply(self, x0: np.ndarray, x1: np.ndarray, angle_deg: float) -> np.ndarray:
        """The output for these bins with the weights as they stand, learning nothing: for scoring each source apart."""
        _, (y_re, y_im) = self._beam(np.asarray(x0, np.complex64), np.asarray(x1, np.complex64), angle_deg)
        return (y_re + 1j * y_im).astype(np.complex64)

    def process(self, x0: np.ndarray, x1: np.ndarray, angle_deg: float, adapt: bool) -> np.ndarray:
        """Beam toward angle_deg; the weights learn from this hop only when adapt is set."""
        (k_re, k_im), (y_re, y_im) = self._beam(np.asarray(x0, np.complex64), np.asarray(x1, np.complex64), angle_deg)
        if adapt:
            gain = self.mu * recip_f32(k_re * k_re + k_im * k_im + self.floor)
            w_re = self.keep * self.w_re + gain * (k_re * y_re + k_im * y_im)
            w_im = self.keep * self.w_im + gain * (k_im * y_re - k_re * y_im)
            norm = w_re * w_re + w_im * w_im
            over = norm > self.cap_power
            shrink = np.where(over, self.cap * rsqrt_f32(np.where(over, norm, f32(1.0))), f32(1.0))
            self.w_re = np.where(over, w_re * shrink, w_re)
            self.w_im = np.where(over, w_im * shrink, w_im)
        return (y_re + 1j * y_im).astype(np.complex64)
