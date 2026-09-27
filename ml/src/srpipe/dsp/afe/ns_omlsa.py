"""ns floor of dsp_afe: the OM-LSA gain with IMCRA noise estimation, one hop of power at a time (KEHOACH 3.9).

Mirrors firmware/components/dsp_afe/src/ns_omlsa.c in float32, vectorised over the bins, with every sum taken in the
order the C takes it. Follows the author's omlsa.m of 2003 ('medium' non-stationarity, broadband decision, no tone
removal), after Cohen and Berdugo (2001) and Cohen (2003). exp and log are the module's own, from frexp, ldexp and
polynomials, and the E1 table is built with basic double arithmetic, so the C matches bit for bit on any libm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from srpipe.generated import afe, grid

f32 = np.float32
EULER_GAMMA = 0.57721566490153286
E1_SERIES_TERMS = 40
EXP_SERIES_TERMS = 30
SQRT_HALF = f32(math.sqrt(0.5))
EXP2_MIN = f32(-126.0)
# 2 atanh(t) / ln 2 = log2((1 + t) / (1 - t)): odd powers 1 .. 9, |t| <= 0.172 on [sqrt(1/2), sqrt(2)).
LOG2_COEFFS = tuple(f32(2.0 / (k * math.log(2.0))) for k in (1, 3, 5, 7, 9))
# 2^f = sum (f ln 2)^k / k! for |f| <= 1/2, degree 7.
EXP2_COEFFS = tuple(f32(math.log(2.0) ** k / math.factorial(k)) for k in range(8))
DB_PER_LOG2 = f32(10.0 * math.log10(2.0))
LOG2_E = f32(1.0 / math.log(2.0))


def log2_f32(x: np.ndarray) -> np.ndarray:
    """log2 of positive float32 values: exponent from frexp, mantissa by an atanh series; relative error < 1e-6."""
    mantissa, exponent = np.frexp(np.asarray(x, dtype=np.float32))
    low = mantissa < SQRT_HALF
    mantissa = np.where(low, mantissa * f32(2.0), mantissa)
    exponent = np.where(low, exponent - 1, exponent)
    t = (mantissa - f32(1.0)) / (mantissa + f32(1.0))
    t2 = t * t
    c1, c3, c5, c7, c9 = LOG2_COEFFS
    series = t * (c1 + t2 * (c3 + t2 * (c5 + t2 * (c7 + t2 * c9))))
    return exponent.astype(np.float32) + series


def exp2_f32(y: np.ndarray) -> np.ndarray:
    """2^y in float32: y split at the nearest integer, the rest by a degree-7 series, joined by ldexp; 0 below -126."""
    y = np.asarray(y, dtype=np.float32)
    whole = np.floor(y + f32(0.5))
    f = y - whole
    p = EXP2_COEFFS[7]
    for c in EXP2_COEFFS[6::-1]:
        p = c + f * p
    out = np.ldexp(p, np.clip(whole, EXP2_MIN, f32(127.0)).astype(np.int32))
    return np.where(y < EXP2_MIN, f32(0.0), out).astype(np.float32)


def exp_f32(x: np.ndarray) -> np.ndarray:
    return exp2_f32(np.asarray(x, dtype=np.float32) * LOG2_E)


def e1_smooth(v: float) -> float:
    """E1(v) + ln v = -gamma - sum (-v)^k / (k k!), in double with basic arithmetic only, so every libm agrees."""
    term = 1.0
    total = -EULER_GAMMA
    for k in range(1, E1_SERIES_TERMS + 1):
        term = term * -v / k
        total = total - term / k
    return total


def exp_series(x: float) -> float:
    """e^x for |x| < 1 by its series, in double with basic arithmetic only."""
    term = 1.0
    total = 1.0
    for k in range(1, EXP_SERIES_TERMS + 1):
        term = term * x / k
        total = total + term
    return total


def e1_table(points: int = afe.NS_E1_POINTS, v_max: float = afe.NS_LSA_V_MAX) -> np.ndarray:
    """exp((E1(v) + ln v) / 2) at points evenly spaced on [0, v_max]; exp(E1(v) / 2) is this over sqrt(v)."""
    step = v_max / (points - 1)
    return np.array([exp_series(0.5 * e1_smooth(i * step)) for i in range(points)], dtype=np.float32)


def hann_taps(half_width: int) -> np.ndarray:
    """MATLAB's hanning(2w + 1), without its zero ends, scaled to sum to one."""
    n = 2 * half_width + 1
    taps = [0.5 * (1.0 - math.cos(2.0 * math.pi * k / (n + 1))) for k in range(1, n + 1)]
    return np.array([t / sum(taps) for t in taps], dtype=np.float32)


