"""NSNet-16k's features and gain mapping (KEHOACH 3.9, ADR-0014): the float32 mirror of what ai_engine/src/ns_nsnet
computes around the net, from the slot's 257-bin power: the log power of the first bins in, a gain a bin out; and the
gain floor both candidates' 257 gains pass through."""

from __future__ import annotations

import numpy as np

from srpipe.generated import grid


def log_power(power: np.ndarray, bins: int, power_floor: float) -> np.ndarray:
    """float32 log power of the first bins (0 .. bins - 1) of a hop, floored at power_floor."""
    return np.log(np.asarray(power, dtype=np.float32)[..., :bins] + np.float32(power_floor)).astype(np.float32)


def to_slot(gains: np.ndarray) -> np.ndarray:
    """The net's gains of the first bins widened to N_BINS: every bin past them takes the last one's gain."""
    g = np.asarray(gains, dtype=np.float32)
    tail = np.repeat(g[..., -1:], grid.N_BINS - g.shape[-1], axis=-1)
    return np.concatenate([g, tail], axis=-1)


def floored(gains: np.ndarray, floor_db: float | None) -> np.ndarray:
    """The gains the slot applies: none under floor_db, as OM-LSA's G_min; None leaves them as they are."""
    g = np.asarray(gains, dtype=np.float32)
    if floor_db is None:
        return g
    return np.maximum(g, np.float32(10.0 ** (floor_db / 20.0)))


def speech_prob(gains: np.ndarray, power: np.ndarray, band_hz: list[float]) -> np.ndarray:
    """The share of the band's power the gains pass, a hop at a time; 0 for a silent band."""
    step = grid.SAMPLE_RATE_HZ / grid.FFT_SIZE
    low, high = (round(f / step) for f in band_hz)
    p = np.asarray(power, dtype=np.float32)[..., low : high + 1]
    g = np.asarray(gains, dtype=np.float32)[..., low : high + 1]
    total = p.sum(axis=-1)
    passed = (g * g * p).sum(axis=-1)
    return np.where(total > 0, passed / np.maximum(total, np.finfo(np.float32).tiny), 0.0).astype(np.float32)
