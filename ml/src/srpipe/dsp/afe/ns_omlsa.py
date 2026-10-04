"""ns floor of dsp_afe: the OM-LSA gain with IMCRA noise estimation, one hop of power at a time (KEHOACH 3.9).

Mirrors firmware/components/dsp_afe/src/ns_omlsa.c in float32, vectorised over the bins, every sum in the C's order.
Follows omlsa.m of 2003 ('medium', broadband decision, no tone removal) after Cohen and Berdugo (2001), Cohen (2003).
exp, log, 1/x and 1/sqrt x are the module's own (bits, series, Newton steps: a float32 division costs ~60 cycles on the
ESP32-S3) and the E1 table is built with basic double arithmetic, so the C matches bit for bit on any libm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numba
import numpy as np

from srpipe.generated import afe, grid

f32 = np.float32
EULER_GAMMA = 0.57721566490153286
E1_SERIES_TERMS = 40
EXP_SERIES_TERMS = 40
COS_SERIES_TERMS = 30
PI = 3.14159265358979323846
LN_2 = 0.69314718055994530942
LN_10 = 2.30258509299404568402
LOG2_10 = 3.32192809488736234787
SQRT_HALF = f32(math.sqrt(0.5))
EXP2_MIN = f32(-126.0)
RECIP_MAGIC = np.uint32(0x7EF311C3)
RSQRT_MAGIC = np.uint32(0x5F3759DF)
NEWTON_STEPS = 3
# 2 atanh(t) / ln 2 = log2((1 + t) / (1 - t)): odd powers 1 .. 9, |t| <= 0.172 on [sqrt(1/2), sqrt(2)).
LOG2_COEFFS = tuple(f32(2.0 / (k * LN_2)) for k in (1, 3, 5, 7, 9))
DB_PER_LOG2 = f32(10.0 / LOG2_10)
LOG2_E = f32(1.0 / LN_2)


def _exp2_coeffs(degree: int) -> tuple[np.float32, ...]:
    """(ln 2)^k / k! for 2^f = sum (f ln 2)^k / k!, |f| <= 1/2, by products only."""
    coeffs, c = [], 1.0
    for k in range(degree + 1):
        coeffs.append(f32(c))
        c = c * LN_2 / (k + 1)
    return tuple(coeffs)


EXP2_COEFFS = _exp2_coeffs(7)


RECIP_MAGIC_BITS = int(RECIP_MAGIC)
RSQRT_MAGIC_BITS = int(RSQRT_MAGIC)
LOG2_C = np.array(LOG2_COEFFS, dtype=np.float32)
EXP2_C = np.array(EXP2_COEFFS, dtype=np.float32)


@numba.njit
def _recip(b: np.ndarray) -> np.ndarray:
    guess = np.empty(len(b), dtype=np.uint32)
    bits = b.view(np.uint32)
    for k in range(len(b)):
        guess[k] = (RECIP_MAGIC_BITS - np.int64(bits[k])) & 0xFFFFFFFF
    r = guess.view(np.float32)
    two = np.float32(2.0)
    for _ in range(NEWTON_STEPS):
        for k in range(len(b)):
            r[k] = r[k] * (two - b[k] * r[k])
    return r


@numba.njit
def _rsqrt(v: np.ndarray) -> np.ndarray:
    guess = np.empty(len(v), dtype=np.uint32)
    bits = v.view(np.uint32)
    for k in range(len(v)):
        guess[k] = (RSQRT_MAGIC_BITS - (np.int64(bits[k]) >> 1)) & 0xFFFFFFFF
    r = guess.view(np.float32)
    half, three_halves = np.float32(0.5), np.float32(1.5)
    for _ in range(NEWTON_STEPS):
        for k in range(len(v)):
            r[k] = r[k] * (three_halves - half * v[k] * r[k] * r[k])
    return r


@numba.njit
def _log2(x: np.ndarray) -> np.ndarray:
    n = len(x)
    mantissa = np.empty(n, dtype=np.float32)
    exponent = np.empty(n, dtype=np.float32)
    for k in range(n):
        m, e = math.frexp(x[k])
        m32 = np.float32(m)
        if m32 < SQRT_HALF:
            m32 = m32 * np.float32(2.0)
            e -= 1
        mantissa[k] = m32
        exponent[k] = np.float32(e)
    to_one = _recip(mantissa + np.float32(1.0))
    out = np.empty(n, dtype=np.float32)
    for k in range(n):
        t = (mantissa[k] - np.float32(1.0)) * to_one[k]
        t2 = t * t
        out[k] = exponent[k] + t * (LOG2_C[0] + t2 * (LOG2_C[1] + t2 * (LOG2_C[2] + t2 * (LOG2_C[3] + t2 * LOG2_C[4]))))
    return out


@numba.njit
def _exp2(y: np.ndarray) -> np.ndarray:
    out = np.empty(len(y), dtype=np.float32)
    for k in range(len(y)):
        whole = np.floor(y[k] + np.float32(0.5))
        f = y[k] - whole
        p = EXP2_C[7]
        for j in range(6, -1, -1):
            p = EXP2_C[j] + f * p
        if y[k] < EXP2_MIN:
            out[k] = np.float32(0.0)
        else:
            out[k] = np.float32(math.ldexp(p, int(min(max(whole, EXP2_MIN), np.float32(127.0)))))
    return out


def _flat(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a = np.asarray(x, dtype=np.float32)
    return a, np.ascontiguousarray(a).reshape(-1)


def recip_f32(b: np.ndarray) -> np.ndarray:
    """1 / b for positive normal float32 b: a guess off the bits, then three Newton steps; relative error < 3e-7."""
    a, flat = _flat(b)
    return _recip(flat).reshape(a.shape)[()]


def rsqrt_f32(v: np.ndarray) -> np.ndarray:
    """1 / sqrt(v) for positive normal float32 v: a guess off the bits, then three Newton steps."""
    a, flat = _flat(v)
    return _rsqrt(flat).reshape(a.shape)[()]


def log2_f32(x: np.ndarray) -> np.ndarray:
    """log2 of positive float32 values: exponent from frexp, mantissa by an atanh series; relative error < 1e-6."""
    a, flat = _flat(x)
    return _log2(flat).reshape(a.shape)[()]


def exp2_f32(y: np.ndarray) -> np.ndarray:
    """2^y in float32: y split at the nearest integer, the rest by a degree-7 series, joined by ldexp; 0 below -126."""
    a, flat = _flat(y)
    return _exp2(flat).reshape(a.shape)[()]


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
    """e^x for |x| < 5 by its series, in double with basic arithmetic only."""
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


def cos_series(x: float) -> float:
    """cos x for 0 <= x <= 2 pi by its series, in double with basic arithmetic only."""
    term = 1.0
    total = 1.0
    for k in range(1, COS_SERIES_TERMS + 1):
        term = term * -x * x / ((2 * k - 1) * (2 * k))
        total = total + term
    return total


def hann_taps(half_width: int) -> np.ndarray:
    """MATLAB's hanning(2w + 1), without its zero ends, scaled to sum to one; summed in order, as ns_omlsa.c."""
    n = 2 * half_width + 1
    taps = [0.5 * (1.0 - cos_series(2.0 * PI * k / (n + 1))) for k in range(1, n + 1)]
    total = 0.0
    for t in taps:
        total = total + t
    return np.array([t / total for t in taps], dtype=np.float32)


