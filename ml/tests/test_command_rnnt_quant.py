"""The rnnt track's int8 graphs: the encoder with the frame projection, the predictor reading its context one-hot and
the joiner of the two compute what the float transducer does."""

from __future__ import annotations

import numpy as np
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


def test_requant_shifts_onto_a_finer_grid_and_rounds_half_even_onto_a_coarser_one() -> None:
    x = np.array([-128, -3, -1, 0, 1, 3, 5, 100, 127], dtype=np.int8)
    assert rnnt_quant.requant(x, -4, -5).tolist() == [-128, -6, -2, 0, 2, 6, 10, 127, 127]
    assert rnnt_quant.requant(x, -4, -3).tolist() == [-64, -2, -0, 0, 0, 2, 2, 50, 64]
    assert rnnt_quant.requant(x, -4, -4).tolist() == x.tolist()


def test_the_contexts_are_those_each_sentence_passes_through_once_each() -> None:
    pad = encoder.n_classes()
    got = rnnt_quant.contexts_of([np.array([3, 5]), np.array([3])], 2, pad)
    assert got == [(pad, ctc_score.BLANK), (ctc_score.BLANK, 4), (4, 6)]


def test_the_int8_chain_gives_log_probabilities_and_a_decision(cfg, net, tmp_path) -> None:
    pytest.importorskip("esp_ppq")
    from srpipe.compress.quant import ptq_espdl
    from srpipe.tasks.command import eval as gate
    from srpipe.tasks.command.ctc import probe

    net = probe.draw_norm_scales(net, *cfg["probe"]["norm_scale"]).eval()
    rng = np.random.default_rng(3)
    dims, hops = encoder.n_dims(cfg), 64
    calib = [torch.from_numpy(rng.normal(size=(1, dims, hops)).astype(np.float32)) for _ in range(4)]
    contexts = rnnt_quant.contexts_of([np.array([0, 38, 1, 39]), np.array([2, 40])], 2, encoder.n_classes())
    rungs = ptq_espdl.ladder("command_ctc") | {"equalization": None, "bias_correction": False, "calibration": "minmax"}
    graphs = rnnt_quant.quantized(net, calib, contexts, tmp_path, rungs, cfg["esp_ppq_patches"], 16, 4)
    e = ptq_espdl.io_of(graphs.predictor).input_exponent
    assert ptq_espdl.to_int8(np.ones(1, np.float32), e)[0] * 2.0**e == 1.0
    stats = (np.zeros(dims, np.float32), np.ones(dims, np.float32))
    lexicon = [[np.array([0, 38, 1, 39], np.uint8)], [np.array([2, 40], np.uint8)]]
    ctc_net = gate.Ctc(net, *stats, ["a", "b"], lexicon, cfg)
    sim = rnnt_quant.Int8Rnnt(graphs, hops, ctc_net)
    x = rng.normal(size=(40, dims)).astype(np.float32)
    lp = sim.log_probs(((x - stats[0]) / stats[1]).T[None].astype(np.float32), 20)(0, contexts[0])
    assert lp.shape == (encoder.n_classes(),) and abs(np.logaddexp.reduce(lp.astype(np.float64))) < 1e-5
    assert rnnt_quant.int8_heard(sim, ctc_net, x).command in ("a", "b", gate.REJECT)
