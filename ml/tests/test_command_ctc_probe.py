"""The ctc probe's command windows: random windows of the lengths the board must handle, and a record whose head,
lexicon and windows read back, each window's decision the one ctc_score takes on its int8 logits."""

from __future__ import annotations

import struct
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("torch")

import torch

from srpipe.core.config import load_yaml
from srpipe.tasks.command import ctc
from srpipe.tasks.command import eval as gate
from srpipe.tasks.command.ctc import probe, quant
from srpipe.tasks.command.ctc.model import encoder
from srpipe.tasks.command.ctc.postproc import ctc_score

DIMS = 43
EXPONENT = -3


class DrawnLogits:
    """Stands for quant.Int8Net: fresh int8 logits on the grid of 2^EXPONENT for each window, from a fixed seed."""

    def __init__(self, graph, hops: int, mean: np.ndarray, std: np.ndarray, like) -> None:
        self.io, self.frames = SimpleNamespace(output_exponent=EXPONENT), hops // like.front.hop_stride
        self.rng = np.random.default_rng(7)

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        q = self.rng.integers(-60, 61, (encoder.n_classes(), self.frames))
        return torch.from_numpy((q * 2.0**EXPONENT).astype(np.float32))[None]


def test_random_windows_hold_the_longest_one_hop_and_one_chunk() -> None:
    cfg = load_yaml(ctc.CONFIG)
    windows = probe.random_windows(cfg, np.zeros(DIMS, np.float32), np.ones(DIMS, np.float32))
    longest = round(cfg["window_s"] * gate.HOPS_PER_S)
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
    replay = DrawnLogits(None, cfg["quant"]["hops"], *norm, model)
    for x in windows:
        (hops,) = struct.unpack_from("<I", body, at)
        at += 4
        assert hops == len(x) and body[at : at + x.nbytes] == x.astype("<f4").tobytes()
        at += x.nbytes
        q = (replay(None).numpy()[0, :, : -(-hops // 2)] / 2.0**EXPONENT).astype(np.int8)
        want, _ = ctc_score.decide(ctc_score.frame_log_probs(q, EXPONENT), lexicon, reject, margin)
        assert list(ctc_score.DECISION_RECORD.unpack_from(body, at)) == want.tolist()
        at += ctc_score.DECISION_RECORD.size
    assert at == len(body)
