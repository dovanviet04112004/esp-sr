"""Detection error trade-off of wake: miss rate against false alarms per hour (KEHOACH 3.15)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DetCurve:
    """One point per threshold, thresholds rising; a score at or above the threshold fires."""

    thresholds: np.ndarray
    miss_rate: np.ndarray
    false_alarms_per_hour: np.ndarray


def det_curve(positive_scores: np.ndarray, negative_scores: np.ndarray, negative_hours: float) -> DetCurve:
    """positive_scores: best score of each wake utterance; negative_scores: every detection peak in negative_hours."""
    pos = np.sort(np.asarray(positive_scores, dtype=np.float64))
    neg = np.sort(np.asarray(negative_scores, dtype=np.float64))
    if pos.size == 0 or negative_hours <= 0:
        raise ValueError("need at least one positive and a positive amount of negative audio")
    thresholds = np.unique(np.concatenate([pos, neg, [np.inf]]))
    missed = np.searchsorted(pos, thresholds, side="left")
    fired = neg.size - np.searchsorted(neg, thresholds, side="left")
    return DetCurve(thresholds, missed / pos.size, fired / negative_hours)


def miss_rate_at(curve: DetCurve, false_alarms_per_hour: float) -> tuple[float, float]:
    """Lowest miss rate whose false alarm rate stays at or under the budget, and the threshold that gives it."""
    allowed = curve.false_alarms_per_hour <= false_alarms_per_hour
    best = int(np.flatnonzero(allowed)[0])
    return float(curve.miss_rate[best]), float(curve.thresholds[best])
