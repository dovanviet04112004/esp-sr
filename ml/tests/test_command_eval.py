"""Gate 3 of the command tracks: which board sessions count, what each should get, where its windows sit, how the
table scores them, and the command set a ctc run is scored on."""

from __future__ import annotations

import json

import numpy as np
import pytest
import soundfile as sf
import yaml

pytest.importorskip("torch")

from srpipe.core import corpus
from srpipe.core.audio_io import to_float
from srpipe.core.config import contract_front, device_of, load_yaml
from srpipe.dsp.afe.chain import ChainConfig
from srpipe.dsp.spec.mel import Mel, MelConfig
from srpipe.dsp.spec.pitch import N_FEATURES
from srpipe.dsp.spec.stft import Stft
from srpipe.generated import grid, listen
from srpipe.scenes import device
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


def test_a_session_of_the_manifest_missing_on_disk_is_refused_not_skipped(tmp_path) -> None:
    (tmp_path / "device" / "board_b" / "s1").mkdir(parents=True)
    rows = [{"board": "board_b", "fw": "x", "pcm_shift": "13", "session": s} for s in ("s1", "s2")]
    with pytest.raises(FileNotFoundError, match="s2"):
        next(gate.heard_rows({"features": "scenes/device.yaml"}, rows[:1], {"raw": tmp_path}, rows))
    with pytest.raises(ValueError, match="s9"):
        next(
            gate.heard_rows({"features": "scenes/device.yaml"}, [rows[0] | {"session": "s9"}], {"raw": tmp_path}, rows)
        )


def test_the_sessions_of_a_sitting_run_through_one_chain_and_one_tracker_without_a_reset(tmp_path) -> None:
    hop, rng = grid.HOP_SAMPLES, np.random.default_rng(3)
    a, b = (rng.normal(0.0, 800.0, (n * hop, 2)).astype(np.int16) for n in (60, 40))
    for name, x in (("s1", a), ("s2", b), ("s3", b)):
        (tmp_path / "device" / "board_b" / name).mkdir(parents=True)
        for m in range(2):
            sf.write(tmp_path / "device" / "board_b" / name / f"ch{m}.wav", x[:, m], grid.SAMPLE_RATE_HZ)
    manifest = [
        {"session": s, "board": "board_b", "fw": fw, "pcm_shift": "13"}
        for s, fw in (("s1", "x"), ("s2", "x"), ("s3", "y"))
    ]
    cfg = {"features": "scenes/device.yaml"}
    (_, second, *_), (_, other, *_) = gate.heard_rows(cfg, manifest[1:], {"raw": tmp_path}, manifest)
    device_cfg = device_of(cfg)
    chain_cfg = ChainConfig(balance_gains=device.load_microphones(device_cfg["microphone"]).gains)
    mel = Mel(MelConfig(**device_cfg["features"]))
    together, alone = device.listen(np.concatenate([a, b]), chain_cfg, mel)[0], device.listen(b, chain_cfg, mel)[0]
    np.testing.assert_array_equal(second[: len(b)], together[len(a) :])
    np.testing.assert_array_equal(other[: len(b)], alone)
    assert not np.array_equal(together[len(a) :], alone)


def test_a_command_session_expects_its_command_only_when_the_net_learned_it() -> None:
    command_of = {tuple(corpus.sounds("bật đèn")): "bat_den"}
    assert gate.expected_of("cmd", "bật đèn", command_of) == "bat_den"
    assert gate.expected_of("cmd", "mở cửa", command_of) == REJECT
    assert gate.expected_of("neg", "bật đèn", command_of) == REJECT


def test_a_window_ends_where_vad_turns_off_and_repeats_the_first_hop_before_the_session() -> None:
    hops, window, lead, bands = 200, 94, 100, 40
    features = np.arange(hops, dtype=np.float32)[:, None].repeat(bands, axis=1)
    pitch = np.arange(hops * N_FEATURES, dtype=np.float32).reshape(hops, N_FEATURES)
    x = gate.windows(features, pitch, [(120, 150), (10, 30)], window, lead)
    assert x.shape == (2, window, bands + N_FEATURES)
    assert x[0, -1, 0] == 151 and x[0, 0, 0] == 151 - window + 1
    assert x[1, -1, 0] == 31 and np.all(x[1, : window - 31, 0] == 0)
    np.testing.assert_array_equal(x[0, -1, bands:], pitch[151])


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