def smooth(x: np.ndarray, taps: np.ndarray) -> np.ndarray:
    """Zero-padded convolution over bins, out[k] = sum over i of taps[i] x[k + w - i], i rising, as ns_omlsa.c."""
    w = (len(taps) - 1) // 2
    padded = np.concatenate([np.zeros(w, np.float32), x.astype(np.float32), np.zeros(w, np.float32)])
    out = np.zeros(len(x), dtype=np.float32)
    for i, tap in enumerate(taps):
        start = 2 * w - i
        out = out + tap * padded[start : start + len(x)]
    return out


def ordered_mean(x: np.ndarray) -> np.float32:
    return np.cumsum(x, dtype=np.float32)[-1] / f32(len(x))


def ramp(level_db: np.ndarray, bounds_db: tuple[float, float], p_min: np.float32) -> np.ndarray:
    """p_min at or under the lower bound, 1 at or over the upper, linear in dB between."""
    low, high = f32(bounds_db[0]), f32(bounds_db[1])
    rising = p_min + (level_db - low) / (high - low) * (f32(1.0) - p_min)
    return np.where(level_db <= low, p_min, np.where(level_db >= high, f32(1.0), rising)).astype(np.float32)


def bin_of(freq_hz: float, fs_hz: int, fft_points: int, n_bins: int) -> int:
    return min(round(freq_hz / fs_hz * fft_points), n_bins - 1)


@dataclass(frozen=True)
class OmlsaConfig:
    """Every constant of contracts/afe.yaml ns:, and the framing; overridden only to run at the paper's framing."""

    floor_db: float = afe.NS_FLOOR_DB
    hop_s: float = grid.HOP_SAMPLES / grid.SAMPLE_RATE_HZ
    fs_hz: int = grid.SAMPLE_RATE_HZ
    fft_points: int = grid.FFT_SIZE
    freq_smooth_bins: int = afe.NS_FREQ_SMOOTH_BINS
    smooth_tau_s: float = afe.NS_SMOOTH_TAU_S
    min_subwindows: int = afe.NS_MIN_SUBWINDOWS
    min_subwindow_s: float = afe.NS_MIN_SUBWINDOW_S
    min_bias: float = afe.NS_MIN_BIAS
    zeta0: float = afe.NS_ZETA0
    gamma0: float = afe.NS_GAMMA0
    gamma1: float = afe.NS_GAMMA1
    noise_tau_s: float = afe.NS_NOISE_TAU_S
    noise_bias: float = afe.NS_NOISE_BIAS
    xi_tau_s: float = afe.NS_XI_TAU_S
    xi_local_bins: int = afe.NS_XI_LOCAL_BINS
    xi_global_bins: int = afe.NS_XI_GLOBAL_BINS
    frame_band_hz: tuple[float, float] = afe.NS_FRAME_BAND_HZ
    local_mean_hz: tuple[float, float] = afe.NS_LOCAL_MEAN_HZ
    p_min: float = afe.NS_P_MIN
    xi_local_db: tuple[float, float] = afe.NS_XI_LOCAL_DB
    xi_global_db: tuple[float, float] = afe.NS_XI_GLOBAL_DB
    xi_frame_db: tuple[float, float] = afe.NS_XI_FRAME_DB
    xi_peak_db: tuple[float, float] = afe.NS_XI_PEAK_DB
    local_reset_hz: tuple[float, float] = afe.NS_LOCAL_RESET_HZ
    local_reset_below: float = afe.NS_LOCAL_RESET_BELOW
    q_max: float = afe.NS_Q_MAX
    q_presence_max: float = afe.NS_Q_PRESENCE_MAX
    eta_tau_s: float = afe.NS_ETA_TAU_S
    eta_min_db: float = afe.NS_ETA_MIN_DB
    lsa_v_max: float = afe.NS_LSA_V_MAX
    e1_points: int = afe.NS_E1_POINTS
    power_floor: float = afe.NS_POWER_FLOOR


