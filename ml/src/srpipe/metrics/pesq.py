"""Wideband PESQ (ITU-T P.862.2) through the pesq package, for ns (KEHOACH 3.15).

Licence: the wrapper (ludlows/python-pesq) is MIT; its P.862 C code stays under the PESQ notice of
Psytechnics and OPTICOM, which allows evaluation with no external commercial use of the results and needs
their licence for anything else. So PESQ scores stay in esp-sr's own reports, the code is never shipped,
and nothing is decided on PESQ alone (KEHOACH 3.15).
"""

from __future__ import annotations

import numpy as np
from pesq import pesq

from srpipe.generated import grid
from srpipe.metrics.sisdr import as_pair

PESQ_WB_MIN, PESQ_WB_MAX = 1.04, 4.644


def pesq_wb(estimate: np.ndarray, reference: np.ndarray, fs_hz: int = grid.SAMPLE_RATE_HZ) -> float:
    """MOS-LQO of P.862.2, PESQ_WB_MIN to PESQ_WB_MAX; raises when the reference holds no speech-like part."""
    est, ref = as_pair(estimate, reference)
    return float(pesq(fs_hz, ref, est, "wb"))
