"""Kaldi's pitch tracker in its own zero-latency online mode, float32 mirror of dsp_spec/pitch.h (KEHOACH 3.11).

Ported from kaldi-asr/kaldi src/feat/pitch-functions.cc and resample.cc (Apache-2.0; Ghahremani et al., ICASSP 2014),
with every default constant: downsampling through a windowed sinc, two NCCFs per frame, sinc interpolation to
log-spaced lags, Viterbi over them, then the POV, POV-weighted mean-normalised log pitch and delta of
OnlineProcessPitch with no right context. The frame shift is the grid hop, and a hop in gives one frame out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields

import numba
import numpy as np

from srpipe.generated import grid

N_FEATURES = 3
LEAD_HOPS = 2


@dataclass(frozen=True)
class PitchConfig:
    """Mirror of dsp_spec_pitch_config_t; the values come from the feature config of a model, never from here."""

    resample_hz: float
    lowpass_cutoff_hz: float
    lowpass_zeros: int
    upsample_zeros: int
    window_s: float
    min_f0_hz: float
    max_f0_hz: float
    soft_min_f0: float
    penalty_factor: float
    delta_pitch: float
    nccf_ballast: float
    normalization_left_s: float
    delta_window: int
    pov_scale: float
    pitch_scale: float
    delta_pitch_scale: float


def as_float32(cfg: PitchConfig) -> PitchConfig:
    """Every field as the C struct and Kaldi's BaseFloat hold it."""
    return PitchConfig(
        **{
            f.name: float(np.float32(getattr(cfg, f.name))) if f.type == "float" else getattr(cfg, f.name)
            for f in fields(cfg)
        }
    )


def sinc_value(t: float, cutoff_hz: float, zeros: int) -> float:
    """Kaldi's windowed sinc h(t) = f(t) g(t): a Hann window over zeros / (2 cutoff) each side of a lowpass at cutoff;
    in double through the C library's sin and cos, as the C builds its tables once at init."""
    if not abs(t) < zeros / (2.0 * cutoff_hz):
        return 0.0
    window = 0.5 * (1.0 + math.cos(2.0 * math.pi * cutoff_hz / zeros * t))
    return (2.0 * cutoff_hz if t == 0.0 else math.sin(2.0 * math.pi * cutoff_hz * t) / (math.pi * t)) * window


def log_lags(cfg: PitchConfig) -> np.ndarray:
    """Lags from 1/max_f0 to 1/min_f0 in steps of 1 + delta_pitch, grown in float32 exactly as SelectLags does."""
    lag, last = np.float32(1.0 / cfg.max_f0_hz), np.float32(1.0 / cfg.min_f0_hz)
    step = 1.0 + float(np.float32(cfg.delta_pitch))
    lags = []
    while lag <= last:
        lags.append(lag)
        lag = np.float32(float(lag) * step)
    return np.array(lags, dtype=np.float32)


