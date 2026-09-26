"""Short-time objective intelligibility through pystoi (MIT licence), for ns (KEHOACH 3.15)."""

from __future__ import annotations

import numpy as np
from pystoi import stoi

from srpipe.generated import grid
from srpipe.metrics.sisdr import as_pair


def stoi_score(
    estimate: np.ndarray, reference: np.ndarray, fs_hz: int = grid.SAMPLE_RATE_HZ, extended: bool = False
) -> float:
    """STOI, 0 to 1 (extended: ESTOI); pystoi resamples to its own 10 kHz and drops silent frames."""
    est, ref = as_pair(estimate, reference)
    return float(stoi(ref, est, fs_hz, extended=extended))
