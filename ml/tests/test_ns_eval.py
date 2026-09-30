"""srpipe.tasks.ns.eval: the whole-sequence iSTFT against the streaming one, the figures of known gains, and the
spans the slices name."""

from __future__ import annotations

import math

import numpy as np
import pytest

from srpipe.dsp.spec.stft import Istft
from srpipe.generated import grid
from srpipe.tasks.ns import eval as ns_eval

HOP = grid.HOP_SAMPLES


def test_the_whole_sequence_istft_equals_the_streaming_one() -> None:
    rng = np.random.default_rng(0)
    bins = (rng.standard_normal((40, grid.N_BINS)) + 1j * rng.standard_normal((40, grid.N_BINS))).astype(np.complex64)
    stream = Istft()
    np.testing.assert_array_equal(ns_eval.synthesis(bins), np.concatenate([stream.synthesize(b) for b in bins]))


def test_the_figures_of_known_gains() -> None:
    rng = np.random.default_rng(1)
    hops = 50
    speech, noise = rng.standard_normal(hops * HOP), rng.standard_normal(hops * HOP)
    labels = np.zeros(hops, dtype=np.uint8)
    labels[10:30] = 1
    after = np.arange(hops) >= 5
    refs = {"speech_ref": speech, "noise_ref": noise}
    flat = ns_eval.window_figures(refs | {"speech": 0.5 * speech, "noise": 0.5 * noise}, labels, after)
    assert flat["noise_down_db"] == pytest.approx(20 * math.log10(2.0))
    assert flat["speech_down_db"] == pytest.approx(20 * math.log10(2.0))
    assert flat["snr_gain_db"] == pytest.approx(0.0, abs=1e-9)
    assert flat["si_sdr_gain_db"] == pytest.approx(0.0, abs=1e-9)
    cut = ns_eval.window_figures(refs | {"speech": speech, "noise": 0.1 * noise}, labels, after)
    assert cut["snr_gain_db"] == pytest.approx(20.0) and cut["speech_down_db"] == pytest.approx(0.0, abs=1e-9)
    assert cut["si_sdr_gain_db"] > 10.0
    silent = ns_eval.window_figures(refs | {"speech": speech, "noise": noise}, np.zeros(hops, dtype=np.uint8), after)
    assert math.isnan(silent["speech_down_db"]) and math.isnan(silent["si_sdr_gain_db"])


def test_buckets_name_the_span_that_holds_a_value() -> None:
    edges = [-5.0, 0.0, 5.0]
    assert ns_eval.bucket(-7.0, edges) == "< -5"
    assert ns_eval.bucket(-5.0, edges) == "[-5, 0)"
    assert ns_eval.bucket(4.9, edges) == "[0, 5)"
    assert ns_eval.bucket(5.0, edges) == ">= 5"
    assert ns_eval.bucket(None, edges) is None