def seq_dot(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Dot products along the last axis, summed term by term in float32 as the C loop does."""
    return np.cumsum(np.asarray(a, np.float32) * np.asarray(b, np.float32), axis=-1, dtype=np.float32)[..., -1]


@numba.njit
def distance_transform(prev: np.ndarray, factor: np.float32) -> tuple[np.ndarray, np.ndarray]:
    """min over j of factor (i - j)^2 + prev[j] for every i, and the j reaching it, in time linear in the states by
    the lower envelope of parabolas (Felzenszwalb and Huttenlocher, 2012); float32 throughout, compiled by numba."""
    n = len(prev)
    idx = np.arange(n).astype(np.float32)
    anchor = prev + factor * idx * idx
    two_factor = np.float32(2.0) * factor
    v = np.zeros(n, dtype=np.int64)
    z = np.empty(n + 1, dtype=np.float32)
    k = 0
    z[0] = -np.inf
    z[1] = np.inf
    for q in range(1, n):
        s = (anchor[q] - anchor[v[k]]) / (two_factor * np.float32(q - v[k]))
        while s <= z[k]:
            k -= 1
            s = (anchor[q] - anchor[v[k]]) / (two_factor * np.float32(q - v[k]))
        k += 1
        v[k] = q
        z[k] = s
        z[k + 1] = np.inf
    best = np.empty(n, dtype=np.int64)
    k = 0
    for i in range(n):
        while z[k + 1] < i:
            k += 1
        best[i] = v[k]
    d = (np.arange(n) - best).astype(np.float32)
    return (d * d) * factor + prev[best], best


@numba.njit
def _nccf_to_pov(n: float) -> np.float32:
    a = min(abs(np.float64(n)), 1.0)
    r = -5.2 + 5.4 * math.exp(7.5 * (a - 1.0)) + 4.8 * a - 2.0 * math.exp(-10.0 * a) + 4.2 * math.exp(20.0 * (a - 1.0))
    return np.float32(1.0 / (1.0 + math.exp(-r)))


@numba.njit
def _nccf_to_pov_feature(n: float) -> np.float32:
    c = min(max(np.float64(n), -1.0), 1.0)
    return np.float32((1.0001 - c) ** 0.15 - 1.0)


def nccf_to_pov(n: float) -> np.float32:
    """Kaldi's probability of voicing from an NCCF (NccfToPov), the weight of each frame in the log-pitch mean."""
    return np.float32(_nccf_to_pov(n))


def nccf_to_pov_feature(n: float) -> np.float32:
    """Kaldi's POV feature (NccfToPovFeature): the NCCF made roughly Gaussian."""
    return np.float32(_nccf_to_pov_feature(n))


@numba.njit
def _path_features(
    forward: np.ndarray,
    backpointers: np.ndarray,
    pov_nccf: np.ndarray,
    head: int,
    count: int,
    lags: np.ndarray,
    context: int,
    delta_scales: np.ndarray,
    scales: np.ndarray,
):
    """POV, normalised log pitch and delta over the Viterbi path traced back from the best state, and the newest
    frame's NCCF and F0; backpointers and pov_nccf ring the last count frames from head, oldest first; scales are
    pov_scale, pitch_scale and delta_pitch_scale."""
    rows = len(backpointers)
    state = np.argmin(forward)
    path = np.empty(count, dtype=np.int64)
    path[count - 1] = state
    for i in range(count - 1, 0, -1):
        path[i - 1] = backpointers[(head + i) % rows, path[i]]
    nccf = np.empty(count, dtype=np.float32)
    log_pitch = np.empty(count, dtype=np.float32)
    for k in range(count):
        nccf[k] = pov_nccf[(head + k) % rows, path[k]]
        log_pitch[k] = np.float32(math.log(np.float64(np.float32(1.0 / np.float64(lags[path[k]])))))
    window = min(count, context + 1)
    sum_pov, sum_log_pitch_pov = np.float32(0.0), np.float32(0.0)
    for k in range(count - window, count):
        pov = _nccf_to_pov(nccf[k])
        sum_pov += pov
        sum_log_pitch_pov += pov * log_pitch[k]
    normalised = (log_pitch[count - 1] - sum_log_pitch_pov / sum_pov) * scales[1]
    reach = (len(delta_scales) - 1) // 2
    delta = np.float32(0.0)
    newest = count - 1
    for i in range(len(delta_scales)):
        if delta_scales[i] != np.float32(0.0):
            delta += delta_scales[i] * log_pitch[min(max(newest + i - reach, newest - min(newest, reach)), newest)]
    out = np.empty(N_FEATURES, dtype=np.float32)
    out[0] = scales[0] * _nccf_to_pov_feature(nccf[newest])
    out[1] = normalised
    out[2] = delta * scales[2]
    return out, nccf[newest], np.float32(1.0 / np.float64(lags[state]))


class PitchTracker:
    """One stream of 16 kHz samples, one grid hop per step; reset at every command window (KEHOACH 3.11)."""

    def __init__(self, cfg: PitchConfig) -> None:
        rate, hop = grid.SAMPLE_RATE_HZ, grid.HOP_SAMPLES
        if rate % cfg.resample_hz or (hop * cfg.resample_hz) % rate:
            raise ValueError(f"resample_hz {cfg.resample_hz} must divide {rate} Hz into whole hops")
        cfg = as_float32(cfg)
        self.cfg = cfg
        self.decimation = int(rate // cfg.resample_hz)
        self.shift = int(hop * cfg.resample_hz // rate)
        half = cfg.lowpass_zeros / (2.0 * cfg.lowpass_cutoff_hz)
        self.taps_before = math.ceil(-half * rate)
        taps = range(self.taps_before, math.floor(half * rate) + 1)
        self.down_weights = np.array(
            [sinc_value(k / rate, cfg.lowpass_cutoff_hz, cfg.lowpass_zeros) / rate for k in taps], dtype=np.float32
        )
        self.down_delay = math.floor(half * rate)
        self.window = int(cfg.resample_hz * cfg.window_s)
        widen = cfg.upsample_zeros / (2.0 * cfg.resample_hz)
        self.first_lag = math.ceil(cfg.resample_hz * (1.0 / cfg.max_f0_hz - widen))
        self.last_lag = math.floor(cfg.resample_hz * (1.0 / cfg.min_f0_hz + widen))
        self.frame_length = self.window + self.last_lag
        self.lags = log_lags(cfg)
        self._nccf_weights()
        # Kaldi rounds the squared log step to float, then scales it (ComputeBacktraces).
        step_sq = np.float32(math.log(1.0 + cfg.delta_pitch) ** 2)
        self.factor = step_sq * np.float32(cfg.penalty_factor)
        self.soft_min = np.float32(cfg.soft_min_f0)
        self.context = round(cfg.normalization_left_s * rate / hop)
        self.history = max(self.context, cfg.delta_window)
        self.delta_scales = np.array(
            [
                j / sum(k * k for k in range(-cfg.delta_window, cfg.delta_window + 1))
                for j in range(-cfg.delta_window, cfg.delta_window + 1)
            ],
            dtype=np.float32,
        )
        self.reset()

    def _nccf_weights(self) -> None:
        cfg, measured = self.cfg, self.last_lag + 1 - self.first_lag
        cutoff = cfg.resample_hz * 0.5
        points = self.lags - np.float32(self.first_lag / cfg.resample_hz)
        width = np.float32(cfg.upsample_zeros / (2.0 * cutoff))
        low = np.maximum(np.ceil(np.float32(cfg.resample_hz) * (points - width)).astype(np.int64), 0)
        high = np.minimum(np.floor(np.float32(cfg.resample_hz) * (points + width)).astype(np.int64), measured - 1)
        self.up_first = low
        self.up_taps = int(np.max(high - low + 1))
        weights = np.zeros((len(points), self.up_taps), dtype=np.float32)
        for i, (p, lo, hi) in enumerate(zip(points, low, high, strict=True)):
            for j in range(hi - lo + 1):
                t = float(np.float32(float(p) - (lo + j) / cfg.resample_hz))
                weights[i, j] = sinc_value(t, cutoff, cfg.upsample_zeros) / cfg.resample_hz
        self.up_weights = weights
        self.up_index = np.minimum(low[:, None] + np.arange(self.up_taps)[None, :], measured - 1)

    def reset(self) -> None:
        """Forget the stream: the next step starts a new window, as after a command window ends."""
        self.samples = np.zeros(0, dtype=np.float32)
        self.sample_offset = 0
        self.down = np.zeros(0, dtype=np.float32)
        self.down_offset = 0
        self.down_count = 0
        self.sum = 0.0
        self.sumsq = 0.0
        self.frames = 0
        self.forward = np.zeros(len(self.lags), dtype=np.float32)
        self.backpointers = np.zeros((self.history + 1, len(self.lags)), dtype=np.int64)
        self.pov_nccf = np.zeros((self.history + 1, len(self.lags)), dtype=np.float32)
        self.ring_head = 0
        self.ring_count = 0
        self.latest = (np.float32(0.0), np.float32(0.0))

    def _downsample(self) -> np.ndarray:
        """Every output whose taps lie inside the samples seen (Kaldi's LinearResample, no flush); taps before the
        first sample read zero. Samples no later output needs are dropped."""
        have = self.sample_offset + len(self.samples)
        n = np.arange(self.down_count, (have - 1 - self.down_delay) // self.decimation + 1)
        taps = n[:, None] * self.decimation + self.taps_before + np.arange(len(self.down_weights))[None, :]
        x = np.where(taps >= 0, self.samples[np.clip(taps - self.sample_offset, 0, None)], np.float32(0.0))
        out = seq_dot(self.down_weights[None, :], x) if len(n) else np.zeros(0, dtype=np.float32)
        self.down_count += len(n)
        keep = max(0, self.down_count * self.decimation + self.taps_before)
        self.samples = self.samples[keep - self.sample_offset :]
        self.sample_offset = keep
        return out.astype(np.float32)

    def _nccf(self, frame: np.ndarray, ballast: np.float32) -> tuple[np.ndarray, np.ndarray]:
        """The NCCF at every whole lag from first_lag to last_lag, with the ballast and without (ComputeCorrelation,
        ComputeNccf): the window's mean is that of its first window samples."""
        w = self.window
        z = frame - np.cumsum(frame[:w], dtype=np.float32)[-1] / np.float32(w)
        head = z[:w]
        e1 = seq_dot(head, head)
        shifted = np.lib.stride_tricks.sliding_window_view(z, w)[self.first_lag : self.last_lag + 1]
        e2 = seq_dot(shifted, shifted)
        inner = seq_dot(shifted, head[None, :])
        norm = e1 * e2
        with_ballast = np.sqrt(norm + ballast)
        without = np.sqrt(norm)
        pitch = np.where(with_ballast != 0, inner / np.where(with_ballast != 0, with_ballast, 1), 0).astype(np.float32)
        pov = np.where(without != 0, inner / np.where(without != 0, without, 1), 0).astype(np.float32)
        return pitch, pov

    def _frame(self, f: int) -> None:
        """Track frame f: NCCFs, resampled to the log lags, one Viterbi step, the best state kept for traceback."""
        n = self.down_count
        mean_square = self.sumsq / n - (self.sum / n) ** 2
        ballast = np.float32((mean_square * self.window) ** 2 * self.cfg.nccf_ballast)
        start = f * self.shift - self.down_offset
        nccf_pitch, nccf_pov = self._nccf(self.down[start : start + self.frame_length], ballast)
        pitch_lags = seq_dot(self.up_weights, nccf_pitch[self.up_index])
        pov_lags = seq_dot(self.up_weights, nccf_pov[self.up_index])
        local = (np.float32(1.0) - pitch_lags) + (self.soft_min * self.lags) * pitch_lags
        forward, backpointer = distance_transform(self.forward, self.factor)
        forward = forward + local
        self.forward = (forward - np.min(forward)).astype(np.float32)
        rows = len(self.backpointers)
        if self.ring_count < rows:
            slot = (self.ring_head + self.ring_count) % rows
            self.ring_count += 1
        else:
            slot = self.ring_head
            self.ring_head = (self.ring_head + 1) % rows
        self.backpointers[slot] = backpointer
        self.pov_nccf[slot] = pov_lags

    def _features(self) -> np.ndarray:
        """POV, normalised log pitch and delta of the newest frame over the Viterbi path traced back from its best
        state, as OnlineProcessPitch reads a zero-latency tracker with no right context."""
        scales = np.array([self.cfg.pov_scale, self.cfg.pitch_scale, self.cfg.delta_pitch_scale], dtype=np.float32)
        features, nccf, f0 = _path_features(
            self.forward,
            self.backpointers,
            self.pov_nccf,
            self.ring_head,
            self.ring_count,
            self.lags,
            self.context,
            self.delta_scales,
            scales,
        )
        self.latest = (np.float32(nccf), np.float32(f0))
        return features

    def step(self, hop: np.ndarray) -> np.ndarray:
        """One grid hop of samples in, the three features of the frame it completes out; zeros for the first
        LEAD_HOPS hops, which complete no frame."""
        hop = np.asarray(hop, dtype=np.float32)
        if hop.shape != (grid.HOP_SAMPLES,):
            raise ValueError(f"want {grid.HOP_SAMPLES} samples, got shape {hop.shape}")
        self.samples = np.concatenate([self.samples, hop])
        new = self._downsample()
        self.down = np.concatenate([self.down, new])
        if len(new):
            # Each hop summed in float32, the running totals in double, as Kaldi adds each chunk's VecVec.
            self.sum += float(np.cumsum(new, dtype=np.float32)[-1])
            self.sumsq += float(seq_dot(new, new))
        full = self.down_count >= self.frame_length
        ready = (self.down_count - self.frame_length) // self.shift + 1 if full else 0
        if ready == self.frames:
            return np.zeros(N_FEATURES, dtype=np.float32)
        for f in range(self.frames, ready):
            self._frame(f)
        self.frames = ready
        keep = self.frames * self.shift
        self.down = self.down[keep - self.down_offset :]
        self.down_offset = keep
        return self._features()


def pitch_features(x: np.ndarray, cfg: PitchConfig) -> tuple[np.ndarray, np.ndarray]:
    """The tracker run over every whole hop of a 16 kHz signal from a reset: (n_hops, 3) features [POV, normalised
    log pitch, delta] aligned hop for hop with dsp_spec mel frames, and (n_hops, 2) [NCCF, F0 Hz] of the newest
    frame for measurement; rows of the first LEAD_HOPS hops are zero."""
    tracker = PitchTracker(cfg)
    hops = len(x) // grid.HOP_SAMPLES
    features = np.zeros((hops, N_FEATURES), dtype=np.float32)
    raw = np.zeros((hops, 2), dtype=np.float32)
    for h in range(hops):
        features[h] = tracker.step(x[h * grid.HOP_SAMPLES : (h + 1) * grid.HOP_SAMPLES])
        if h >= LEAD_HOPS:
            raw[h] = tracker.latest
    return features, raw
