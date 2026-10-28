"""SwiftF0's decoding of its network's outputs (KEHOACH 3.11): the most likely pitch step within [f_min_hz, f_max_hz],
the softmax of it and its two neighbours over their log frequencies, and the confidence a sigmoid, zero when the
network's most likely step of all lies outside that range. decode_int8 is the step ai_engine/src/pitch/ runs after the
network, float32 on int8 logits with tables built in double and rounded, as the chip builds them at load (step 3.3)."""

from __future__ import annotations

import numpy as np

F_MIN_HZ, F_MAX_HZ = 46.875, 2093.75
PITCH_BINS = 95
STEPS_HZ = (F_MIN_HZ * (F_MAX_HZ / F_MIN_HZ) ** (np.arange(PITCH_BINS) / (PITCH_BINS - 1))).astype(np.float32)
STEPS_LOG = np.log(STEPS_HZ.astype(np.float64))
NEIGHBOURS = np.arange(-1, 2)
INT8_STEPS = 256


def decode(
    logits: np.ndarray, confidence_logits: np.ndarray, f_min_hz: float, f_max_hz: float
) -> tuple[np.ndarray, np.ndarray]:
    """(frames,) pitch in Hz and confidence from (PITCH_BINS, frames) pitch logits and (frames,) confidence logits."""
    inside = (np.float32(f_min_hz) <= STEPS_HZ) & (np.float32(f_max_hz) >= STEPS_HZ)
    masked = np.where(inside[:, None], logits, -np.inf)
    near = masked.argmax(axis=0)[None, :] + NEIGHBOURS[:, None]
    padded = np.pad(masked, ((1, 1), (0, 0)), constant_values=-np.inf)
    picked = np.take_along_axis(padded, near + 1, axis=0).astype(np.float64)
    weights = np.exp(picked - picked.max(axis=0))
    weights /= weights.sum(axis=0)
    pitch_hz = np.exp((weights * STEPS_LOG[np.clip(near, 0, PITCH_BINS - 1)]).sum(axis=0))
    sure = (1.0 / (1.0 + np.exp(-confidence_logits.astype(np.float32)))).astype(np.float32)
    return pitch_hz, np.where(inside[logits.argmax(axis=0)], sure, np.float32(0.0))


def exp_table(exponent: int) -> np.ndarray:
    """e^(-k 2^exponent) for k = 0 .. 255 in float32: the softmax weight of a logit k int8 steps under the largest."""
    return np.exp(-np.arange(INT8_STEPS) * 2.0**exponent).astype(np.float32)


def sigmoid_table(exponent: int) -> np.ndarray:
    """The float32 sigmoid of each int8 confidence logit q at its exponent, at q + 128."""
    q = np.arange(-INT8_STEPS // 2, INT8_STEPS // 2)
    return (1.0 / (1.0 + np.exp(-q * 2.0**exponent))).astype(np.float32)


def decode_int8(
    logits: np.ndarray, logit_exponent: int, sure: np.ndarray, sure_exponent: int, f_min_hz: float, f_max_hz: float
) -> tuple[np.ndarray, np.ndarray]:
    """(frames,) float32 log F0 and confidence from (PITCH_BINS, frames) int8 pitch logits and (frames,) int8
    confidence logits at their exponents, as decode gives them but float32 in the chip's order: the weights e of the
    three steps summed left to right, each divided by the sum, then the weighted log frequencies left to right."""
    inside = (np.float32(f_min_hz) <= STEPS_HZ) & (np.float32(f_max_hz) >= STEPS_HZ)
    lo, hi = np.flatnonzero(inside)[[0, -1]]
    q = np.asarray(logits, np.int64)
    frames = np.arange(q.shape[1])
    most = lo + q[lo : hi + 1].argmax(axis=0)
    near = most[None, :] + NEIGHBOURS[:, None]
    at = np.clip(near, lo, hi)
    e = np.where((near >= lo) & (near <= hi), exp_table(logit_exponent)[q[most, frames] - q[at, frames]], 0)
    e = e.astype(np.float32)
    total = (e[0] + e[1]) + e[2]
    steps = STEPS_LOG.astype(np.float32)[at]
    log_f0 = ((e[0] / total) * steps[0] + (e[1] / total) * steps[1]) + (e[2] / total) * steps[2]
    sure_at = sigmoid_table(sure_exponent)[np.asarray(sure, np.int64) + INT8_STEPS // 2]
    return log_f0, np.where(inside[q.argmax(axis=0)], sure_at, np.float32(0.0))