@numba.njit
def _smooth(x: np.ndarray, taps: np.ndarray) -> np.ndarray:
    w = (len(taps) - 1) // 2
    n = len(x)
    padded = np.zeros(n + 2 * w, dtype=np.float32)
    padded[w : w + n] = x
    out = np.zeros(n, dtype=np.float32)
    for i in range(len(taps)):
        start = 2 * w - i
        for k in range(n):
            out[k] = out[k] + taps[i] * padded[start + k]
    return out


@numba.njit
def _ordered_sum(x: np.ndarray) -> np.float32:
    total = x[0]
    for k in range(1, len(x)):
        total = total + x[k]
    return total


def smooth(x: np.ndarray, taps: np.ndarray) -> np.ndarray:
    """Zero-padded convolution over bins, out[k] = sum over i of taps[i] x[k + w - i], i rising, as ns_omlsa.c."""
    return _smooth(np.ascontiguousarray(x, dtype=np.float32), np.ascontiguousarray(taps, dtype=np.float32))


def ordered_sum(x: np.ndarray) -> np.float32:
    return np.float32(_ordered_sum(np.ascontiguousarray(x, dtype=np.float32)))


@dataclass(frozen=True)
class Ramp:
    """From p_min to 1 over a span in dB: the bounds in dB and as linear ratios, and 1 / span, made once."""

    low_db: np.float32
    high_db: np.float32
    low: np.float32
    high: np.float32
    per_db: np.float32

    @classmethod
    def of(cls, bounds_db: tuple[float, float]) -> Ramp:
        low, high = (float(f32(b)) for b in bounds_db)
        ratio = [f32(exp_series(b / 10.0 * LN_10)) for b in (low, high)]
        return cls(f32(low), f32(high), ratio[0], ratio[1], f32(1.0 / (high - low)))

    def row(self) -> np.ndarray:
        return np.array([self.low_db, self.high_db, self.low, self.high, self.per_db], dtype=np.float32)


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
    """exp(-hop / tau) in double, rounded once: the paper's factor at its 8 ms, squared at 16 ms; hop < 5 tau."""
    return f32(exp_series(-hop_s / float(f32(tau_s))))


