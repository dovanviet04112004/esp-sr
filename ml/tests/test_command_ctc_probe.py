"""The ctc probe's command windows: random windows of the lengths the board must handle, and records whose head,
lexicon and windows read back, each window's decision the one ctc_score takes on its int8 logits; the Gate 3 record
keeps each window's int8 input, Gate 3 is counted on the decisions the chip prints, and no listen round is made of a
locked command of another listen.yaml."""

from __future__ import annotations

import json
import struct
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("torch")

import torch

from srpipe.core.config import load_yaml
from srpipe.generated import grid, listen
from srpipe.tasks.command import ctc
from srpipe.tasks.command import eval as gate
from srpipe.tasks.command.ctc import probe, quant
from srpipe.tasks.command.ctc.model import encoder
from srpipe.tasks.command.ctc.postproc import ctc_score

DIMS = encoder.n_dims(load_yaml(ctc.CONFIG))
EXPONENT = -3
INPUT_EXPONENT = -3


class DrawnLogits:
    """Stands for quant.Int8Net: fresh int8 logits on the grid of 2^EXPONENT for each window, from a fixed seed."""

    def __init__(self, graph, hops: int, mean: np.ndarray, std: np.ndarray, like, patches: list[str]) -> None:
        self.io = SimpleNamespace(input_exponent=INPUT_EXPONENT, output_exponent=EXPONENT)
        self.frames = hops // like.front.hop_stride
        self.rng = np.random.default_rng(7)

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        q = self.rng.integers(-60, 61, (encoder.n_classes(), self.frames))
        return torch.from_numpy((q * 2.0**EXPONENT).astype(np.float32))[None]


def test_random_windows_hold_the_longest_one_hop_and_one_chunk() -> None:
    cfg = load_yaml(ctc.CONFIG)
    windows = probe.random_windows(cfg, np.zeros(DIMS, np.float32), np.ones(DIMS, np.float32))
    longest = listen.WINDOW_HOPS
    assert [len(w) for w in windows[:3]] == [longest, 1, cfg["chunk_hops"]]
    assert len(windows) == cfg["probe"]["command"]["windows"]
    assert all(w.shape[1] == DIMS and w.dtype == np.float32 and 1 <= len(w) <= longest for w in windows)


