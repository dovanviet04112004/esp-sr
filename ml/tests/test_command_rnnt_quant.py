"""The rnnt track's int8 graphs: the encoder with the frame projection, the predictor reading its context one-hot and
the joiner of the two compute what the float transducer does."""

from __future__ import annotations

import pytest

pytest.importorskip("torch")

import torch

from srpipe.core.config import load_yaml
from srpipe.tasks.command import ctc
from srpipe.tasks.command.ctc.model import encoder
from srpipe.tasks.command.ctc.postproc import ctc_score
from srpipe.tasks.command.rnnt import quant as rnnt_quant


@pytest.fixture
def cfg():
    return load_yaml(ctc.CONFIG)


@pytest.fixture
def net(cfg):
    torch.manual_seed(1)
    return encoder.build(cfg).eval()


def test_the_rnnt_graphs_compute_what_the_float_transducer_does(cfg, net) -> None:
    t = net.transducer
    x = torch.randn(1, encoder.n_dims(cfg), 4 * cfg["chunk_hops"])
    with torch.no_grad():
        frames = rnnt_quant.FramesGraph(net)(x)
        assert torch.allclose(frames, t.joiner.frame_proj(net.encode(x).transpose(1, 2)).transpose(1, 2), atol=1e-5)
        for context in [(t.predictor.pad, ctc_score.BLANK), (ctc_score.BLANK, 7), (12, 40)]:
            graph = rnnt_quant.PredictorGraph(t)(torch.from_numpy(rnnt_quant.one_hot(context, encoder.n_classes())))
            want = t.joiner.prefix_proj(t.predictor(torch.tensor([context]))[0, -1])
            assert torch.allclose(graph[0, :, 0], want, atol=1e-5)
            logits = rnnt_quant.JoinerGraph(t)(frames[:, :, :1], graph)
            assert torch.allclose(logits[0, :, 0], t.joiner(frames[0, :, 0], want), atol=1e-5)