@dataclass(frozen=True)
class OmlsaHop:
    gain: np.ndarray
    speech_prob: np.float32
    noise: np.ndarray


# Rows of Omlsa.state and slots of its constants and bin bounds, as the hop kernel reads them.
LAMBDA_D, LAMBDA_DAV, ETA_2TERM, XI, S, ST, S_MIN, S_MIN_T, S_ACT, S_ACT_T = range(10)
(
    C_ALPHA_S,
    C_ALPHA_D,
    C_ALPHA_XI,
    C_ALPHA_ETA,
    C_ETA_MIN,
    C_FLOOR,
    C_P_MIN,
    C_TABLE_PER_V,
    C_PER_GAMMA1,
    C_PER_FRAME_BIN,
    C_PER_MEAN_BIN,
    C_LOG2_GAIN_MIN,
    C_MIN_BIAS,
    C_ZETA0,
    C_GAMMA0,
    C_GAMMA1,
    C_NOISE_BIAS,
    C_LOCAL_RESET_BELOW,
    C_Q_MAX,
    C_Q_PRESENCE_MAX,
    C_LSA_V_MAX,
    C_PEAK_LOW,
    C_PEAK_HIGH,
    C_COUNT,
) = range(24)
B_SUBWINDOW, B_FRAME, B_FRAME_END, B_MEAN, B_MEAN_END, B_RESET, B_RESET_END = range(7)
STARTED, HOP, IN_SUBWINDOW = range(3)
XI_FRAME, XI_PEAK_DB = range(2)


@numba.njit
def _prior_snr(power: np.ndarray, noise: np.ndarray, eta_2term: np.ndarray, c: np.ndarray):
    """gamma, the decision-directed eta, v and the Wiener gain eta / (1 + eta), which v and G_H1 share."""
    n = len(power)
    one = np.float32(1.0)
    floored = np.empty(n, dtype=np.float32)
    for k in range(n):
        floored[k] = max(noise[k], c[C_FLOOR])
    per_noise = _recip(floored)
    gamma = np.empty(n, dtype=np.float32)
    eta = np.empty(n, dtype=np.float32)
    for k in range(n):
        gamma[k] = power[k] * per_noise[k]
        prior = c[C_ALPHA_ETA] * eta_2term[k] + (one - c[C_ALPHA_ETA]) * max(gamma[k] - one, np.float32(0.0))
        eta[k] = max(prior, c[C_ETA_MIN])
    per_eta = _recip(eta + one)
    wiener = np.empty(n, dtype=np.float32)
    v = np.empty(n, dtype=np.float32)
    for k in range(n):
        wiener[k] = eta[k] * per_eta[k]
        v[k] = gamma[k] * wiener[k]
    return gamma, eta, v, wiener


