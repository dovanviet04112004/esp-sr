"""Nets, calibration and esp-dl's integer steps the tests of esp_ppq_patches share."""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
import torch
from torch import nn

from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl

BANDS, HOPS = 8, 40
RUNGS = ptq_espdl.ladder("command_ctc")


def calib(rng: np.random.Generator) -> list:
    return [torch.from_numpy(rng.normal(0, 1, (1, BANDS, HOPS)).astype(np.float32)) for _ in range(4)]


TAIL_BANDS, TAIL_FRAMES, TAIL_SLICED, TAIL_TAPS = 24, 40, 20, 3


class Tail(nn.Module):
    """SwiftF0 without its blocks: a stem, then the head's pitch logits and peaks, and the confidence from the peaks
    beside the input's level, as SwiftF0Net computes them."""

    def __init__(self) -> None:
        super().__init__()
        self.stem = nn.Conv2d(3, 12, 3, padding=1)
        self.head = nn.Conv2d(12, 4, 3)
        self.spread = nn.Conv2d(4, 4, (5, 1), padding=(2, 0))
        self.pitch = nn.Conv2d(4, 1, 1)
        self.hidden = nn.Conv1d(5, 16, TAIL_TAPS, padding=1)
        self.confidence = nn.Conv1d(16, 1, 1)

    def forward(self, spectra: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        x = torch.relu(self.stem(spectra))
        h = torch.relu(self.head(nn.functional.pad(x[:, :, :TAIL_SLICED], (1, 1, 1, 0))))
        peaks = self.spread(h).amax(dim=2)
        level = spectra.mean(dim=(1, 2))[:, None]
        confidence = self.confidence(torch.relu(self.hidden(torch.cat([peaks, level], dim=1))))
        return self.pitch(h)[:, 0], confidence[:, 0]


def tail_calib(rng: np.random.Generator) -> list:
    """Spectra for Tail: a small offset a frame moves the level frame to frame, on the peaks' exponent."""
    shape = (1, 3, TAIL_BANDS, TAIL_FRAMES)
    offsets = (1, 1, 1, TAIL_FRAMES)
    return [
        torch.from_numpy((rng.normal(0, 1, shape) + rng.normal(0, 0.05, offsets)).astype(np.float32)) for _ in range(2)
    ]


def export_info(graph, out: Path, patches: list[str], streaming_input_shape: list[int] | None = None) -> str:
    with esp_ppq_patches.applied(patches):
        path = export_espdl.export(graph, out, streaming_input_shape=streaming_input_shape)
    return path.with_suffix(".info").read_text()


def exponent(info: str, var: str) -> int:
    return int(re.search(rf"%{re.escape(var)}\[[^\]]*\], exponents: \[(-?\d+)\]", info).group(1))


LN_HOPS, LN_WIDTH = 2048, 24


def layernorm_graph(tmp_path: Path):
    norm = nn.LayerNorm(LN_WIDTH)
    with torch.no_grad():
        norm.weight.copy_(torch.linspace(0.02, 1.5, LN_WIDTH))
        norm.bias.copy_(torch.linspace(-0.9, 0.6, LN_WIDTH))
    rng = np.random.default_rng(9)
    calib = [torch.from_numpy(rng.normal(0, 4, (1, LN_HOPS, LN_WIDTH)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(norm.eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    (op,) = [op for op in graph.operations.values() if op.type == "LayerNormalization"]
    return graph, op


def fused(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """madd.s on float32 arrays: a * b + c to float32 with one rounding. The float64 sum of the exact product rounds
    as the exact value does unless it lands on a float32 half step; then the sum's error picks the side."""
    product, addend = a.astype(np.float64) * b.astype(np.float64), np.asarray(c, np.float64)
    total = product + addend
    back = total - product
    error = (product - (total - back)) + (addend - back)
    near = total.astype(np.float32)
    low = np.where(near.astype(np.float64) > total, np.nextafter(near, np.float32(-np.inf)), near)
    high = np.where(near.astype(np.float64) < total, np.nextafter(near, np.float32(np.inf)), near)
    halfway = (low != high) & ((low.astype(np.float64) + high.astype(np.float64)) / 2 == total)
    return np.where(halfway & (error > 0), high, np.where(halfway & (error < 0), low, near))


class SoftmaxReadTwice(nn.Module):
    """Attention weights read by two products, as ReDimNet2's pool reads them."""

    def __init__(self) -> None:
        super().__init__()
        self.linear = nn.Linear(BANDS, BANDS)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = self.linear(x)
        alpha = torch.softmax(y, dim=-1)
        return torch.cat([alpha * y, alpha * (y * y)], dim=-1)


class ScaledScores(nn.Module):
    """Each hop's scores against every hop scaled by a constant into a softmax over them that weighs the hops, as
    ReDimNet2's attention scales q.k before its softmax."""

    def __init__(self) -> None:
        super().__init__()
        self.linear = nn.Linear(BANDS, BANDS)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = self.linear(x)
        return torch.matmul(torch.softmax(torch.matmul(y, y.transpose(1, 2)) * 0.41, dim=-1), y)


def espdl_graph(path: Path) -> tuple[dict, dict]:
    """An .espdl's nodes by name as (op, inputs), and its initializers by name as arrays."""
    import flatbuffers
    from esp_ppq.parser.espdl.FlatBuffers.Dl import Model

    graph = Model.Model.GetRootAs(path.read_bytes()[16:], 0).Graph()
    nodes = {}
    for i in range(graph.NodeLength()):
        node = graph.Node(i)
        nodes[node.Name().decode()] = (
            node.OpType().decode(),
            [node.Input(j).decode() for j in range(node.InputLength())],
        )
    dtypes, tensors = {1: np.float32, 3: np.int8, 6: np.int32, 7: np.int64}, {}
    for i in range(graph.InitializerLength()):
        t = graph.Initializer(i)
        dims = [int(d) for d in t.DimsAsNumpy()]
        start = t._tab.Vector(flatbuffers.number_types.UOffsetTFlags.py_type(t._tab.Offset(20)))
        raw = bytes(t._tab.Bytes[start : start + 16 * t.RawDataLength()])
        tensors[t.Name().decode()] = np.frombuffer(raw, dtypes[t.DataType()])[: math.prod(dims)].reshape(dims)
    return nodes, tensors


def filters(stored: np.ndarray) -> np.ndarray:
    """A pointwise convolution's (outputs, inputs) weights from ESP-PPQ's S3 layout of them, each 16 outputs
    interleaved innermost and the rest as they are (ResetParamLayoutPattern)."""
    n = stored.shape[-1]
    flat, aligned = stored.reshape(-1), n // 16 * 16
    per = flat.size // n
    head = flat[: aligned * per].reshape(n // 16, per, 16).transpose(0, 2, 1).reshape(aligned, per)
    return np.concatenate([head, flat[aligned * per :].reshape(n - aligned, per)])


class CopiedStem(nn.Module):
    """A one-channel map copied into c channels, as ReDimNet2's stem is rewritten (b0 12, b3 24), then a depthwise
    convolution."""

    def __init__(self, c: int = 12) -> None:
        super().__init__()
        self.c = c
        self.depthwise = nn.Conv2d(c, c, 3, padding=1, groups=c)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.depthwise(torch.cat([x] * self.c, dim=1))


class WeightedChannels(nn.Module):
    """A convolution, its channels weighted as ReDimNet2's stage sums weigh an earlier stage's, another convolution."""

    def __init__(self) -> None:
        super().__init__()
        self.front, self.back = nn.Conv1d(BANDS, BANDS, 3, padding=1), nn.Conv1d(BANDS, BANDS, 1)
        self.weigh = nn.Conv1d(BANDS, BANDS, 1, groups=BANDS, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.back(self.weigh(self.front(x)))


def round_shift(acc: np.ndarray, shift: int) -> np.ndarray:
    """int8 saturate(round half up(acc / 2^shift)), as tie728_s8_vector_round_result takes a QACC to int8."""
    if shift:
        acc = ((acc >> (shift - 1)) + 1) >> 1
    return np.clip(acc, -128, 127)
