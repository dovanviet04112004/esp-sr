"""The int8 graphs of the rnnt track (KEHOACH 3.12, 3.14, ADR-0016): the encoder with the joiner's frame projection,
the predictor reading its context one-hot, and the joiner of the two projections, each as esp-dl runs it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from torch import Tensor, nn

from srpipe.compress.quant import esp_ppq_patches, ptq_espdl
from srpipe.tasks.command import eval as gate
from srpipe.tasks.command.ctc import quant as ctc_quant
from srpipe.tasks.command.ctc.model import encoder
from srpipe.tasks.command.ctc.postproc import ctc_score
from srpipe.tasks.command.rnnt.model import transducer
from srpipe.tasks.command.rnnt.postproc import rnnt_search

FRAME, PREFIX, LOGITS = "frame", "prefix", "logits"  # the joiner graph's tensors, by name on the chip


def pointwise(linear: nn.Linear) -> nn.Conv1d:
    """linear as the 1x1 convolution over (batch, features, frames) esp-dl runs."""
    conv = nn.Conv1d(linear.in_features, linear.out_features, 1, bias=linear.bias is not None)
    with torch.no_grad():
        conv.weight.copy_(linear.weight[:, :, None])
        if linear.bias is not None:
            conv.bias.copy_(linear.bias)
    return conv


class FramesGraph(nn.Module):
    """Features (1, dims, hops) to the joiner's projected frames (1, width, frames): the encoder, then the frame
    projection, so each frame is projected once."""

    def __init__(self, net: encoder.CtcNet) -> None:
        super().__init__()
        self.front, self.stacks = net.front, net.stacks
        self.project = pointwise(net.transducer.joiner.frame_proj)

    def forward(self, x: Tensor) -> Tensor:
        return self.project(self.stacks(self.front(x)))


class PredictorGraph(nn.Module):
    """A context of classes one-hot (1, classes + 1, context) to the joiner's projected prefix (1, width, 1): the
    embedding as a 1x1 convolution, the depthwise mix over the whole context, ReLU, the prefix projection."""

    def __init__(self, t: transducer.Transducer) -> None:
        super().__init__()
        p = t.predictor
        self.embed = nn.Conv1d(p.embed.num_embeddings, p.embed.embedding_dim, 1, bias=False)
        with torch.no_grad():
            self.embed.weight.copy_(p.embed.weight.T[:, :, None])
        self.mix, self.project = p.mix, pointwise(t.joiner.prefix_proj)

    def forward(self, context: Tensor) -> Tensor:
        return self.project(torch.relu(self.mix(self.embed(context))))


class JoinerGraph(nn.Module):
    """A projected frame and a projected prefix, each (1, width, 1), to the logits of the classes (1, classes, 1)."""

    def __init__(self, t: transducer.Transducer) -> None:
        super().__init__()
        self.out = pointwise(t.joiner.out)

    def forward(self, frame: Tensor, prefix: Tensor) -> Tensor:
        return self.out(torch.tanh(frame + prefix))


def one_hot(context: tuple[int, ...], classes: int) -> np.ndarray:
    """A predictor context as PredictorGraph reads it: (1, classes + 1, len(context)), the pad id its last row."""
    x = np.zeros((1, classes + 1, len(context)), dtype=np.float32)
    x[0, list(context), np.arange(len(context))] = 1.0
    return x


def contexts_of(units: list[np.ndarray], size: int, pad: int) -> list[tuple[int, ...]]:
    """Every predictor context the unit sequences pass through when each is fed its own units, once each, in the order
    first seen."""
    seen: dict[tuple[int, ...], None] = {}
    for u in units:
        said: tuple[int, ...] = ()
        for k in range(len(u) + 1):
            seen.setdefault(rnnt_search.context_of(said, size, pad), None)
            said = (*said, int(u[k])) if k < len(u) else said
    return list(seen)


@dataclass(frozen=True)
class Graphs:
    """The rnnt track's three quantised ESP-PPQ graphs."""

    frames: object
    predictor: object
    joiner: object