@numba.njit
def _presence(q: np.ndarray, eta: np.ndarray, v: np.ndarray) -> np.ndarray:
    """(1 - q) / (1 - q + q (1 + eta) e^-v): the probability that speech is present, given the prior q."""
    n = len(q)
    one = np.float32(1.0)
    decay = _exp2((-v) * LOG2_E)
    denominator = np.empty(n, dtype=np.float32)
    for k in range(n):
        denominator[k] = (one - q[k]) + q[k] * (one + eta[k]) * decay[k]
    per = _recip(denominator)
    out = np.empty(n, dtype=np.float32)
    for k in range(n):
        out[k] = (one - q[k]) * per[k]
    return out


@numba.njit
def _track_noise(
    power: np.ndarray,
    eta: np.ndarray,
    v: np.ndarray,
    c: np.ndarray,
    bounds: np.ndarray,
    taps: np.ndarray,
    state: np.ndarray,
    windows: np.ndarray,
    counts: np.ndarray,
):
    n = len(power)
    one = np.float32(1.0)
    a_s, a_d = c[C_ALPHA_S], c[C_ALPHA_D]
    bias, zeta0, gamma0, gamma1 = c[C_MIN_BIAS], c[C_ZETA0], c[C_GAMMA0], c[C_GAMMA1]
    hop = counts[HOP]
    span = bounds[B_SUBWINDOW]
    warming = hop < span - 1
    s, st, lambda_dav = state[S], state[ST], state[LAMBDA_DAV]
    s_min, s_min_t, s_act, s_act_t = state[S_MIN], state[S_MIN_T], state[S_ACT], state[S_ACT_T]
    sf = _smooth(power, taps)
    if hop == 0:
        s[:] = sf
        st[:] = sf
        lambda_dav[:] = power
    else:
        for k in range(n):
            s[k] = a_s * s[k] + (one - a_s) * sf[k]
    if warming:
        s_min[:] = s
        s_act[:] = s
    else:
        for k in range(n):
            s_min[k] = min(s_min[k], s[k])
            s_act[k] = min(s_act[k], s[k])
    power_gate, smooth_gate = gamma0 * bias, zeta0 * bias
    absent = np.zeros(n, dtype=np.float32)
    absent_power = np.empty(n, dtype=np.float32)
    for k in range(n):
        if power[k] < power_gate * s_min[k] and s[k] < smooth_gate * s_min[k]:
            absent[k] = one
        absent_power[k] = absent[k] * power[k]
    weight = _smooth(absent, taps)
    weighted = _smooth(absent_power, taps)
    divisor = np.empty(n, dtype=np.float32)
    for k in range(n):
        divisor[k] = weight[k] if weight[k] != np.float32(0.0) else one
    per_weight = _recip(divisor)
    if warming:
        st[:] = s
        s_min_t[:] = st
        s_act_t[:] = st
    else:
        for k in range(n):
            sft = weighted[k] * per_weight[k] if weight[k] != np.float32(0.0) else st[k]
            st[k] = a_s * st[k] + (one - a_s) * sft
            s_min_t[k] = min(s_min_t[k], st[k])
            s_act_t[k] = min(s_act_t[k], st[k])
    floored = np.empty(n, dtype=np.float32)
    for k in range(n):
        floored[k] = bias * max(s_min_t[k], c[C_FLOOR])
    per_floor = _recip(floored)
    gamma_min = np.empty(n, dtype=np.float32)
    zeta = np.empty(n, dtype=np.float32)
    between = np.empty(n, dtype=np.bool_)
    q = np.empty(n, dtype=np.float32)
    for k in range(n):
        gamma_min[k] = power[k] * per_floor[k]
        zeta[k] = s[k] * per_floor[k]
        between[k] = gamma_min[k] > one and gamma_min[k] < gamma1 and zeta[k] < zeta0
        q[k] = (gamma1 - gamma_min[k]) * c[C_PER_GAMMA1] if between[k] else np.float32(0.5)
    present = _presence(q, eta, v)
    for k in range(n):
        p_hat = present[k] if between[k] else np.float32(0.0)
        if gamma_min[k] >= gamma1 or zeta[k] >= zeta0:
            p_hat = one
        alpha = a_d + (one - a_d) * p_hat
        lambda_dav[k] = alpha * lambda_dav[k] + (one - alpha) * power[k]
    counts[IN_SUBWINDOW] += 1
    if counts[IN_SUBWINDOW] == span:
        counts[IN_SUBWINDOW] = 0
        last = windows.shape[1] - 1
        if hop == span - 1:
            for u in range(last + 1):
                windows[0, u, :] = s
                windows[1, u, :] = st
        else:
            for which, act, low, now in ((0, s_act, s_min, s), (1, s_act_t, s_min_t, st)):
                for u in range(last):
                    windows[which, u, :] = windows[which, u + 1, :]
                windows[which, last, :] = act
                for k in range(n):
                    lowest = windows[which, 0, k]
                    for u in range(1, last + 1):
                        lowest = min(lowest, windows[which, u, k])
                    low[k] = lowest
                act[:] = now
    for k in range(n):
        state[LAMBDA_D, k] = c[C_NOISE_BIAS] * lambda_dav[k]


