"""RNNoise-16k's features and gain mapping (KEHOACH 3.9, ADR-0014): the float32 streaming mirror of what
ai_engine/src/ns_rnnoise computes, from the slot's 257-bin power.

RNNoise's band energies over triangles on the grid, their log with RNNoise's floors, an orthonormal DCT, a ring of the
last cepstra for the deltas and the spectral variability; band gains back to the 257 bins through the same triangles.
"""

from __future__ import annotations

import numpy as np

from srpipe.generated import grid

N_FEATURES_BASE = 1
LN10_OVER_10 = float(np.log(10.0) / 10.0)


def band_edges(bands_hz: list[float]) -> np.ndarray:
    """The band edges as bins of the grid, the last at the Nyquist bin."""
    step = grid.SAMPLE_RATE_HZ / grid.FFT_SIZE
    edges = np.round(np.asarray(bands_hz, dtype=np.float64) / step).astype(np.int64)
    if edges[0] != 0 or edges[-1] != grid.N_BINS - 1 or np.any(np.diff(edges) <= 0):
        raise ValueError(f"band edges {edges.tolist()} must rise from bin 0 to bin {grid.N_BINS - 1}")
    return edges


def triangles(bands_hz: list[float]) -> np.ndarray:
    """(bands, N_BINS) float32 weights: each bin splits between the two bands whose centres enclose it, as RNNoise's
    compute_band_energy; every column sums to one."""
    edges = band_edges(bands_hz)
    w = np.zeros((len(edges), grid.N_BINS), dtype=np.float64)
    for i in range(len(edges) - 1):
        span = edges[i + 1] - edges[i]
        for k in range(edges[i], edges[i + 1]):
            frac = (k - edges[i]) / span
            w[i, k] += 1.0 - frac
            w[i + 1, k] += frac
    w[-1, edges[-1]] += 1.0
    return w.astype(np.float32)


def dct_table(n: int) -> np.ndarray:
    """Orthonormal DCT-II, (n, n) float32, built in double and rounded once (KEHOACH 3.14)."""
    i = np.arange(n)[:, None]
    b = np.arange(n)[None, :]
    scale = np.where(i == 0, np.sqrt(1.0 / n), np.sqrt(2.0 / n))
    return (scale * np.cos(np.pi * i * (b + 0.5) / n)).astype(np.float32)


def n_features(spec: dict) -> int:
    """Features a hop: the cepstrum, a first and a second difference of its first delta_ceps, the variability."""
    return len(spec["bands_hz"]) + 2 * spec["delta_ceps"] + N_FEATURES_BASE


class Features:
    """One hop of power in, RNNoise-16k's features out; starts from a zero ring, as after dsp_afe_reset."""

    def __init__(self, spec: dict, power_floor: float) -> None:
        self.w = triangles(spec["bands_hz"])
        self.dct = dct_table(len(spec["bands_hz"]))
        self.delta = spec["delta_ceps"]
        self.floor = np.float32(power_floor)
        self.range = np.float32(spec["range_db"] * LN10_OVER_10)
        self.follow = np.float32(spec["follow_db"] * LN10_OVER_10)
        self.ring = np.zeros((spec["variability_hops"], len(spec["bands_hz"])), dtype=np.float32)

    def reset(self) -> None:
        self.ring[:] = 0.0

    def log_bands(self, power: np.ndarray) -> np.ndarray:
        """Log band energies with RNNoise's floors: within range of the loudest band under, falling at most follow."""
        energy = self.w @ np.asarray(power, dtype=np.float32)
        log = np.log(self.floor + energy).astype(np.float32)
        log_max = follow = np.float32(np.log(self.floor))
        for b in range(len(log)):
            log[b] = max(log_max - self.range, follow - self.follow, log[b])
            log_max = max(log_max, log[b])
            follow = max(follow - self.follow, log[b])
        return log

    def step(self, power: np.ndarray) -> np.ndarray:
        """float32 features of one hop of the slot's power (N_BINS)."""
        c0 = (self.dct @ self.log_bands(power)).astype(np.float32)
        self.ring = np.roll(self.ring, 1, axis=0)
        self.ring[0] = c0
        c1, c2 = self.ring[1], self.ring[2]
        d = self.delta
        out = np.concatenate(
            [c0[:d] + c1[:d] + c2[:d], c0[d:], c0[:d] - c2[:d], c0[:d] - 2.0 * c1[:d] + c2[:d], [self.variability()]]
        )
        return out.astype(np.float32)

    def variability(self) -> np.float32:
        """The mean over the ring of each cepstrum's least squared distance to another."""
        dist = np.sum((self.ring[:, None, :] - self.ring[None, :, :]) ** 2, axis=-1)
        np.fill_diagonal(dist, np.inf)
        return np.float32(np.mean(np.min(dist, axis=1)))


def band_gains_to_bins(band_gains: np.ndarray, w: np.ndarray) -> np.ndarray:
    """257 gains from the band gains through the triangles, as RNNoise's interp_band_gain."""
    return (np.asarray(band_gains, dtype=np.float32) @ w).astype(np.float32)
