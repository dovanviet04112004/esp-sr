"""hpf of dsp_afe: second-order Butterworth high-pass per microphone, first in the chain (KEHOACH 3.4).

Mirrors the esp-dsp 1.8.2 kernels the firmware calls, in float32: coefficients as dsps_biquad_gen_hpf_f32
computes them (RBJ cookbook, Q = 1/sqrt 2), filtering as dsps_biquad_f32 (direct form II, two states).
"""

from __future__ import annotations

import numpy as np

from srpipe.generated import afe, array, grid

Q_BUTTERWORTH = np.float32(1.0 / np.sqrt(2.0))
MIN_CUTOFF_HZ = 10.0
MAX_CUTOFF_HZ = grid.SAMPLE_RATE_HZ / 4


def coefficients(cutoff_hz: float = afe.HPF_CUTOFF_HZ, fs_hz: int = grid.SAMPLE_RATE_HZ) -> np.ndarray:
    """b0, b1, b2, a1, a2 over a0, in the order and float32 arithmetic of dsps_biquad_gen_hpf_f32."""
    if not MIN_CUTOFF_HZ <= cutoff_hz <= MAX_CUTOFF_HZ:
        raise ValueError(f"cutoff {cutoff_hz} Hz outside {MIN_CUTOFF_HZ} .. {MAX_CUTOFF_HZ}")
    f = np.float32(cutoff_hz / fs_hz)
    w0 = np.float32(2.0 * np.pi * float(f))
    c, s = np.cos(w0, dtype=np.float32), np.sin(w0, dtype=np.float32)
    alpha = s / (np.float32(2.0) * Q_BUTTERWORTH)
    one, two = np.float32(1.0), np.float32(2.0)
    b0 = (one + c) / two
    a0 = one + alpha
    return np.array([b0 / a0, -(one + c) / a0, b0 / a0, -two * c / a0, (one - alpha) / a0], dtype=np.float32)


class Hpf:
    """One biquad state per channel; starts from silence."""

    def __init__(self, cutoff_hz: float = afe.HPF_CUTOFF_HZ, n_channels: int = array.N_MICS) -> None:
        self.coef = coefficients(cutoff_hz)
        self.state = np.zeros((n_channels, 2), dtype=np.float32)

    def reset(self) -> None:
        self.state[:] = 0.0

    def process(self, channel: int, samples: np.ndarray) -> np.ndarray:
        """Filter one channel's samples, float32 in and out, as dsps_biquad_f32 does sample by sample."""
        b0, b1, b2, a1, a2 = (np.float32(v) for v in self.coef)
        w0, w1 = np.float32(self.state[channel, 0]), np.float32(self.state[channel, 1])
        x = np.asarray(samples, dtype=np.float32)
        out = np.empty_like(x)
        for i, xi in enumerate(x):
            d0 = xi - a1 * w0 - a2 * w1
            out[i] = b0 * d0 + b1 * w0 + b2 * w1
            w1, w0 = w0, d0
        self.state[channel] = (w0, w1)
        return out