@numba.njit
def _of_ratio(x: np.ndarray, ramp: np.ndarray, p_min: np.float32) -> np.ndarray:
    """p_min at or under the lower bound, 1 at or over the upper, linear in dB between."""
    level = _log2(x)
    out = np.empty(len(x), dtype=np.float32)
    for k in range(len(x)):
        if x[k] <= ramp[2]:
            out[k] = p_min
        elif x[k] >= ramp[3]:
            out[k] = np.float32(1.0)
        else:
            out[k] = p_min + (DB_PER_LOG2 * level[k] - ramp[0]) * ramp[4] * (np.float32(1.0) - p_min)
    return out


@numba.njit
def _absence_prior(
    eta: np.ndarray,
    c: np.ndarray,
    ramps: np.ndarray,
    bounds: np.ndarray,
    taps: tuple,
    state: np.ndarray,
    frame: np.ndarray,
):
    """q, the prior probability that speech is absent, from xi at the local, global and frame scales."""
    one, p_min = np.float32(1.0), c[C_P_MIN]
    xi = state[XI]
    for k in range(len(xi)):
        xi[k] = c[C_ALPHA_XI] * xi[k] + (one - c[C_ALPHA_XI]) * eta[k]
    p_local = _of_ratio(_smooth(xi, taps[0]), ramps[0], p_min)
    p_global = _of_ratio(_smooth(xi, taps[1]), ramps[1], p_min)
    previous = frame[XI_FRAME]
    frame[XI_FRAME] = _ordered_sum(xi[bounds[B_FRAME] : bounds[B_FRAME_END]]) * c[C_PER_FRAME_BIN]
    rising = frame[XI_FRAME] - previous >= np.float32(0.0)
    frame_db = np.float32(-100.0)
    if frame[XI_FRAME] > np.float32(0.0):
        frame_db = DB_PER_LOG2 * _log2(frame[XI_FRAME : XI_FRAME + 1])[0]
    if _ordered_sum(p_local[bounds[B_MEAN] : bounds[B_MEAN_END]]) * c[C_PER_MEAN_BIN] < c[C_LOCAL_RESET_BELOW]:
        p_local[bounds[B_RESET] : bounds[B_RESET_END]] = p_min
    low, high, per_db = ramps[2, 0], ramps[2, 1], ramps[2, 4]
    if frame_db <= low:
        p_frame = p_min
    elif rising:
        frame[XI_PEAK_DB] = min(max(frame_db, c[C_PEAK_LOW]), c[C_PEAK_HIGH])
        p_frame = one
    elif frame_db >= frame[XI_PEAK_DB] + high:
        p_frame = one
    elif frame_db <= frame[XI_PEAK_DB] + low:
        p_frame = p_min
    else:
        p_frame = p_min + ((frame_db - frame[XI_PEAK_DB]) - low) * per_db * (one - p_min)
    q = np.empty(len(xi), dtype=np.float32)
    for k in range(len(xi)):
        q[k] = min(one - p_global[k] * p_local[k] * p_frame, c[C_Q_MAX])
    return q


