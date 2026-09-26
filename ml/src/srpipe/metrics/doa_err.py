"""Direction error of doa: mean absolute error and share of frames within a tolerance (KEHOACH 3.6, 3.15)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

ANGLE_UNKNOWN_DEG = -1
TOLERANCE_DEG = 10.0
PERCENT = 100.0


@dataclass(frozen=True)
class DoaScore:
    """Scores over frames; a frame without an estimate counts against within_pct but not the mean error."""

    frames: int
    estimated: int
    mean_abs_error_deg: float
    within_pct: float


def doa_score(
    estimate_deg: np.ndarray,
    true_deg: np.ndarray,
    tolerance_deg: float = TOLERANCE_DEG,
    true_range_deg: tuple[float, float] | None = None,
) -> DoaScore:
    """Score per-frame estimates; true_range_deg keeps only frames whose truth lies in it, e.g. (0, 30) near endfire."""
    est = np.asarray(estimate_deg, dtype=np.float64)
    true = np.asarray(true_deg, dtype=np.float64)
    if est.shape != true.shape or est.ndim != 1:
        raise ValueError(f"need two 1-D angle arrays of one length, got {est.shape} and {true.shape}")
    if true_range_deg is not None:
        keep = (true >= true_range_deg[0]) & (true <= true_range_deg[1])
        est, true = est[keep], true[keep]
    if est.size == 0:
        raise ValueError("no frame to score")
    known = est != ANGLE_UNKNOWN_DEG
    error = np.abs(est - true)
    within = known & (error <= tolerance_deg)
    return DoaScore(
        frames=int(est.size),
        estimated=int(known.sum()),
        mean_abs_error_deg=float(error[known].mean()) if known.any() else float("nan"),
        within_pct=float(PERCENT * within.sum() / est.size),
    )
