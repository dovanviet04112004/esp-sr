"""hpf of dsp_afe: second-order Butterworth high-pass per microphone, first in the chain (KEHOACH 3.4, ADR-0004).

Mirrors firmware/components/dsp_afe/src/hpf.c in float32: RBJ coefficients at Q = 1/sqrt 2, with cosine and sine
taken in double and rounded once so every libm agrees, then a transposed direct form II, operation for operation.
"""

from __future__ import annotations

import numba
import numpy as np

from srpipe.generated import afe, array, grid

Q_BUTTERWORTH = np.float32(1.0 / np.sqrt(2.0))
MIN_CUTOFF_HZ = 10.0
MAX_CUTOFF_HZ = grid.SAMPLE_RATE_HZ / 4


def coefficients(cutoff_hz: float = afe.HPF_CUTOFF_HZ, fs_hz: int = grid.SAMPLE_RATE_HZ) -> np.ndarray:
    """b0, b1, b2, a1, a2 over a0 in float32, computed in the order hpf.c computes them."""
    if not MIN_CUTOFF_HZ <= cutoff_hz <= MAX_CUTOFF_HZ:
        raise ValueError(f"cutoff {cutoff_hz} Hz outside {MIN_CUTOFF_HZ} .. {MAX_CUTOFF_HZ}")
    one, two = np.float32(1.0), np.float32(2.0)
    w0 = two * np.float32(np.pi) * np.float32(cutoff_hz) / np.float32(fs_hz)
    c = np.float32(np.cos(np.float64(w0)))
    alpha = np.float32(np.sin(np.float64(w0))) / (two * Q_BUTTERWORTH)
    a0 = one + alpha
    b0 = (one + c) / two / a0
    return np.array([b0, -(one + c) / a0, b0, -two * c / a0, (one - alpha) / a0], dtype=np.float32)


class Hpf:
    """One biquad state per channel; starts from silence."""

    def __init__(self, cutoff_hz: float = afe.HPF_CUTOFF_HZ, n_channels: int = array.N_MICS) -> None:
        self.coef = coefficients(cutoff_hz)
        self.state = np.zeros((n_channels, 2), dtype=np.float32)

    def reset(self) -> None:
        self.state[:] = 0.0

    def process(self, channel: int, samples: np.ndarray) -> np.ndarray:
        """Filter one channel's samples, float32 in and out, sample by sample in transposed direct form II."""
        return _biquad(np.asarray(samples, dtype=np.float32), self.coef, self.state[channel])


@numba.njit
def _biquad(x: np.ndarray, coef: np.ndarray, state: np.ndarray) -> np.ndarray:
    """x through b0, b1, b2, a1, a2 in transposed direct form II from state, which it leaves at the end of x."""
    b0, b1, b2, a1, a2 = coef[0], coef[1], coef[2], coef[3], coef[4]
    s0, s1 = state[0], state[1]
    out = np.empty_like(x)
    for i in range(len(x)):
        xi = x[i]
        y = b0 * xi + s0
        s0 = b1 * xi - a1 * y + s1
        s1 = b2 * xi - a2 * y
        out[i] = y
    state[0], state[1] = s0, s1
    return out
