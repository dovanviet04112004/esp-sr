"""srpipe.tasks.ns.postproc: RNNoise-16k's triangles, gain spread, streaming features and variability; NSNet-16k's log
power and Nyquist gain; the gain floor and the speech share; against brute force and the batched torch features."""

from __future__ import annotations

import numpy as np
import pytest
import torch

from srpipe.core.config import load_yaml
from srpipe.generated import grid
from srpipe.tasks import ns
from srpipe.tasks.ns.model import rnnoise
from srpipe.tasks.ns.postproc import bands, bins

CFG = load_yaml(ns.CONFIG)
SPEC = CFG["rnnoise"]


def slot_power(hops: int, seed: int) -> np.ndarray:
    """Seeded powers of a tilted spectrum at varying levels, with a near-silent stretch and digital silence."""
    rng = np.random.default_rng(seed)
    level = rng.uniform(-9.0, -2.0, size=(hops, 1))
    level[20:40] = -12.0
    p = 10.0 ** (level + np.linspace(0.0, -4.0, grid.N_BINS) + rng.normal(0.0, 0.5, size=(hops, grid.N_BINS)))
    p[50:55] = 0.0
    return p.astype(np.float32)


def test_band_edges_follow_the_grid_and_the_triangles_sum_to_one() -> None:
    edges = bands.band_edges(SPEC["bands_hz"])
    assert edges[0] == 0 and edges[-1] == grid.N_BINS - 1
    w = bands.triangles(SPEC["bands_hz"])
    assert w.shape == (len(SPEC["bands_hz"]), grid.N_BINS)
    np.testing.assert_allclose(w.sum(axis=0), 1.0, rtol=0.0, atol=1e-6)
    assert all(w[b, e] == 1.0 for b, e in enumerate(edges))
    with pytest.raises(ValueError):
        bands.band_edges([0, 400, 200, 8000])


def test_band_gains_interpolate_through_the_triangles_of_the_band_energies() -> None:
    g = np.random.default_rng(2).uniform(0.0, 1.0, len(SPEC["bands_hz"])).astype(np.float32)
    out = bands.band_gains_to_bins(g, bands.triangles(SPEC["bands_hz"]))
    edges = bands.band_edges(SPEC["bands_hz"])
    np.testing.assert_allclose(out[edges], g, rtol=0.0, atol=1e-7)
    for b in range(len(edges) - 1):
        k = np.arange(edges[b], edges[b + 1] + 1)
        np.testing.assert_allclose(out[k], np.interp(k, edges[b : b + 2], g[b : b + 2]), rtol=0.0, atol=1e-6)


def test_the_streaming_rnnoise_features_equal_the_batched_ones_from_a_reset() -> None:
    power = slot_power(300, 1)
    stream = bands.Features(SPEC, CFG["power_floor"])
    hop_by_hop = np.stack([stream.step(p) for p in power])
    assert hop_by_hop.shape == (300, bands.n_features(SPEC)) and np.all(np.isfinite(hop_by_hop))
    batched = rnnoise.Features(SPEC, CFG["power_floor"])(torch.from_numpy(power)[None])[0].numpy()
    np.testing.assert_allclose(batched, hop_by_hop, rtol=1e-5, atol=1e-4)
    stream.reset()
    np.testing.assert_array_equal(stream.step(power[0]), hop_by_hop[0])


def test_spectral_variability_matches_a_brute_force_over_the_ring() -> None:
    stream = bands.Features(SPEC, CFG["power_floor"])
    for p in slot_power(20, 3):
        stream.step(p)
    ring = stream.ring.astype(np.float64)
    n = len(ring)
    nearest = [min(np.sum((ring[i] - ring[j]) ** 2) for j in range(n) if j != i) for i in range(n)]
    assert float(stream.variability()) == pytest.approx(float(np.mean(nearest)), rel=1e-5)


def test_log_power_features_floor_silence_and_bin_256_takes_bin_255s_gain() -> None:
    n = CFG["nsnet"]["bins"]
    feats = bins.log_power(np.zeros(grid.N_BINS, dtype=np.float32), n, CFG["power_floor"])
    assert feats.shape == (n,) and np.all(feats == np.log(np.float32(CFG["power_floor"])))
    gains = np.linspace(0.0, 1.0, n, dtype=np.float32)
    slot = bins.to_slot(gains)
    assert slot.shape == (grid.N_BINS,) and np.array_equal(slot[:n], gains) and np.all(slot[n:] == gains[-1])


def test_the_gain_floor_lifts_only_the_gains_under_it() -> None:
    g = np.array([0.0, 0.1, 0.25, 0.5, 1.0], dtype=np.float32)
    floor = np.float32(10.0 ** (-12.0 / 20.0))
    out = bins.floored(g, -12.0)
    assert np.array_equal(out, [floor, floor, floor, 0.5, 1.0])
    assert np.array_equal(bins.floored(g, None), g)


def test_nsnet_speech_prob_is_the_share_of_band_power_passed() -> None:
    band = CFG["nsnet"]["speech_prob_band_hz"]
    power = np.ones((2, grid.N_BINS), dtype=np.float32)
    power[1] = 0.0
    half = np.full((2, grid.N_BINS), 0.5, dtype=np.float32)
    np.testing.assert_allclose(bins.speech_prob(half, power, band), [0.25, 0.0])
    step = grid.SAMPLE_RATE_HZ / grid.FFT_SIZE
    outside = np.ones(grid.N_BINS, dtype=bool)
    outside[round(band[0] / step) : round(band[1] / step) + 1] = False
    muted = half[0].copy()
    muted[outside] = 0.0
    assert bins.speech_prob(muted, power[0], band) == pytest.approx(0.25)