def quantized(
    net: encoder.CtcNet,
    calib: list[torch.Tensor],
    contexts: list[tuple[int, ...]],
    folder: Path,
    rungs: dict,
    patches: list[str],
    pairs: int,
    seed: int,
) -> Graphs:
    """net's three graphs quantised under rungs into folder with the named ESP-PPQ fixes in force: the frames graph
    on the calibration sentences, the predictor on contexts one-hot, the joiner on pairs of a calibration frame and a
    context's prefix, drawn with seed."""
    t = net.transducer
    frames_graph, predictor_graph = FramesGraph(net).eval(), PredictorGraph(t).eval()
    hot = [torch.from_numpy(one_hot(c, t.predictor.pad)) for c in contexts]
    with torch.no_grad():
        frames = torch.cat([frames_graph(x) for x in calib], dim=2)
        prefixes = torch.cat([predictor_graph(h) for h in hot], dim=2)
    rng = np.random.default_rng(seed)
    drawn = zip(rng.integers(frames.shape[2], size=pairs), rng.integers(prefixes.shape[2], size=pairs), strict=True)
    joined = [(frames[:, :, i : i + 1], prefixes[:, :, j : j + 1]) for i, j in drawn]
    built = ctc_quant.quantized(frames_graph, calib, folder / "frames", rungs, patches)
    with esp_ppq_patches.applied(patches):
        predictor = ptq_espdl.quantize(predictor_graph, hot, folder / "predictor", rungs)
        joiner = ptq_espdl.quantize_named(
            JoinerGraph(t).eval(), joined, folder / "joiner", rungs, [FRAME, PREFIX], [LOGITS]
        )
    return Graphs(built, predictor, joiner)


def requant(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    """int8 values on the grid of 2^src put on the grid of 2^dst, as the chip hands a vector from one graph to the
    next: a shift when dst is finer, rounded half to even when coarser, held to int8 either way."""
    return np.clip(np.rint(np.ldexp(x.astype(np.float64), src - dst)), -128, 127).astype(np.int8)


class Int8Rnnt:
    """The three graphs as the chip runs them, simulated under the branch's ESP-PPQ fixes: a window's projected frames
    once, each context's prefix once, and the joiner's int8 logits to log-probabilities by ctc_score's
    frame_log_probs."""

    def __init__(self, graphs: Graphs, hops: int, net: gate.Ctc, patches: list[str]) -> None:
        self.frames = ctc_quant.Int8Net(graphs.frames, hops, net.mean, net.std, net.model, patches)
        self.predictor = ptq_espdl.Simulator(graphs.predictor, patches)
        self.joiner = ptq_espdl.Simulator(graphs.joiner, patches)
        self.frames_out = ptq_espdl.io_of(graphs.frames).output_exponent
        self.predictor_io = ptq_espdl.io_of(graphs.predictor)
        (self.join_frame, self.join_prefix), (self.join_out,) = ptq_espdl.ports_of(graphs.joiner)
        self.size, self.pad = net.cfg["rnnt"]["context"], net.model.transducer.predictor.pad

    def prefix(self, context: tuple[int, ...]) -> np.ndarray:
        """The predictor graph's int8 output for a context."""
        e = self.predictor_io.input_exponent
        hot = ptq_espdl.to_int8(one_hot(context, self.pad), e).astype(np.float32) * np.float32(2.0**e)
        return ptq_espdl.to_int8(self.predictor(hot), self.predictor_io.output_exponent)[0, :, 0]

    def log_probs(self, x: np.ndarray, frames: int) -> rnnt_search.LogProbs:
        """The search's log-probabilities over a normalised window (1, dims, hops) of frames frames."""
        projected = ptq_espdl.to_int8(self.frames(torch.from_numpy(x)).numpy(), self.frames_out)[0, :, :frames]
        prefixes: dict[tuple[int, ...], np.ndarray] = {}

        def log_probs(t: int, context: tuple[int, ...]) -> np.ndarray:
            if context not in prefixes:
                prefixes[context] = self.prefix(context)
            f = requant(projected[:, t], self.frames_out, self.join_frame.exponent)
            p = requant(prefixes[context], self.predictor_io.output_exponent, self.join_prefix.exponent)
            scale_f, scale_p = np.float32(2.0**self.join_frame.exponent), np.float32(2.0**self.join_prefix.exponent)
            (logits,) = self.joiner.run(f[None, :, None] * scale_f, p[None, :, None] * scale_p)
            q = ptq_espdl.to_int8(logits, self.join_out.exponent)[0]
            return ctc_score.frame_log_probs(q, self.join_out.exponent)[:, 0]

        return log_probs


def int8_heard(sim: Int8Rnnt, net: gate.Ctc, x: np.ndarray) -> gate.Heard:
    """One window decided by the rnnt track on its int8 graphs with no threshold, as rnnt_heard decides on float."""
    frames = -(-len(x) // net.model.front.hop_stride)
    window = ((x - net.mean) / net.std).T[None].astype(np.float32)
    r = net.cfg["rnnt"]
    fst = rnnt_search.command_fst(net.lexicon)
    decision = rnnt_search.decide(
        sim.log_probs(window, frames), frames, fst, len(net.names), ctc_score.CAP, 0, r["beam"], sim.size, sim.pad
    )
    return gate.heard_of(net, *decision)
