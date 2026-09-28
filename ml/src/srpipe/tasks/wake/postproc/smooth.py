"""The wake output's post-processing that ai_engine/src/wake/ mirrors (KEHOACH 3.11): a trailing moving average
of the per-hop probability, then triggers against a threshold with a lockout."""

from __future__ import annotations

import numpy as np


def smooth(probs: np.ndarray, hops: int) -> np.ndarray:
    """Mean of each hop and the hops - 1 before it; the first hops average what exists so far."""
    x = np.asarray(probs, dtype=np.float32)
    total = np.cumsum(x, dtype=np.float64)
    ahead = np.concatenate([np.zeros(hops, dtype=np.float64), total[:-hops]])
    count = np.minimum(np.arange(1, len(x) + 1), hops)
    return ((total - ahead[: len(x)]) / count).astype(np.float32)


def triggers(smoothed: np.ndarray, threshold: float, lockout_hops: int) -> np.ndarray:
    """Hops where the smoothed score reaches the threshold, each followed by lockout_hops in which none counts."""
    above = np.flatnonzero(smoothed >= threshold)
    fired, k = [], 0
    while k < len(above):
        fired.append(above[k])
        k = int(np.searchsorted(above, above[k] + lockout_hops, side="right"))
    return np.array(fired, dtype=np.int64)