@numba.njit
def _lsa_gain(wiener: np.ndarray, v: np.ndarray, v_max: np.float32, table: np.ndarray, per_v: np.float32):
    """G_H1: eta / (1 + eta) exp(E1(v) / 2), Wiener's over lsa_v_max, 1 where v is 0, as omlsa.m."""
    n = len(v)
    one = np.float32(1.0)
    positive = np.empty(n, dtype=np.float32)
    for k in range(n):
        positive[k] = v[k] if v[k] > np.float32(0.0) else one
    per_root = _rsqrt(positive)
    out = np.empty(n, dtype=np.float32)
    last = len(table) - 2
    for k in range(n):
        position = min(v[k], v_max) * per_v
        index = min(np.int32(position), last)
        frac = position - np.float32(index)
        lsa = wiener[k] * ((table[index] + frac * (table[index + 1] - table[index])) * per_root[k])
        gain = wiener[k] if v[k] > v_max else lsa
        out[k] = gain if v[k] > np.float32(0.0) else one
    return out


@numba.njit
def _hop(
    power: np.ndarray,
    echo: np.ndarray,
    c: np.ndarray,
    ramps: np.ndarray,
    bounds: np.ndarray,
    taps: tuple,
    table: np.ndarray,
    state: np.ndarray,
    windows: np.ndarray,
    counts: np.ndarray,
    frame: np.ndarray,
):
    """One hop of OmlsaHop's gains and speech probability, the state left for the next."""
    n = len(power)
    one = np.float32(1.0)
    gain = np.ones(n, dtype=np.float32)
    if counts[STARTED] == 0:
        if not np.any(power > np.float32(0.0)):
            return gain, np.float32(0.0)
        counts[STARTED] = 1
        state[LAMBDA_D, :] = power
    _, eta, v, _ = _prior_snr(power, state[LAMBDA_D] + echo, state[ETA_2TERM], c)
    _track_noise(power, eta, v, c, bounds, taps[0], state, windows, counts)
    q = _absence_prior(eta, c, ramps, bounds, (taps[1], taps[2]), state, frame)
    gamma, eta, v, wiener = _prior_snr(power, state[LAMBDA_D] + echo, state[ETA_2TERM], c)
    present = _presence(q, eta, v)
    p = np.zeros(n, dtype=np.float32)
    for k in range(n):
        if q[k] < c[C_Q_PRESENCE_MAX]:
            p[k] = present[k]
    g_h1 = _lsa_gain(wiener, v, c[C_LSA_V_MAX], table, c[C_TABLE_PER_V])
    log_gain = _log2(g_h1)
    blend = np.empty(n, dtype=np.float32)
    for k in range(n):
        blend[k] = p[k] * log_gain[k] + (one - p[k]) * c[C_LOG2_GAIN_MIN]
    # LSA lifts bins far under the noise towards their expected level, up to gains in the thousands; the slot
    # promises 0 .. 1, so only the applied gain is capped and the decision-directed state keeps G_H1 (KEHOACH 3.9).
    lifted = _exp2(blend)
    for k in range(n):
        gain[k] = min(lifted[k], one)
        state[ETA_2TERM, k] = g_h1[k] * (g_h1[k] * gamma[k])
    counts[HOP] += 1
    return gain, _ordered_sum(p[bounds[B_FRAME] : bounds[B_FRAME_END]]) * c[C_PER_FRAME_BIN]


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
        self.eta_min = f32(exp_series(float(f32(cfg.eta_min_db)) / 10.0 * LN_10))
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
        self.per_gamma1 = f32(1.0 / (float(f32(cfg.gamma1)) - 1.0))
        self.per_frame_bin = f32(1.0 / (self.frame_bins.stop - self.frame_bins.start))
        self.per_mean_bin = f32(1.0 / (self.local_mean_bins.stop - self.local_mean_bins.start))
        self.local_ramp = Ramp.of(cfg.xi_local_db)
        self.global_ramp = Ramp.of(cfg.xi_global_db)
        self.frame_ramp = Ramp.of(cfg.xi_frame_db)
        self.constants = np.zeros(C_COUNT, dtype=np.float32)
        for slot, value in (
            (C_ALPHA_S, self.alpha_s),
            (C_ALPHA_D, self.alpha_d),
            (C_ALPHA_XI, self.alpha_xi),
            (C_ALPHA_ETA, self.alpha_eta),
            (C_ETA_MIN, self.eta_min),
            (C_FLOOR, self.floor),
            (C_P_MIN, self.p_min),
            (C_TABLE_PER_V, self.table_per_v),
            (C_PER_GAMMA1, self.per_gamma1),
            (C_PER_FRAME_BIN, self.per_frame_bin),
            (C_PER_MEAN_BIN, self.per_mean_bin),
            (C_MIN_BIAS, cfg.min_bias),
            (C_ZETA0, cfg.zeta0),
            (C_GAMMA0, cfg.gamma0),
            (C_GAMMA1, cfg.gamma1),
            (C_NOISE_BIAS, cfg.noise_bias),
            (C_LOCAL_RESET_BELOW, cfg.local_reset_below),
            (C_Q_MAX, cfg.q_max),
            (C_Q_PRESENCE_MAX, cfg.q_presence_max),
            (C_LSA_V_MAX, cfg.lsa_v_max),
            (C_PEAK_LOW, cfg.xi_peak_db[0]),
            (C_PEAK_HIGH, cfg.xi_peak_db[1]),
        ):
            self.constants[slot] = f32(value)
        self.ramps = np.stack([self.local_ramp.row(), self.global_ramp.row(), self.frame_ramp.row()])
        self.bounds = np.array(
            [
                self.subwindow_hops,
                self.frame_bins.start,
                self.frame_bins.stop,
                self.local_mean_bins.start,
                self.local_mean_bins.stop,
                self.local_reset_bins.start,
                self.local_reset_bins.stop,
            ],
            dtype=np.int64,
        )
        self.set_floor(cfg.floor_db)
        self.reset()

    def set_floor(self, floor_db: float) -> None:
        """G_min in dB, as NVS afe/ns_floor_db sets it; kept as log2 since the gain is formed in the log domain."""
        self.log2_gain_min = f32(float(f32(floor_db)) / 20.0 * LOG2_10)
        self.constants[C_LOG2_GAIN_MIN] = self.log2_gain_min

    def reset(self) -> None:
        n = self.n_bins
        self.state = np.zeros((S_ACT_T + 1, n), dtype=np.float32)
        self.state[ETA_2TERM] = f32(1.0)
        self.windows = np.zeros((2, self.cfg.min_subwindows, n), dtype=np.float32)
        self.counts = np.zeros(IN_SUBWINDOW + 1, dtype=np.int64)
        self.frame = np.array([0.0, self.cfg.xi_peak_db[0]], dtype=np.float32)

    @property
    def started(self) -> bool:
        return bool(self.counts[STARTED])

    @property
    def lambda_d(self) -> np.ndarray:
        return self.state[LAMBDA_D]

    def _lsa_gain(self, wiener: np.ndarray, v: np.ndarray) -> np.ndarray:
        return _lsa_gain(
            np.ascontiguousarray(wiener, dtype=np.float32),
            np.ascontiguousarray(v, dtype=np.float32),
            self.constants[C_LSA_V_MAX],
            self.table,
            self.table_per_v,
        )

    def process(self, power: np.ndarray, echo_power: np.ndarray | None = None) -> OmlsaHop:
        """Gains for one hop of n_bins noisy powers; echo_power, the residual echo of aec, adds to the noise."""
        power = np.ascontiguousarray(power, dtype=np.float32)
        if power.shape != (self.n_bins,):
            raise ValueError(f"want {self.n_bins} bins, got shape {power.shape}")
        echo = np.zeros(self.n_bins, np.float32) if echo_power is None else np.ascontiguousarray(echo_power, np.float32)
        taps = (self.freq_taps, self.local_taps, self.global_taps)
        gain, prob = _hop(
            power,
            echo,
            self.constants,
            self.ramps,
            self.bounds,
            taps,
            self.table,
            self.state,
            self.windows,
            self.counts,
            self.frame,
        )
        return OmlsaHop(gain, f32(prob), self.state[LAMBDA_D].copy())
