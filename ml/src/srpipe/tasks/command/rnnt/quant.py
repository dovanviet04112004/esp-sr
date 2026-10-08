"""The int8 graphs of the rnnt track (KEHOACH 3.12, 3.14, ADR-0016): the encoder with the joiner's frame projection,
the predictor reading its context one-hot, and the joiner of the two projections, each as esp-dl runs it. Run: python
-m srpipe.tasks.command.rnnt.quant ptq <run>: a float row, then the three graphs quantised with each calibration of
rung 2, Gate 3 on the board sessions after int8, rows rnnt_* in <run>/int8/ladder.yaml beside the ctc track's, each
row's graphs under <run>/int8/<row>/ for probe.py.
"""

from __future__ import annotations

import argparse
import functools
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from torch import Tensor, nn

from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl
from srpipe.core.config import apply_overrides, load_yaml
from srpipe.tasks.command import ctc
from srpipe.tasks.command import eval as gate
from srpipe.tasks.command.ctc import quant as ctc_quant
from srpipe.tasks.command.ctc.model import encoder
from srpipe.tasks.command.ctc.postproc import ctc_score
from srpipe.tasks.command.rnnt.model import transducer
from srpipe.tasks.command.rnnt.postproc import rnnt_search

FRAME, PREFIX, LOGITS = "frame", "prefix", "logits"  # the joiner graph's tensors, by name on the chip
ROW_PREFIX = "rnnt_"  # its rows in <run>/int8/ladder.yaml, by ctc's
GRAPH_FILES = {"frames": "frames.native", "predictor": "predictor.native", "joiner": "joiner.native"}


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
    columns: int,
) -> Graphs:
    """net's three graphs quantised under rungs into folder with the named ESP-PPQ fixes in force: the frames graph
    on the calibration sentences, the predictor on contexts one-hot, the joiner on pairs of a calibration frame and a
    context's prefix, drawn with seed, columns pairs side by side a run as the chip runs it."""
    if pairs % columns:
        raise ValueError(f"{pairs} joiner pairs do not fill runs of {columns} columns")
    t = net.transducer
    frames_graph, predictor_graph = FramesGraph(net).eval(), PredictorGraph(t).eval()
    hot = [torch.from_numpy(one_hot(c, t.predictor.pad)) for c in contexts]
    with torch.no_grad():
        frames = torch.cat([frames_graph(x) for x in calib], dim=2)
        prefixes = torch.cat([predictor_graph(h) for h in hot], dim=2)
    rng = np.random.default_rng(seed)
    at_frame, at_prefix = rng.integers(frames.shape[2], size=pairs), rng.integers(prefixes.shape[2], size=pairs)
    joined = [
        (frames[:, :, at_frame[k : k + columns]], prefixes[:, :, at_prefix[k : k + columns]])
        for k in range(0, pairs, columns)
    ]
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
    frame_log_probs. The contexts the search asks for at once run side by side as columns, which gives each column's
    int8 exactly as alone: every product and sum of the 1x1 convolutions is an integer times a power of two that
    float32 holds exactly."""

    def __init__(self, graphs: Graphs, hops: int, net: gate.Ctc, patches: list[str]) -> None:
        self.frames = ctc_quant.Int8Net(graphs.frames, hops, net.mean, net.std, net.model, patches)
        self.predictor = ptq_espdl.Simulator(graphs.predictor, patches)
        self.joiner = ptq_espdl.Simulator(graphs.joiner, patches)
        self.frames_out = ptq_espdl.io_of(graphs.frames).output_exponent
        self.predictor_io = ptq_espdl.io_of(graphs.predictor)
        (self.join_frame, self.join_prefix), (self.join_out,) = ptq_espdl.ports_of(graphs.joiner)
        self.size, self.pad = net.cfg["rnnt"]["context"], net.model.transducer.predictor.pad
        self.prefixes: dict[tuple[int, ...], np.ndarray] = {}

    def prefix(self, context: tuple[int, ...]) -> np.ndarray:
        """The predictor graph's int8 output for a context, run once a context."""
        if context not in self.prefixes:
            e = self.predictor_io.input_exponent
            hot = ptq_espdl.to_int8(one_hot(context, self.pad), e).astype(np.float32) * np.float32(2.0**e)
            out = ptq_espdl.to_int8(self.predictor(hot), self.predictor_io.output_exponent)[0, :, 0]
            self.prefixes[context] = out
        return self.prefixes[context]

    def projected(self, x: np.ndarray, frames: int) -> np.ndarray:
        """The frames graph's int8 output (width, frames) over a normalised window (1, dims, hops)."""
        return ptq_espdl.to_int8(self.frames(torch.from_numpy(x)).numpy(), self.frames_out)[0, :, :frames]

    def logits(self, frame: np.ndarray, contexts: list[tuple[int, ...]]) -> np.ndarray:
        """The joiner's int8 logits (classes, contexts) of one projected frame (width,) with each context."""
        f = requant(frame, self.frames_out, self.join_frame.exponent)
        p = [requant(self.prefix(c), self.predictor_io.output_exponent, self.join_prefix.exponent) for c in contexts]
        scale_f, scale_p = np.float32(2.0**self.join_frame.exponent), np.float32(2.0**self.join_prefix.exponent)
        frames = np.repeat(f[None, :, None], len(contexts), axis=2).astype(np.float32) * scale_f
        (out,) = self.joiner.run(frames, np.stack(p, axis=1)[None].astype(np.float32) * scale_p)
        return ptq_espdl.to_int8(out, self.join_out.exponent)[0]

    def log_probs(self, x: np.ndarray, frames: int) -> rnnt_search.LogProbs:
        """The search's log-probabilities over a normalised window (1, dims, hops) of frames frames, the contexts of
        one request joined side by side in one run, each frame and context computed once."""
        projected = self.projected(x, frames)
        rows: dict[tuple[int, tuple[int, ...]], np.ndarray] = {}

        def log_probs(t: int, contexts: list[tuple[int, ...]]) -> np.ndarray:
            missing = list(dict.fromkeys(c for c in contexts if (t, c) not in rows))
            if missing:
                q = self.logits(projected[:, t], missing)
                for c, row in zip(missing, ctc_score.frame_log_probs(q, self.join_out.exponent).T, strict=True):
                    rows[(t, c)] = row
            return np.stack([rows[(t, c)] for c in contexts])

        return log_probs


