"""srpipe.tasks.ns.eval: the whole-sequence iSTFT against the streaming one, the figures of known gains, the spans
the slices name, and the bench's recorded mixtures scored for the floor and every candidate at an epoch."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from srpipe.core.audio_io import write_wav
from srpipe.core.config import load_device, load_yaml
from srpipe.dsp.spec.stft import Istft
from srpipe.generated import grid
from srpipe.scenes import compare
from srpipe.tasks import ns

torch = pytest.importorskip("torch")

from srpipe.tasks.ns import eval as ns_eval  # noqa: E402
from srpipe.tasks.ns import model, train  # noqa: E402

HOP = grid.HOP_SAMPLES
FS = grid.SAMPLE_RATE_HZ


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


def test_the_bench_scores_the_floor_and_every_candidate_at_an_epoch_on_each_recorded_mixture(tmp_path: Path) -> None:
    paths = {"interim": tmp_path / "interim"}
    root = paths["interim"] / "scenes" / load_yaml(compare.CONFIG)["name"]
    mixes = [item["name"] for item in load_yaml(compare.CONFIG)["items"] if "mix" in item]
    rng = np.random.default_rng(2)
    t = np.arange(6 * FS) / FS
    voiced = (t % 1.5 > 0.5) * 0.2 * np.sin(2 * np.pi * 180 * t)
    for name in mixes:
        clean = np.stack([voiced, voiced], axis=1)
        write_wav(root / name / "clean.wav", clean)
        write_wav(root / name / "input.wav", clean + 0.02 * rng.standard_normal(clean.shape))
        spans = [[k + 0.5, k + 1.5] for k in np.arange(0.0, 6.0, 1.5).tolist()]
        meta = {"name": name, "source": {"mix": {"snr_db": 5.0}}, "segments": {"speech_s": spans}}
        (root / name / "item.json").write_text(json.dumps(meta), encoding="utf-8")
    cfg = load_yaml(ns.CONFIG)
    cfg["train"]["candidates"] = model.names(cfg)
    run = tmp_path / "run"
    for name in model.names(cfg):
        train.checkpoint(run, name, 3).parent.mkdir(parents=True)
        torch.save(model.build(cfg, name).state_dict(), train.checkpoint(run, name, 3))
    summary = ns_eval.bench(run, cfg, load_device(cfg["device"]), paths, 3)
    floors = [ns_eval.floor_name(f) for f in cfg["eval"]["floors_db"]]
    assert set(summary) == {ns_eval.OMLSA} | {f"{n}@{f}" for n in model.names(cfg) for f in floors}
    omlsa = summary[ns_eval.OMLSA]["settled"]["class"]
    assert set(omlsa) == set(mixes) and all(omlsa[name]["noise_down_db"] > 3.0 for name in mixes)
    assert (run / "eval" / "bench_epoch_03.yaml").exists()