def smoothing(hop_s: float, tau_s: float) -> np.float32:
    """exp(-hop / tau) in double, rounded once: the paper's factor at its 8 ms, squared at 16 ms."""
    return f32(math.exp(-hop_s / float(f32(tau_s))))


@dataclass(frozen=True)
class OmlsaHop:
    gain: np.ndarray
    speech_prob: np.float32
    noise: np.ndarray


class Omlsa:
    """One instance per stream, one hop of power at a time; idle until the first hop with any power."""

    def __init__(self, cfg: OmlsaConfig | None = None) -> None:
        cfg = cfg or OmlsaConfig()
        if cfg.floor_db > 0 or cfg.min_subwindows < 1 or cfg.lsa_v_max <= 0:
            raise ValueError(f"bad ns configuration {cfg}")
        self.cfg = cfg
        self.n_bins = cfg.fft_points // 2 + 1
        self.alpha_s = smoothing(cfg.hop_s, cfg.smooth_tau_s)
        self.alpha_d = smoothing(cfg.hop_s, cfg.noise_tau_s)
        self.alpha_xi = smoothing(cfg.hop_s, cfg.xi_tau_s)
        self.alpha_eta = smoothing(cfg.hop_s, cfg.eta_tau_s)
        self.subwindow_hops = int(np.rint(cfg.min_subwindow_s / cfg.hop_s))
        if self.subwindow_hops < 2:
            raise ValueError(f"a minimum sub-window of {self.subwindow_hops} hops is too short")
        self.eta_min = f32(10.0 ** (cfg.eta_min_db / 10.0))
        self.floor = f32(cfg.power_floor)
        self.p_min = f32(cfg.p_min)
        self.freq_taps = hann_taps(cfg.freq_smooth_bins)
        self.local_taps = hann_taps(cfg.xi_local_bins)
        self.global_taps = hann_taps(cfg.xi_global_bins)
        at = [bin_of(f, cfg.fs_hz, cfg.fft_points, self.n_bins) for f in cfg.frame_band_hz]
        self.frame_bins = slice(at[0], at[1] + 1)
        at = [bin_of(f, cfg.fs_hz, cfg.fft_points, self.n_bins) for f in cfg.local_mean_hz]
        self.local_mean_bins = slice(at[0], at[1] + 1)
        at = [bin_of(f, cfg.fs_hz, cfg.fft_points, self.n_bins) for f in cfg.local_reset_hz]
        self.local_reset_bins = slice(at[0], at[1] + 1)
        self.table = e1_table(cfg.e1_points, cfg.lsa_v_max)
        self.table_per_v = f32((cfg.e1_points - 1) / cfg.lsa_v_max)
        self.set_floor(cfg.floor_db)
        self.reset()

    def set_floor(self, floor_db: float) -> None:
        """G_min in dB, as NVS afe/ns_floor_db sets it; kept as log2 since the gain is formed in the log domain."""
        self.log2_gain_min = f32(float(f32(floor_db)) / 20.0 * math.log2(10.0))

    def reset(self) -> None:
        n = self.n_bins
        self.started = False
        self.hop = 0
        self.lambda_d = np.zeros(n, np.float32)
        self.lambda_dav = np.zeros(n, np.float32)
        self.eta_2term = np.ones(n, np.float32)
        self.xi = np.zeros(n, np.float32)
        self.xi_frame = f32(0.0)
        self.xi_peak_db = f32(self.cfg.xi_peak_db[0])
        self.s = np.zeros(n, np.float32)
        self.st = np.zeros(n, np.float32)
        self.s_min = np.zeros(n, np.float32)
        self.s_min_t = np.zeros(n, np.float32)
        self.s_act = np.zeros(n, np.float32)
        self.s_act_t = np.zeros(n, np.float32)
        self.windows = np.zeros((self.cfg.min_subwindows, n), np.float32)
        self.windows_t = np.zeros((self.cfg.min_subwindows, n), np.float32)
        self.in_subwindow = 0

    def _prior_snr(self, power: np.ndarray, noise: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        gamma = power / np.maximum(noise, self.floor)
        eta = self.alpha_eta * self.eta_2term + (f32(1.0) - self.alpha_eta) * np.maximum(gamma - f32(1.0), f32(0.0))
        eta = np.maximum(eta, self.eta_min)
        return gamma, eta, gamma * eta / (f32(1.0) + eta)

    def _presence(self, q: np.ndarray, eta: np.ndarray, v: np.ndarray) -> np.ndarray:
        """1 / (1 + q / (1 - q) (1 + eta) e^-v): the probability that speech is present, given the prior q."""
        return f32(1.0) / (f32(1.0) + q / (f32(1.0) - q) * (f32(1.0) + eta) * exp_f32(-v))

    def _track_noise(self, power: np.ndarray, eta: np.ndarray, v: np.ndarray) -> None:
        a_s, one = self.alpha_s, f32(1.0)
        bias = f32(self.cfg.min_bias)
        zeta0, gamma0, gamma1 = f32(self.cfg.zeta0), f32(self.cfg.gamma0), f32(self.cfg.gamma1)
        warming = self.hop < self.subwindow_hops - 1
        sf = smooth(power, self.freq_taps)
        if self.hop == 0:
            self.s = sf
            self.st = sf.copy()
            self.lambda_dav = power.copy()
        else:
            self.s = a_s * self.s + (one - a_s) * sf
        if warming:
            self.s_min = self.s.copy()
            self.s_act = self.s.copy()
        else:
            self.s_min = np.minimum(self.s_min, self.s)
            self.s_act = np.minimum(self.s_act, self.s)
        absent = ((power < gamma0 * bias * self.s_min) & (self.s < zeta0 * bias * self.s_min)).astype(np.float32)
        weight = smooth(absent, self.freq_taps)
        weighted = smooth(absent * power, self.freq_taps)
        sft = np.where(weight != f32(0.0), weighted / np.where(weight != f32(0.0), weight, one), self.st)
        if warming:
            self.st = self.s.copy()
            self.s_min_t = self.st.copy()
            self.s_act_t = self.st.copy()
        else:
            self.st = a_s * self.st + (one - a_s) * sft
            self.s_min_t = np.minimum(self.s_min_t, self.st)
            self.s_act_t = np.minimum(self.s_act_t, self.st)
        floor_t = np.maximum(self.s_min_t, self.floor)
        gamma_min = power / bias / floor_t
        zeta = self.s / bias / floor_t
        between = (gamma_min > one) & (gamma_min < gamma1) & (zeta < zeta0)
        q_hat = np.where(between, (gamma1 - gamma_min) / (gamma1 - one), one)
        p_hat = np.where(between, self._presence(np.where(between, q_hat, f32(0.5)), eta, v), f32(0.0))
        p_hat = np.where((gamma_min >= gamma1) | (zeta >= zeta0), one, p_hat).astype(np.float32)
        alpha = self.alpha_d + (one - self.alpha_d) * p_hat
        self.lambda_dav = alpha * self.lambda_dav + (one - alpha) * power
        self.in_subwindow += 1
        if self.in_subwindow == self.subwindow_hops:
            self.in_subwindow = 0
            if self.hop == self.subwindow_hops - 1:
                self.windows[:] = self.s
                self.windows_t[:] = self.st
            else:
                self.windows = np.concatenate([self.windows[1:], self.s_act[None]])
                self.s_min = self.windows.min(axis=0)
                self.s_act = self.s.copy()
                self.windows_t = np.concatenate([self.windows_t[1:], self.s_act_t[None]])
                self.s_min_t = self.windows_t.min(axis=0)
                self.s_act_t = self.st.copy()
        self.lambda_d = f32(self.cfg.noise_bias) * self.lambda_dav

    def _absence_prior(self, eta: np.ndarray) -> np.ndarray:
        """q, the prior probability that speech is absent, from xi at the local, global and frame scales."""
        cfg, p_min, one = self.cfg, self.p_min, f32(1.0)
        self.xi = self.alpha_xi * self.xi + (one - self.alpha_xi) * eta
        local_db = DB_PER_LOG2 * log2_f32(smooth(self.xi, self.local_taps))
        global_db = DB_PER_LOG2 * log2_f32(smooth(self.xi, self.global_taps))
        previous = self.xi_frame
        self.xi_frame = ordered_mean(self.xi[self.frame_bins])
        rising = self.xi_frame - previous >= f32(0.0)
        frame_db = DB_PER_LOG2 * log2_f32(self.xi_frame)[()] if self.xi_frame > 0 else f32(-100.0)
        p_local = ramp(local_db, cfg.xi_local_db, p_min)
        p_global = ramp(global_db, cfg.xi_global_db, p_min)
        if ordered_mean(p_local[self.local_mean_bins]) < f32(cfg.local_reset_below):
            p_local[self.local_reset_bins] = p_min
        low, high = f32(cfg.xi_frame_db[0]), f32(cfg.xi_frame_db[1])
        if frame_db <= low:
            p_frame = p_min
        elif rising:
            self.xi_peak_db = min(max(frame_db, f32(cfg.xi_peak_db[0])), f32(cfg.xi_peak_db[1]))
            p_frame = one
        elif frame_db >= self.xi_peak_db + high:
            p_frame = one
        elif frame_db <= self.xi_peak_db + low:
            p_frame = p_min
        else:
            p_frame = p_min + (frame_db - self.xi_peak_db - low) / (high - low) * (one - p_min)
        q = one - p_global * p_local * f32(p_frame)
        return np.minimum(q, f32(cfg.q_max))

    def _lsa_gain(self, eta: np.ndarray, v: np.ndarray) -> np.ndarray:
        """G_H1: eta / (1 + eta) exp(E1(v) / 2), Wiener's over lsa_v_max, 1 where v is 0, as omlsa.m."""
        wiener = eta / (f32(1.0) + eta)
        clipped = np.minimum(v, f32(self.cfg.lsa_v_max))
        position = clipped * self.table_per_v
        index = np.minimum(position.astype(np.int32), len(self.table) - 2)
        frac = position - index.astype(np.float32)
        smooth_part = self.table[index] + frac * (self.table[index + 1] - self.table[index])
        lsa = wiener * (smooth_part / np.sqrt(np.where(v > f32(0.0), v, f32(1.0))))
        gain = np.where(v > f32(self.cfg.lsa_v_max), wiener, lsa)
        return np.where(v > f32(0.0), gain, f32(1.0)).astype(np.float32)

    def process(self, power: np.ndarray, echo_power: np.ndarray | None = None) -> OmlsaHop:
        """Gains for one hop of n_bins noisy powers; echo_power, the residual echo of aec, adds to the noise."""
        power = np.asarray(power, dtype=np.float32)
        if power.shape != (self.n_bins,):
            raise ValueError(f"want {self.n_bins} bins, got shape {power.shape}")
        if not self.started:
            if not np.any(power > f32(0.0)):
                return OmlsaHop(np.ones(self.n_bins, np.float32), f32(0.0), self.lambda_d.copy())
            self.started = True
            self.lambda_d = power.copy()
        echo = np.zeros(self.n_bins, np.float32) if echo_power is None else np.asarray(echo_power, np.float32)
        _, eta, v = self._prior_snr(power, self.lambda_d + echo)
        self._track_noise(power, eta, v)
        q = self._absence_prior(eta)
        gamma, eta, v = self._prior_snr(power, self.lambda_d + echo)
        p = np.where(q < f32(self.cfg.q_presence_max), self._presence(q, eta, v), f32(0.0)).astype(np.float32)
        g_h1 = self._lsa_gain(eta, v)
        # LSA lifts bins far under the noise towards their expected level, up to gains in the thousands; the slot
        # promises 0 .. 1, so only the applied gain is capped and the decision-directed state keeps G_H1 (KEHOACH 3.9).
        gain = np.minimum(exp2_f32(p * log2_f32(g_h1) + (f32(1.0) - p) * self.log2_gain_min), f32(1.0))
        self.eta_2term = g_h1 * (g_h1 * gamma)
        self.hop += 1
        return OmlsaHop(gain, ordered_mean(p[self.frame_bins]), self.lambda_d.copy())
