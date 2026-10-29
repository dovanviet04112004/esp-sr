"""The wake output's post-processing that ai_engine/src/wake/ mirrors (KEHOACH 3.11): a trailing moving average
of the per-hop probability, then triggers against a threshold with a lockout; chip_scores is the same in integer
permille as the chip runs it on int8 logits."""

from __future__ import annotations

import numpy as np

LOGITS = 256  # an int8 logit's values, -128 first


def permille_table(exponent: int) -> np.ndarray:
    """The probability in permille of each int8 logit at exponent, 1000 / (1 + e^-x) rounded half up in double, as
    ai_engine builds it at load."""
    x = np.arange(-LOGITS // 2, LOGITS // 2, dtype=np.float64) * 2.0**exponent
    return np.floor(1000.0 / (1.0 + np.exp(-x)) + 0.5).astype(np.uint16)


def chip_scores(logits: np.ndarray, exponent: int, hops: int) -> np.ndarray:
    """Each hop's permille through permille_table, then the mean of it and the hops - 1 before it in integers,
    rounded half up; the first hops average what exists so far."""
    p = permille_table(exponent)[np.asarray(logits, dtype=np.int64) + LOGITS // 2].astype(np.int64)
    total = np.cumsum(p)
    ahead = np.concatenate([np.zeros(hops, dtype=np.int64), total])[: len(p)]
    count = np.minimum(np.arange(1, len(p) + 1), hops)
    return ((total - ahead + count // 2) // count).astype(np.uint16)


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
