"""SwiftF0's decoding of its network's outputs, the step ai_engine/src/pitch/ runs after the network (KEHOACH 3.11): the
most likely pitch step within [f_min_hz, f_max_hz], the softmax of it and its two neighbours over their log
frequencies, and the confidence a sigmoid, zero when the network's most likely step of all lies outside that range."""

from __future__ import annotations

import numpy as np

F_MIN_HZ, F_MAX_HZ = 46.875, 2093.75
PITCH_BINS = 95
STEPS_HZ = (F_MIN_HZ * (F_MAX_HZ / F_MIN_HZ) ** (np.arange(PITCH_BINS) / (PITCH_BINS - 1))).astype(np.float32)
STEPS_LOG = np.log(STEPS_HZ.astype(np.float64))
NEIGHBOURS = np.arange(-1, 2)


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
