"""SwiftF0's three Kaldi-scale pitch dims as dsp_spec/swift_dims computes them streaming (KEHOACH 3.11 step 3.3): a
frame's POV of its confidence, its log F0 less the confidence-weighted mean over the voiced frames left_frames back and
right_frames ahead, and its delta, each frame given once right_frames have followed it. An unvoiced frame's log F0 is
drawn straight between the voiced frames seen around it; the last voiced one's is held while none follows, the next
one's taken while none precedes. float32, every step as the C takes it, so the two match bit for bit.
"""

from __future__ import annotations

from dataclasses import dataclass

import numba
import numpy as np

from srpipe.dsp.spec.pitch import _nccf_to_pov_feature
from srpipe.generated import grid

DIMS = 3
DELTA_TAPS = 2  # frames each side, as Kaldi's delta
DELTA_NORM = np.float32(10.0)  # the sum of j * j over the taps


@dataclass(frozen=True)
class DimsConfig:
    """The dims' thresholds, windows in frames and scales, from listen.yaml's pitch merged with pitch_source."""

    confidence: float
    left_frames: int
    right_frames: int
    pov_scale: float
    pitch_scale: float
    delta_scale: float

    @classmethod
    def of(cls, spec: dict) -> DimsConfig:
        hops_per_s = grid.SAMPLE_RATE_HZ / grid.HOP_SAMPLES
        return cls(
            spec["confidence"],
            round(spec["normalization_left_s"] * hops_per_s),
            round(spec["normalization_right_s"] * hops_per_s),
            spec["pov_scale"],
            spec["pitch_scale"],
            spec["delta_pitch_scale"],
        )


@numba.njit
def _neighbours(voiced: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = len(voiced)
    before, after = np.empty(n, np.int64), np.empty(n, np.int64)
    last = -1
    for s in range(n):
        before[s] = last
        if voiced[s]:
            last = s
    nxt = n
    for s in range(n - 1, -1, -1):
        after[s] = nxt
        if voiced[s]:
            nxt = s
    return before, after


@numba.njit
def _log_f0_at(log_f0, voiced, before, after, s: int, seen: int) -> np.float32:
    if voiced[s]:
        return log_f0[s]
    p, v = before[s], after[s]
    if v > seen:
        v = -1
    if p >= 0 and v >= 0:
        return log_f0[p] + (log_f0[v] - log_f0[p]) * (np.float32(s - p) / np.float32(v - p))
    if v >= 0:
        return log_f0[v]
    if p >= 0:
        return log_f0[p]
    return np.float32(0.0)


@numba.njit
def _dims(log_f0, confidence, threshold, left, right, scales) -> np.ndarray:
    n = len(log_f0)
    voiced = confidence >= threshold
    before, after = _neighbours(voiced)
    out = np.zeros((n, DIMS), np.float32)
    for t in range(n):
        seen = min(t + right, n - 1)
        held, weighted = np.float32(0.0), np.float32(0.0)
        for u in range(max(0, t - left), seen + 1):
            if voiced[u]:
                held += confidence[u]
                weighted += confidence[u] * log_f0[u]
        if held > np.float32(0.0):
            out[t, 1] = (_log_f0_at(log_f0, voiced, before, after, t, seen) - weighted / held) * scales[1]
        a = _log_f0_at(log_f0, voiced, before, after, max(t - 2, 0), seen)
        b = _log_f0_at(log_f0, voiced, before, after, max(t - 1, 0), seen)
        d = _log_f0_at(log_f0, voiced, before, after, min(t + 1, seen), seen)
        e = _log_f0_at(log_f0, voiced, before, after, min(t + 2, seen), seen)
        out[t, 2] = (np.float32(2.0) * (e - a) + (d - b)) / DELTA_NORM * scales[2]
        out[t, 0] = _nccf_to_pov_feature(confidence[t]) * scales[0]
    return out


def dims(log_f0: np.ndarray, confidence: np.ndarray, cfg: DimsConfig) -> np.ndarray:
    """(frames, DIMS) float32 of a stream's per-frame float32 log F0 and confidence: POV, normalised log F0, delta. A
    frame is what dsp_spec_swift_dims_push gives right_frames later; the last right_frames are as they stand once the
    stream ends, which the chip never reaches."""
    scales = np.array([cfg.pov_scale, cfg.pitch_scale, cfg.delta_scale], np.float32)
    return _dims(
        np.asarray(log_f0, np.float32),
        np.asarray(confidence, np.float32),
        np.float32(cfg.confidence),
        cfg.left_frames,
        cfg.right_frames,
        scales,
    )
