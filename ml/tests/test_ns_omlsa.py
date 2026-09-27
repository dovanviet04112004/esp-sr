"""Check srpipe.dsp.afe.ns_omlsa: its own exp and log, the E1 table, and what OM-LSA with IMCRA must do to noise."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.special import exp1

from srpipe.dsp.afe import ns_omlsa
from srpipe.dsp.spec.stft import Stft
from srpipe.generated import afe, grid

HOPS_SETTLED = 200
HOPS = 400
NOISE_RMS = 0.01


def powers(x: np.ndarray) -> list[np.ndarray]:
    st = Stft()
    out = []
    for k in range(0, len(x) - grid.HOP_SAMPLES + 1, grid.HOP_SAMPLES):
        b = st.analyze(x[k : k + grid.HOP_SAMPLES].astype(np.float32))
        out.append((b.real * b.real + b.imag * b.imag).astype(np.float32))
    return out


def white(rng: np.random.Generator, hops: int, rms: float = NOISE_RMS) -> np.ndarray:
    return (rms * rng.standard_normal(hops * grid.HOP_SAMPLES)).astype(np.float32)


def test_log2_and_exp2_are_within_a_few_float32_steps() -> None:
    x = np.float32(10.0) ** np.linspace(-30, 30, 20001).astype(np.float32)
    want = np.log2(x.astype(np.float64))
    assert np.max(np.abs(ns_omlsa.log2_f32(x) - want) / np.maximum(1.0, np.abs(want))) < 2e-7
    y = np.linspace(-120, 120, 20001).astype(np.float32)
    assert np.max(np.abs(ns_omlsa.exp2_f32(y) / np.exp2(y.astype(np.float64)) - 1.0)) < 2e-7
    assert ns_omlsa.exp2_f32(np.float32(-200.0)) == 0.0


def test_reciprocal_and_reciprocal_root_are_within_a_few_float32_steps() -> None:
    b = np.float32(10.0) ** np.linspace(-12, 12, 20001).astype(np.float32)
    exact = b.astype(np.float64)
    assert np.max(np.abs(ns_omlsa.recip_f32(b) * exact - 1.0)) < 3e-7
    assert np.max(np.abs(ns_omlsa.rsqrt_f32(b) * np.sqrt(exact) - 1.0)) < 3e-7


def test_the_e1_table_is_the_smooth_part_of_e1() -> None:
    table = ns_omlsa.e1_table()
    v = np.linspace(0.0, afe.NS_LSA_V_MAX, afe.NS_E1_POINTS)[1:]
    want = np.exp(0.5 * (exp1(v) + np.log(v)))
    assert np.max(np.abs(table[1:] / want - 1.0)) < 1e-6
    assert table[0] == pytest.approx(math.exp(-0.5 * ns_omlsa.EULER_GAMMA), rel=1e-6)


def test_the_lsa_gain_between_table_points_stays_within_1e5() -> None:
    om = ns_omlsa.Omlsa()
    v = np.linspace(1e-4, afe.NS_LSA_V_MAX, 5001).astype(np.float32)
    wiener = np.full_like(v, 0.5)
    want = 0.5 * np.exp(0.5 * exp1(v.astype(np.float64)))
    assert np.max(np.abs(om._lsa_gain(wiener, v) / want - 1.0)) < 1e-5


def test_nothing_happens_until_the_first_hop_with_power() -> None:
    om = ns_omlsa.Omlsa()
    out = om.process(np.zeros(grid.N_BINS, np.float32))
    assert not om.started and np.all(out.gain == 1.0) and out.speech_prob == 0.0
    om.process(np.full(grid.N_BINS, 1e-3, np.float32))
    assert om.started


def test_stationary_noise_settles_at_the_gain_floor_with_its_level_tracked() -> None:
    p = powers(white(np.random.default_rng(1), HOPS))
    om = ns_omlsa.Omlsa()
    hops = [om.process(q) for q in p]
    gain_db = 20 * np.log10(np.mean([h.gain for h in hops[HOPS_SETTLED:]]))
    assert gain_db == pytest.approx(afe.NS_FLOOR_DB, abs=0.5)
    expected = np.mean(np.array(p[HOPS_SETTLED:])[:, 10:-10])
    noise = np.mean(np.array([h.noise for h in hops[HOPS_SETTLED:]])[:, 10:-10])
    assert 10 * np.log10(noise / expected) == pytest.approx(0.0, abs=2.0)


def test_a_lower_floor_suppresses_further() -> None:
    om = ns_omlsa.Omlsa()
    om.set_floor(-6.0)
    hops = [om.process(p) for p in powers(white(np.random.default_rng(3), HOPS))]
    assert 20 * np.log10(np.mean([h.gain for h in hops[HOPS_SETTLED:]])) == pytest.approx(-6.0, abs=0.5)


def test_a_short_tone_over_noise_keeps_its_bin_and_the_rest_stays_down() -> None:
    x = white(np.random.default_rng(4), HOPS)
    t = np.arange(len(x)) / grid.SAMPLE_RATE_HZ
    tone_bin, start, stop = 64, HOPS_SETTLED, HOPS_SETTLED + 50
    tone = 0.1 * np.sin(2 * np.pi * tone_bin * grid.SAMPLE_RATE_HZ / grid.FFT_SIZE * t)
    x[start * grid.HOP_SAMPLES : stop * grid.HOP_SAMPLES] += tone[start * grid.HOP_SAMPLES : stop * grid.HOP_SAMPLES]
    om = ns_omlsa.Omlsa()
    gains = np.array([om.process(p).gain for p in powers(x)])
    assert np.mean(gains[start + 5 : stop, tone_bin]) > 0.9
    assert 20 * np.log10(np.mean(gains[start + 5 : stop, 150:250])) < afe.NS_FLOOR_DB + 1.0


def test_the_noise_estimate_follows_a_rise_of_20_db_within_two_minimum_windows() -> None:
    rng = np.random.default_rng(5)
    x = np.concatenate([white(rng, HOPS), white(rng, HOPS, NOISE_RMS * 10)])
    om = ns_omlsa.Omlsa()
    noise = [np.median(om.process(p).noise[10:-10]) for p in powers(x)]
    window_hops = afe.NS_MIN_SUBWINDOWS * om.subwindow_hops
    # The coarse minimum needs one window to forget the old level and the second pass one more (Cohen 2003).
    assert 10 * np.log10(noise[HOPS + window_hops] / noise[HOPS - 1]) < 3.0
    assert 10 * np.log10(noise[HOPS + 3 * window_hops] / noise[HOPS - 1]) == pytest.approx(20.0, abs=3.0)


def test_residual_echo_adds_to_the_noise_and_is_suppressed() -> None:
    rng = np.random.default_rng(6)
    p = powers(white(rng, HOPS) + white(rng, HOPS, 0.05))
    echo = np.array(powers(white(np.random.default_rng(6), HOPS, 0.05)))
    plain, with_echo = ns_omlsa.Omlsa(), ns_omlsa.Omlsa()
    g_plain = np.mean([plain.process(q).gain for q in p[HOPS_SETTLED:]])
    g_echo = np.mean([with_echo.process(q, e).gain for q, e in zip(p, echo, strict=True)][HOPS_SETTLED:])
    assert g_echo < g_plain


def test_gains_stay_within_0_and_1() -> None:
    rng = np.random.default_rng(7)
    om = ns_omlsa.Omlsa()
    for p in powers(white(rng, HOPS) * np.repeat(rng.uniform(0.01, 3.0, HOPS), grid.HOP_SAMPLES).astype(np.float32)):
        g = om.process(p).gain
        assert np.all((g >= 0.0) & (g <= 1.0))


def test_reset_gives_the_same_gains_as_a_new_instance() -> None:
    p = powers(white(np.random.default_rng(8), 60))
    om = ns_omlsa.Omlsa()
    for q in p:
        om.process(q)
    om.reset()
    again = [om.process(q).gain for q in p]
    fresh = [ns_omlsa.Omlsa().process(q).gain for q in p[:1]]
    np.testing.assert_array_equal(again[0], fresh[0])
    new = ns_omlsa.Omlsa()
    np.testing.assert_array_equal(np.array(again), np.array([new.process(q).gain for q in p]))


def test_the_paper_framing_gives_the_paper_constants() -> None:
    om = ns_omlsa.Omlsa(ns_omlsa.OmlsaConfig(hop_s=0.008, min_subwindow_s=0.12))
    assert (om.alpha_s, om.alpha_d, om.alpha_xi, om.alpha_eta) == pytest.approx((0.9, 0.85, 0.7, 0.95), abs=1e-7)
    assert om.subwindow_hops == 15
    grid_om = ns_omlsa.Omlsa()
    assert grid_om.alpha_s == pytest.approx(0.81, abs=1e-7) and grid_om.subwindow_hops == 8


def test_a_positive_floor_is_refused() -> None:
    with pytest.raises(ValueError):
        ns_omlsa.Omlsa(ns_omlsa.OmlsaConfig(floor_db=3.0))