def tree_contexts(tree: rnnt_search.Tree, size: int, pad: int) -> list[tuple[int, ...]]:
    """The distinct predictor contexts of the tree's nodes, in the order the search first needs them."""
    return list(dict.fromkeys(rnnt_search.context_of(u, size, pad) for u in tree.units))


def int8_decided(sim: Int8Rnnt, x: np.ndarray, mean: np.ndarray, std: np.ndarray, tree, reject: int, margin: int):
    """The decision of a raw window (hops, dims) on the int8 graphs, as rnnt_search.decide gives it."""
    stride = sim.frames.front.hop_stride
    frames, per_frames = -(-len(x) // stride), ctc_score.window_frames(stride)
    window = ((x - mean) / std).T[None].astype(np.float32)
    log_probs = sim.log_probs(window, frames)
    return rnnt_search.decide(log_probs, frames, tree, reject, margin, sim.size, sim.pad, per_frames)


def int8_heard(sim: Int8Rnnt, net: gate.Ctc, x: np.ndarray) -> gate.Heard:
    """One window decided by the rnnt track on its int8 graphs with no threshold, as rnnt_heard decides on float."""
    tree = rnnt_search.command_tree(net.lexicon)
    return gate.heard_of(net, *int8_decided(sim, x, net.mean, net.std, tree, ctc_score.CAP, 0))


def saved(graphs: Graphs, folder: Path) -> Path:
    """The three graphs as ESP-PPQ's native files under folder."""
    for part, name in GRAPH_FILES.items():
        export_espdl.save_native(getattr(graphs, part), folder / name)
    return folder


def loaded(folder: Path) -> Graphs:
    """The three graphs saved saved."""
    return Graphs(*(export_espdl.load_native(folder / name) for name in GRAPH_FILES.values()))


def step_ptq(cfg: dict, run: Path) -> Path:
    """Rung 2 of the rnnt track (KEHOACH 3.14): the float row, then a row of the three graphs quantised with each
    calibration; Gate 3 of each at quant.reject and eval.margin, as the ctc track's rows."""
    spec, b = cfg["quant"], ctc_quant.bench(cfg, run)
    net, patches = b.net, cfg["esp_ppq_patches"]
    thresholds = (spec["reject"], cfg["eval"]["margin"], ctc_score.CAP)
    if net.model.transducer is None:
        raise ValueError(f"{run} learnt no transducer: its config has no rnnt section")
    tree = rnnt_search.command_tree(net.lexicon)
    contexts = tree_contexts(tree, net.cfg["rnnt"]["context"], net.model.transducer.predictor.pad)
    head = {"rungs": ptq_espdl.ladder(ctc_quant.LADDER), "quant": spec}
    float_row = ctc_quant.gate_row(net, b.windows, thresholds, gate.rnnt_heard)
    out = ctc_quant.recorded(run, head, {f"{ROW_PREFIX}float": float_row})
    for name in spec["calibrations"]:
        rungs = ptq_espdl.ladder(ctc_quant.LADDER) | {"calibration": name}
        folder = run / "int8" / f"{ROW_PREFIX}{name}"
        columns = cfg["rnnt"]["joiner_columns"]
        graphs = quantized(
            net.model, b.calib, contexts, folder, rungs, patches, spec["joiner_pairs"], spec["seed"], columns
        )
        saved(graphs, folder)
        sim = Int8Rnnt(graphs, spec["hops"], net, patches)
        row = ctc_quant.gate_row(net, b.windows, thresholds, functools.partial(int8_heard, sim))
        out = ctc_quant.recorded(run, head, {f"{ROW_PREFIX}{name}": {"calibration": name, **row}})
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["ptq"])
    parser.add_argument("run", type=Path, help="a run directory of python -m srpipe.tasks.command.ctc.train")
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    args = parser.parse_args(argv)
    print(step_ptq(apply_overrides(load_yaml(ctc.CONFIG), args.overrides), args.run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
