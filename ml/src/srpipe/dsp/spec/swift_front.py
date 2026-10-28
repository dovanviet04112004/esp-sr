"""Front end of the pitch network SwiftF0, what dsp_spec computes for ai_engine/src/pitch/ (KEHOACH 3.11).

Per grid hop three magnitude spectra of 1024, 2048 and 512 points by the radix-4 FFT of dsp_spec, each frame centred on
the hop's first sample under its window, zeros beyond both ends of the stream; each through its band matrix onto the
network's 128 log-spaced bands, then log(x + LOG_FLOOR). Windows and band matrices are the pinned model's. float32.
"""

from __future__ import annotations

import numpy as np

from srpipe.dsp.spec import fft
from srpipe.generated import grid

SIZES = (1024, 2048, 512)  # FFT points, in the network's channel order
LOG_FLOOR = np.float32(1e-8)


def spectra(samples: np.ndarray, windows: dict[int, np.ndarray], bands: dict[int, np.ndarray]) -> np.ndarray:
    """(len(SIZES), bands, hops) log band magnitudes of float samples in [-1, 1), one frame per whole hop and at
    least one; windows[n] has n values and bands[n] is (n / 2 + 1, bands) for each n of SIZES."""
    hop = grid.HOP_SAMPLES
    hops = max(len(samples) // hop, 1)
    out = []
    for size in SIZES:
        zeros = np.zeros(size // 2, np.float32)
        padded = np.concatenate([zeros, np.asarray(samples, np.float32), zeros, np.zeros(hop, np.float32)])
        frames = np.lib.stride_tricks.sliding_window_view(padded, size)[::hop][:hops] * windows[size]
        bins = fft.forward(frames)
        magnitude = np.sqrt(np.square(bins.real) + np.square(bins.imag))
        out.append(np.log(magnitude @ bands[size] + LOG_FLOOR).T)
    return np.stack(out).astype(np.float32)
