"""Gate 3 of the command tracks: which board sessions count, what each should get, where its windows sit, and how the
table scores them."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("torch")

from srpipe.core import corpus
from srpipe.core.config import CONFIGS, load_yaml
from srpipe.dsp.spec.pitch import N_FEATURES, PitchConfig, PitchTracker
from srpipe.generated import grid
from srpipe.tasks.command import ctc
from srpipe.tasks.command import eval as gate
from srpipe.tasks.command.ctc.model import encoder
from srpipe.tasks.command.ctc.postproc import ctc_score
from srpipe.tasks.command.eval import REJECT, Scored
from srpipe.tasks.command.rnnt.postproc import rnnt_search

SPEC = {"pcm_shift": 13, "kinds": ["cmd", "neg"], "left_out": ["s3"]}


def test_only_sessions_of_the_products_shift_and_a_scored_kind_count() -> None:
    row = {"pcm_shift": "13", "kind": "cmd", "session": "s1"}
    assert gate.counted(row, SPEC)
    assert not gate.counted(row | {"pcm_shift": "16"}, SPEC)
    assert not gate.counted(row | {"kind": "wake"}, SPEC)
    assert not gate.counted(row | {"session": "s3"}, SPEC)


def test_a_command_session_expects_its_command_only_when_the_net_learned_it() -> None:
    command_of = {tuple(corpus.sounds("bật đèn")): "bat_den"}
    assert gate.expected_of("cmd", "bật đèn", command_of) == "bat_den"
    assert gate.expected_of("cmd", "mở cửa", command_of) == REJECT
    assert gate.expected_of("neg", "bật đèn", command_of) == REJECT


def test_a_window_ends_where_vad_turns_off_and_repeats_the_first_hop_before_the_session() -> None:
    hops, window, lead, bands = 200, 94, 100, 40
    features = np.arange(hops, dtype=np.float32)[:, None].repeat(bands, axis=1)
    clean = np.zeros(hops * grid.HOP_SAMPLES, dtype=np.int16)
    tracker = PitchTracker(PitchConfig(**load_yaml(CONFIGS / "scenes" / "device.yaml")["pitch"]))
    x = gate.windows(clean, features, [(120, 150), (10, 30)], window, lead, tracker)
    assert x.shape == (2, window, bands + N_FEATURES)
    assert x[0, -1, 0] == 151 and x[0, 0, 0] == 151 - window + 1
    assert x[1, -1, 0] == 31 and np.all(x[1, : window - 31, 0] == 0)


def test_the_table_scores_each_command_and_the_rejections_by_kind() -> None:
    results = [
        Scored("a", "cmd", "100", "bật đèn", "bat_den", [("bat_den", 900), ("bat_den", 800), (REJECT, 400)]),
        Scored("b", "cmd", "100", "mở cửa", REJECT, [(REJECT, 300), ("bat_den", 700)]),
        Scored("c", "neg", "100", "bật điện | tắt điện", REJECT, [(REJECT, 500)]),
    ]
    text = gate.table(results, ["bat_den", "other", "silence"], {"command_recall": 0.9, "rejection": 0.95})
    assert "- bat_den: 2/3 right (fail at 90%)" in text
    assert "- rejected: 2/3 (fail at 95%)" in text and "  - cmd: 1/2" in text and "  - neg: 1/1" in text
    assert "bật điện \\| tắt điện" in text


def test_a_ctc_window_reaches_the_longest_back_but_never_into_the_utterance_ahead() -> None:
    hops, bands = 400, 40
    features = np.arange(hops, dtype=np.float32)[:, None].repeat(bands, axis=1)
    clean = np.zeros(hops * grid.HOP_SAMPLES, dtype=np.int16)
    tracker = PitchTracker(PitchConfig(**load_yaml(CONFIGS / "scenes" / "device.yaml")["pitch"]))
    xs = gate.ctc_windows(clean, features, [(30, 60), (70, 100), (200, 390)], 120, tracker)
    dims = bands + N_FEATURES
    assert [(x[0, 0], x[-1, 0], x.shape[1]) for x in xs] == [(0, 61, dims), (62, 101, dims), (272, 391, dims)]


def test_a_ctc_window_reaches_the_decision_as_frames_of_its_own_hops(monkeypatch) -> None:
    torch = pytest.importorskip("torch")
    cfg = load_yaml(ctc.CONFIG)
    torch.manual_seed(0)
    dims = encoder.n_dims(cfg)
    lexicon = [[np.array([0, 1], np.uint8)], [np.array([2], np.uint8)]]
    net = gate.Ctc(
        encoder.build(cfg).eval(), np.zeros(dims, np.float32), np.ones(dims, np.float32), ["a", "b"], lexicon, cfg
    )
    seen, decide = [], ctc_score.decide
    monkeypatch.setattr(
        ctc_score, "decide", lambda log_probs, *rest: seen.append(log_probs.shape) or decide(log_probs, *rest)
    )
    heard = gate.ctc_heard(net, np.random.default_rng(0).normal(size=(50, dims)).astype(np.float32))
    assert seen == [(encoder.n_classes(), 25)] and heard.command in ("a", "b") and 0 <= heard.gap <= ctc_score.CAP


def test_an_rnnt_window_reaches_the_search_as_frames_of_its_own_hops(monkeypatch) -> None:
    torch = pytest.importorskip("torch")
    cfg = load_yaml(ctc.CONFIG)
    torch.manual_seed(0)
    dims = encoder.n_dims(cfg)
    lexicon = [[np.array([0, 1], np.uint8)], [np.array([2], np.uint8)]]
    stats = (np.zeros(dims, np.float32), np.ones(dims, np.float32))
    net = gate.Ctc(encoder.build(cfg).eval(), *stats, ["a", "b"], lexicon, cfg)
    seen, decide = [], rnnt_search.decide

    def watched(log_probs, frames, *rest):
        pad = net.model.transducer.predictor.pad
        seen.append((log_probs(0, (pad, ctc_score.BLANK)).shape, frames))
        return decide(log_probs, frames, *rest)

    monkeypatch.setattr(rnnt_search, "decide", watched)
    x = np.random.default_rng(0).normal(size=(50, dims)).astype(np.float32)
    heard = gate.rnnt_heard(net, x)
    assert seen == [((encoder.n_classes(),), 25)] and heard.command in ("a", "b", REJECT)
    plain = gate.Ctc(encoder.build(cfg | {"rnnt": None}).eval(), *stats, ["a", "b"], lexicon, cfg)
    with pytest.raises(ValueError, match="no transducer"):
        gate.rnnt_heard(plain, x)


def test_the_ctc_table_counts_best_commands_and_sweeps_the_reject_threshold() -> None:
    h = gate.Heard
    results = [
        Scored(
            "a",
            "cmd",
            "100",
            "bật đèn",
            "bat_den",
            [h("bat_den", 700, 300, 100), h("bat_den", 600, 20, 200), h("tat_den", 500, 100, 150)],
        ),
        Scored("b", "neg", "100", "bật điện", REJECT, [h("bat_den", 400, 100, 500), h(REJECT, 0, 65535, 65535)]),
        Scored("c", "noise", "", "quạt", REJECT, [h("tat_den", 300, 200, 900)]),
    ]
    spec, sweep = {"command_recall": 0.9, "rejection": 0.95}, {"margin": 50, "reject_sweep": [150, 600]}
    text = gate.ctc_table(results, ["bat_den", "tat_den"], spec, sweep)
    assert "- bat_den: best 2/3" in text and "- tat_den: best 0/0" in text
    assert "| 150 | 33% | 33% | 3/3 | 2/2 | 1/1 |" in text
    assert "| 600 | 33% | 33% | 2/3 | 1/2 | 1/1 |" in text