def test_the_windows_record_reads_back_with_the_decisions_python_takes(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_yaml(ctc.CONFIG)
    monkeypatch.setattr(quant, "Int8Net", DrawnLogits)
    model = SimpleNamespace(front=SimpleNamespace(hop_stride=2))
    rng = np.random.default_rng(1)
    windows = [rng.normal(size=(n, DIMS)).astype(np.float32) for n in (188, 1, 37)]
    norm = (np.zeros(DIMS, np.float32), np.ones(DIMS, np.float32))
    body = probe.command_windows(cfg, None, model, norm, windows)

    magic, features, count, reject, margin, *sizes = probe.WINDOWS_HEAD.unpack_from(body)
    lexicon = ctc_score.default_lexicon()
    (commands, most, longest), packed = ctc_score.packed_lexicon(lexicon)
    assert (magic, features, count) == (probe.WINDOWS_MAGIC, DIMS, len(windows))
    assert (reject, margin) == (cfg["quant"]["reject"], cfg["eval"]["margin"])
    assert sizes == [commands, most, longest, cfg["chunk_hops"]]
    at = probe.WINDOWS_HEAD.size
    assert body[at : at + len(packed)] == packed
    at += len(packed) + (-(at + len(packed)) % 4)
    replay = DrawnLogits(None, cfg["quant"]["hops"], *norm, model, cfg["esp_ppq_patches"])
    for x in windows:
        (hops,) = struct.unpack_from("<I", body, at)
        at += 4
        assert hops == len(x) and body[at : at + x.nbytes] == x.astype("<f4").tobytes()
        at += x.nbytes
        q = (replay(None).numpy()[0, :, : -(-hops // 2)] / 2.0**EXPONENT).astype(np.int8)
        per_frames = ctc_score.window_frames(2)
        want, _ = ctc_score.decide(ctc_score.frame_log_probs(q, EXPONENT), lexicon, reject, margin, per_frames)
        assert list(ctc_score.DECISION_RECORD.unpack_from(body, at)) == want.tolist()
        at += ctc_score.DECISION_RECORD.size
    assert at == len(body)


def test_the_gate_record_reads_back_each_window_as_its_int8_input_and_python_decision(monkeypatch) -> None:
    cfg = load_yaml(ctc.CONFIG)
    monkeypatch.setattr(quant, "Int8Net", DrawnLogits)
    model = SimpleNamespace(front=SimpleNamespace(hop_stride=2))
    rng = np.random.default_rng(2)
    windows = [rng.normal(size=(n, DIMS)).astype(np.float32) for n in (188, 3, 37)]
    mean, std = rng.normal(size=DIMS).astype(np.float32), rng.uniform(0.5, 2.0, DIMS).astype(np.float32)
    (body,) = probe.gate_rounds(cfg, None, model, (mean, std), windows, 1 << 30)

    magic, features, count, reject, margin, *sizes, exponent, first = probe.GATE_HEAD.unpack_from(body)
    assert (magic, features, count, exponent, first) == (probe.GATE_MAGIC, DIMS, len(windows), INPUT_EXPONENT, 0)
    at = probe.GATE_HEAD.size
    stats = np.frombuffer(body, "<f4", 2 * DIMS, at)
    assert np.array_equal(stats, np.concatenate([mean, std]))
    lexicon = ctc_score.default_lexicon()
    (commands, most, longest), packed = ctc_score.packed_lexicon(lexicon)
    at += 2 * DIMS * 4
    assert sizes == [commands, most, longest, cfg["chunk_hops"]] and body[at : at + len(packed)] == packed
    at += len(packed) + (-(at + len(packed)) % 4)
    replay = DrawnLogits(None, cfg["quant"]["hops"], mean, std, model, cfg["esp_ppq_patches"])
    for x in windows:
        (hops,) = struct.unpack_from("<I", body, at)
        at += 4
        want = np.clip(np.rint((x - mean) / std / 2.0**INPUT_EXPONENT), -128, 127).astype(np.int8)
        assert hops == len(x) and body[at : at + x.size] == want.tobytes()
        at += x.size + (-x.size % 4)
        q = (replay(None).numpy()[0, :, : -(-hops // 2)] / 2.0**EXPONENT).astype(np.int8)
        log_probs = ctc_score.frame_log_probs(q, EXPONENT)
        decision, _ = ctc_score.decide(log_probs, lexicon, reject, margin, ctc_score.window_frames(2))
        assert list(ctc_score.DECISION_RECORD.unpack_from(body, at)) == decision.tolist()
        at += ctc_score.DECISION_RECORD.size
    assert at == len(body)


def test_gate_rounds_split_the_windows_in_order_into_records_the_partition_takes(monkeypatch) -> None:
    cfg = load_yaml(ctc.CONFIG)
    monkeypatch.setattr(quant, "Int8Net", DrawnLogits)
    model = SimpleNamespace(front=SimpleNamespace(hop_stride=2))
    rng = np.random.default_rng(3)
    windows = [rng.normal(size=(n, DIMS)).astype(np.float32) for n in (40, 40, 40, 40, 40)]
    mean, std = rng.normal(size=DIMS).astype(np.float32), rng.uniform(0.5, 2.0, DIMS).astype(np.float32)
    (whole,) = probe.gate_rounds(cfg, None, model, (mean, std), windows, 1 << 30)
    (one,) = probe.gate_rounds(cfg, None, model, (mean, std), windows[:1], 1 << 30)
    room = len(whole) - 3 * (len(whole) - len(one)) // (len(windows) - 1)

    rounds = probe.gate_rounds(cfg, None, model, (mean, std), windows, room)
    heads = [probe.GATE_HEAD.unpack_from(body) for body in rounds]
    assert [(h[2], h[-1]) for h in heads] == [(2, 0), (2, 2), (1, 4)]
    assert all(len(body) <= room for body in rounds)
    with pytest.raises(ValueError, match="alone does not fit"):
        probe.gate_rounds(cfg, None, model, (mean, std), windows, probe.GATE_HEAD.size + 64)


def test_gate_3_counts_the_chips_decisions_and_refuses_a_log_missing_a_window(tmp_path) -> None:
    import json

    labels = tmp_path / probe.GATE_LABELS
    expected = ["bat_den", "bat_den", "tat_den", gate.REJECT, gate.REJECT]
    labels.write_text(json.dumps({"names": ["bat_den", "tat_den"], "windows": [{"expected": e} for e in expected]}))
    log = tmp_path / "unit.log"
    log.write_text("".join(f"gate window {k}: board {c} 900 100 100\n" for k, c in enumerate([0, -1, 0, -1, 1])))
    assert probe.gate_on_chip(log, labels) == {"accepted_right": "1/3", "false_accepts": "1/2"}
    log.write_text("gate window 0: board 0 900 100 100\n")
    with pytest.raises(ValueError, match="1 gate windows"):
        probe.gate_on_chip(log, labels)


def test_a_listen_session_keeps_vad_whole_and_only_the_samples_its_windows_read() -> None:
    hop, rng, lead = grid.HOP_SAMPLES, np.random.default_rng(5), listen.UTTERANCE_LEAD_HOPS
    second = 120 + lead
    total = -(-(second + 200) // 32) * 32
    vad = np.zeros(total, dtype=bool)
    vad[50:80] = vad[second : second + 30] = vad[second + 36 : second + 50] = vad[total - 20 : total - 10] = True
    clean = rng.integers(-2000, 2000, total * hop).astype(np.int16)
    features = rng.normal(size=(total, listen.N_BANDS)).astype(np.float32)
    tracker = SimpleNamespace(reset=lambda: None, step=lambda x: np.zeros(3, dtype=np.float32))
    mel = SimpleNamespace(log=lambda bins: np.zeros(listen.N_BANDS, dtype=np.float32))
    body = probe.listen_session(clean, vad, lambda x: [len(x), 1, 2, 3], tracker, mel)
    hops, n_segments, n_windows = probe.SESSION_HEAD.unpack_from(body)
    assert hops == total and n_windows == 2
    at = probe.SESSION_HEAD.size
    bits = np.unpackbits(np.frombuffer(body[at : at + total // 8], np.uint8), bitorder="little").astype(bool)
    assert np.array_equal(bits, vad)
    at += total // 8
    segments = []
    for _ in range(n_segments):
        first, n = probe.SEGMENT_HEAD.unpack_from(body, at)
        at += probe.SEGMENT_HEAD.size
        samples = np.frombuffer(body[at : at + n * hop * 2], "<i2")
        assert np.array_equal(samples, clean[first * hop : (first + n) * hop])
        segments.append((first, first + n - 1))
        at += n * hop * 2
    windows = [probe.WINDOW_RECORD.unpack_from(body, at + k * probe.WINDOW_RECORD.size) for k in range(n_windows)]
    assert [w[:2] for w in windows] == [(0, 80), (second - lead, second + 50)]
    assert [w[2] for w in windows] == [81, 51 + lead] and segments == [(0, 80), (second - 1 - lead, second + 50)]
    assert at + n_windows * probe.WINDOW_RECORD.size == len(body)
    vad[total - 20 : total] = True
    session = gate.with_silence(clean, vad, features, mel)
    body = probe.listen_session(session[0], session[1], lambda x: [len(x), 1, 2, 3], tracker, mel)
    hops, _, n_windows = probe.SESSION_HEAD.unpack_from(body)
    last = probe.WINDOW_RECORD.unpack_from(body, len(body) - probe.WINDOW_RECORD.size)
    assert hops == total + listen.UTTERANCE_GAP_HOPS + 1 and n_windows == 3
    assert last[:3] == (total - 20 - lead, total, 21 + lead)


@pytest.mark.parametrize("locked", [{}, {"listen_hash": f"0x{listen.HASH ^ 1:08x}"}])
def test_no_listen_round_comes_of_a_locked_command_of_another_listen_yaml(locked, tmp_path, monkeypatch) -> None:
    lock = tmp_path / "models.lock.json"
    lock.write_text(json.dumps({"models": {"command": locked}}), encoding="utf-8")
    monkeypatch.setattr(probe.update_lock, "LOCK", lock)
    with pytest.raises(ValueError, match="svc_listen cuts by"):
        probe.listen_rounds(load_yaml(ctc.CONFIG), tmp_path / "out")
