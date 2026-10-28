"""Front end of the pitch network SwiftF0, what dsp_spec/swift_front computes for the chip (KEHOACH 3.11).

Per grid hop three magnitude spectra of 1024, 2048 and 512 points by the radix-4 FFT of dsp_spec, each frame centred
on the hop's first sample under its window, zeros before the stream; each through its sparse band matrix onto the
network's 128 log-spaced bands, summed bin by bin, then mel's own ln of the sum plus LOG_FLOOR. Windows and band
matrices are the pinned model's. float32, every step as dsp_spec/swift_front.c takes it, so the two match bit for bit.
"""

from __future__ import annotations

from dataclasses import dataclass

import numba
import numpy as np

from srpipe.dsp.spec import fft
from srpipe.dsp.spec.mel import ln_f32
from srpipe.generated import grid

SIZES = (1024, 2048, 512)  # FFT points, in the network's channel order
BANDS = 128
LOG_FLOOR = np.float32(1e-8)
DELAY_HOPS = max(SIZES) // 2 // grid.HOP_SAMPLES - 1  # a hop's frame comes out with the hop this many later
PCM_FULL_SCALE = np.float32(32768.0)


@dataclass(frozen=True)
class Bands:
    """A band matrix as dsp_spec walks it: each band's first bin and count of bins, their weights band after band."""

    first: np.ndarray
    count: np.ndarray
    weights: np.ndarray


def sparse(matrix: np.ndarray) -> Bands:
    """matrix (bins, BANDS) as Bands, each band from its first weighted bin to its last."""
    first, count, weights = [], [], []
    for b in range(matrix.shape[1]):
        rows = np.flatnonzero(matrix[:, b])
        lo, hi = (int(rows[0]), int(rows[-1]) + 1) if rows.size else (0, 0)
        first.append(lo)
        count.append(hi - lo)
        weights.append(matrix[lo:hi, b])
    return Bands(np.asarray(first, np.uint16), np.asarray(count, np.uint16), np.concatenate(weights).astype(np.float32))


@numba.njit
def _band_sums(magnitude: np.ndarray, first: np.ndarray, count: np.ndarray, weights: np.ndarray, floor: np.float32):
    out = np.empty(len(first), dtype=np.float32)
    w = 0
    for b in range(len(first)):
        acc = np.float32(0.0)
        for i in range(count[b]):
            acc += weights[w + i] * magnitude[first[b] + i]
        w += count[b]
        out[b] = acc + floor
    return out


def frame_logs(frames: np.ndarray, window: np.ndarray, bands: Bands) -> np.ndarray:
    """(frames, BANDS) log band magnitudes of (frames, n) float32 samples under window."""
    bins = fft.forward(np.asarray(frames, np.float32) * window)
    magnitude = np.sqrt(np.square(bins.real) + np.square(bins.imag))
    sums = [_band_sums(m, bands.first, bands.count, bands.weights, LOG_FLOOR) for m in magnitude]
    return ln_f32(np.stack(sums))


def spectra(samples: np.ndarray, windows: dict[int, np.ndarray], bands: dict[int, np.ndarray]) -> np.ndarray:
    """(len(SIZES), BANDS, hops) log band magnitudes of float samples in [-1, 1), one frame per whole hop and at
    least one, zeros past both ends of the stream; windows[n] has n values and bands[n] is (n / 2 + 1, BANDS) for
    each n of SIZES."""
    hop = grid.HOP_SAMPLES
    hops = max(len(samples) // hop, 1)
    out = []
    for size in SIZES:
        zeros = np.zeros(size // 2, np.float32)
        padded = np.concatenate([zeros, np.asarray(samples, np.float32), zeros, np.zeros(hop, np.float32)])
        frames = np.lib.stride_tricks.sliding_window_view(padded, size)[::hop][:hops]
        out.append(frame_logs(frames, windows[size], sparse(bands[size])).T)
    return np.stack(out).astype(np.float32)


class SwiftFront:
    """dsp_spec_swift_front: one hop of int16 PCM in, the frame DELAY_HOPS hops back out once whole, as
    (len(SIZES), BANDS) log band magnitudes."""

    def __init__(self, windows: dict[int, np.ndarray], bands: dict[int, Bands]) -> None:
        self.windows, self.bands = windows, bands
        self.reset()

    def reset(self) -> None:
        """Forget every sample, as at the start of a stream."""
        self.history = np.zeros(max(SIZES), np.float32)
        self.pushed = 0

    def push(self, hop: np.ndarray) -> np.ndarray | None:
        samples = np.asarray(hop, np.int16).astype(np.float32) / PCM_FULL_SCALE
        self.history = np.concatenate([self.history[len(samples) :], samples])
        self.pushed += 1
        if self.pushed <= DELAY_HOPS:
            return None
        centre = max(SIZES) // 2
        return np.stack(
            [
                frame_logs(self.history[None, centre - n // 2 : centre + n // 2], self.windows[n], self.bands[n])[0]
                for n in SIZES
            ]
        )