def test_a_command_window_opens_its_lead_ahead_never_into_the_one_before_and_a_long_one_reaches_back() -> None:
    lead, longest = listen.UTTERANCE_LEAD_HOPS, listen.WINDOW_HOPS
    spans = [(30, 60), (70, 100), (lead + 120, lead + 180), (lead + 220, lead + 250 + longest)]
    expected = [(0, 61), (62, 101), (120, lead + 181), (lead + 252, lead + 251 + longest)]
    assert device.command_cut(spans) == expected
    hops, bands = lead + longest + 300, 40
    features = np.arange(hops, dtype=np.float32)[:, None].repeat(bands, axis=1)
    pitch = np.arange(hops * N_FEATURES, dtype=np.float32).reshape(hops, N_FEATURES)
    xs = gate.ctc_windows(features, pitch, spans)
    cut = [(int(x[0, 0]), int(x[-1, 0])) for x in xs]
    assert cut == device.command_cut(spans) and {x.shape[1] for x in xs} == {bands + N_FEATURES}
    assert all(np.array_equal(x[:, bands:], pitch[a : b + 1]) for x, (a, b) in zip(xs, cut, strict=True))


def test_a_session_closes_on_silence_whose_log_mel_goes_on_from_its_last_hop() -> None:
    hop, rng = grid.HOP_SAMPLES, np.random.default_rng(1)
    clean = rng.integers(-3000, 3000, 20 * hop).astype(np.int16)
    vad = np.zeros(20, dtype=bool)
    vad[-1] = True
    mel = Mel(MelConfig(**listen.FEATURES))
    stft = Stft()
    features = np.stack([mel.log(stft.analyze(to_float(h))) for h in clean.reshape(-1, hop)]).astype(np.float32)
    on_clean, on_vad, on_features = gate.with_silence(clean, vad, features, mel)
    tail = listen.UTTERANCE_GAP_HOPS + 1
    assert len(on_clean) == (20 + tail) * hop and not on_clean[20 * hop :].any() and not on_vad[20:].any()
    assert np.array_equal(on_features[:20], features)
    went_on = np.stack([mel.log(stft.analyze(np.zeros(hop, np.float32))) for _ in range(tail)])
    assert np.array_equal(on_features[20:], went_on.astype(np.float32))
    assert device.command_cut([(19 - listen.UTTERANCE_MIN_HOPS, 19)])[-1][1] == 20 < len(on_vad)


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
        seen.append((log_probs(0, [(pad, ctc_score.BLANK)]).shape, frames))
        return decide(log_probs, frames, *rest)

    monkeypatch.setattr(rnnt_search, "decide", watched)
    x = np.random.default_rng(0).normal(size=(50, dims)).astype(np.float32)
    heard = gate.rnnt_heard(net, x)
    assert seen == [((1, encoder.n_classes()), 25)] and heard.command in ("a", "b", REJECT)
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


def test_a_ctc_run_scores_the_command_set_it_is_given(tmp_path) -> None:
    torch = pytest.importorskip("torch")
    cfg = load_yaml(ctc.CONFIG) | {"listen": contract_front(), "listen_hash": listen.HASH}
    (tmp_path / "config.resolved.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    torch.save(encoder.build(cfg).state_dict(), tmp_path / "model.pt")
    dims = encoder.n_dims(cfg)
    np.savez(tmp_path / "feature_stats.npz", mean=np.zeros(dims, np.float32), std=np.ones(dims, np.float32))
    given = tmp_path / "set.json"
    given.write_text(
        json.dumps({"version": 1, "commands": [{"id": "a", "text": "bật ti vi"}, {"id": "b", "text": "tắt ti vi"}]})
    )
    net = gate.load_ctc(tmp_path, given)
    assert net.names == ["a", "b"] and len(net.lexicon) == 2
    assert gate.load_ctc(tmp_path).names[0] == "bat_den"
