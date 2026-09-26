"""Windows of the shared time grid; mirror of dsp_spec/window.h (KEHOACH 3.1)."""

from __future__ import annotations

import numpy as np


def sqrt_hann(n: int) -> np.ndarray:
    """Periodic square-root Hann of n points as float32; its square overlap-adds to one at a hop of n / 2.

    sin(pi * i / n) equals sqrt(0.5 - 0.5 cos(2 pi i / n)) and is the form the C side evaluates.
    """
    if n <= 0:
        raise ValueError(f"window length {n} is not positive")
    i = np.arange(n, dtype=np.float32)
    return np.sin(np.float32(np.pi) * i / np.float32(n)).astype(np.float32)
