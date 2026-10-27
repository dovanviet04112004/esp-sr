"""esp_ppq_patches.ops.conv_block: residual blocks and the 1x1 convolutions beside them as ConvBlock, with
esp-dl's integers."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from torch import nn  # noqa: E402

from esp_ppq_cases import RUNGS, espdl_graph, export_info, round_shift  # noqa: E402
from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl  # noqa: E402

BLOCK_HOPS, BLOCK_BANDS = 6, 8


class ResBlock(nn.Module):
    """A ReDimNet2 conv block over a [1, C, T, F] map as its ResBasicBlock runs one, depthwise: a 3x3 and a pointwise
    convolution, a ReLU and a norm, the two again and a norm, the input added and a ReLU; middle, the first norm's
    output is an output of its own too."""

    def __init__(self, c: int, middle: bool = False) -> None:
        super().__init__()
        self.middle = middle
        self.conv1, self.conv2 = (nn.Conv2d(c, c, 3, padding=1, groups=c, bias=False) for _ in range(2))
        self.conv1pw, self.conv2pw = nn.Conv2d(c, c, 1), nn.Conv2d(c, c, 1)
        self.bn1, self.bn2 = nn.BatchNorm2d(c), nn.BatchNorm2d(c)
        for bn in (self.bn1, self.bn2):
            nn.init.uniform_(bn.weight, 0.5, 1.5)
            nn.init.uniform_(bn.bias, -0.5, 0.5)
            bn.running_mean.uniform_(-0.5, 0.5)
            bn.running_var.uniform_(0.5, 2.0)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = self.bn1(torch.relu(self.conv1pw(self.conv1(x))))
        z = self.bn2(self.conv2pw(self.conv2(y)))
        out = torch.relu(z + x)
        return torch.cat([out, y], dim=1) if self.middle else out


def res_block(tmp_path: Path, c: int, middle: bool):
    """The quantised ResBlock of c channels and a window on its input's int8 grid."""
    torch.manual_seed(c)
    rng = np.random.default_rng(c)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, c, BLOCK_HOPS, BLOCK_BANDS)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(ResBlock(c, middle).eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    exponent = ptq_espdl.io_of(graph).input_exponent
    return graph, ptq_espdl.to_int8(calib[0].numpy(), exponent).astype(np.float32) * np.float32(2.0**exponent)


def conv_block_espdl(x: np.ndarray, values: list[np.ndarray], shifts: list[int]) -> np.ndarray:
    """ConvBlock's int8 output for x [T, F, C], from its weights and biases as the export lays them and its shifts,
    each op's integers as esp-dl's S3 kernels give them."""
    t, f, c = x.shape
    dw1, pw1, b1, bn, bn_bias, dw2, pw2, b2 = (v.astype(np.int64) for v in values)

    def depthwise(m: np.ndarray, taps: np.ndarray, shift: int) -> np.ndarray:
        p = np.pad(m, [(1, 1), (1, 1), (0, 0)])
        return round_shift(
            sum(p[i : i + t, j : j + f] * taps[3 * i + j, :c] for i in range(3) for j in range(3)), shift
        )

    def pointwise(m: np.ndarray, laid: np.ndarray, bias: np.ndarray, shift: int) -> np.ndarray:
        weights = laid.transpose(0, 2, 1).reshape(-1, c)[:c]
        return round_shift(m @ weights.T + bias[:c], shift)

    x = x.astype(np.int64)
    a = depthwise(x, dw1, shifts[0])
    b = np.maximum(pointwise(a, pw1, b1, shifts[1]), 0)
    d = depthwise(round_shift(b * bn[0, :c] + bn_bias[:c], shifts[2]), dw2, shifts[3])
    e = pointwise(d, pw2, b2, shifts[4])
    return np.maximum(np.clip(e + round_shift(x, shifts[5]), -128, 127), 0)


@pytest.mark.parametrize("c", [24, 48])
def test_conv_block_exports_a_block_as_one_op_giving_its_integers(tmp_path: Path, c: int) -> None:
    graph, x = res_block(tmp_path, c, middle=False)
    patches = ["requantise_graph_inputs", "aligned_pointwise", "conv_block"]
    with esp_ppq_patches.applied(patches):
        path = export_espdl.export(graph, tmp_path / "block.espdl")
    nodes, tensors = espdl_graph(path)
    ((_, (_, *params)),) = [node for node in nodes.values() if node[0] == "ConvBlock"]
    assert not {"Conv", "Add", "Relu", "Reshape", "RequantizeLinear"} & {op for op, _ in nodes.values()}
    cp = -(-c // 16) * 16
    taps, pointwise, channels = (9, cp), (cp // 16, c, 16), (cp,)
    assert [tensors[p].shape for p in params] == [
        taps,
        pointwise,
        channels,
        (1, cp),
        channels,
        taps,
        pointwise,
        channels,
    ]
    info = path.with_suffix(".info").read_text()
    shifts = [int(s) for s in re.search(r"= ConvBlock\[.*shifts = \[([-\d, ]+)\]", info).group(1).split(",")]
    io = ptq_espdl.io_of(graph)
    want = ptq_espdl.Simulator(graph, patches)(x) / 2.0**io.output_exponent
    laid = x[0].transpose(1, 2, 0) / 2.0**io.input_exponent
    values = [tensors[p] for p in params]
    assert np.array_equal(conv_block_espdl(laid.astype(np.int64), values, shifts), want[0].transpose(1, 2, 0))
    off = [*shifts[:4], shifts[4] + 1, shifts[5]]
    assert not np.array_equal(conv_block_espdl(laid.astype(np.int64), values, off), want[0].transpose(1, 2, 0))


class StageEnds(nn.Module):
    """A conv block between 1x1 convolutions, as ReDimNet2 b0's stages 0 and 1 run theirs: 12 channels to 24, by a
    1x1 or by a 1x2 at a stride of 2, the block, then back to 12."""

    def __init__(self, pair: bool) -> None:
        super().__init__()
        self.pre = nn.Conv2d(12, 24, (1, 2), stride=(1, 2)) if pair else nn.Conv2d(12, 24, 1)
        self.block, self.post = ResBlock(24), nn.Conv2d(24, 12, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.post(self.block(self.pre(x)))


def pointwise_espdl(rows: np.ndarray, laid: np.ndarray, bias: np.ndarray, shift: int, n: int) -> np.ndarray:
    """A pointwise convolution's int8 outputs [..., n] from int8 rows [..., K], its laid weights and bias, as esp-dl's
    Conv rounds them."""
    weights = laid.astype(np.int64).transpose(0, 2, 1).reshape(-1, rows.shape[-1])[:n]
    return round_shift(rows.astype(np.int64) @ weights.T + bias[:n].astype(np.int64), shift)


@pytest.mark.parametrize("pair", [False, True])
def test_conv_block_takes_the_1x1_convolutions_before_and_after_it(tmp_path: Path, pair: bool) -> None:
    torch.manual_seed(7)
    rng = np.random.default_rng(7)
    bands = BLOCK_BANDS * (2 if pair else 1)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 12, BLOCK_HOPS, bands)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(StageEnds(pair).eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    exponent = ptq_espdl.io_of(graph).input_exponent
    x = ptq_espdl.to_int8(calib[0].numpy(), exponent).astype(np.float32) * np.float32(2.0**exponent)
    patches = ["requantise_graph_inputs", "aligned_pointwise", "conv_block"]
    with esp_ppq_patches.applied(patches):
        path = export_espdl.export(graph, tmp_path / "ends.espdl")
    nodes, tensors = espdl_graph(path)
    assert "Conv" not in {op for op, _ in nodes.values()}
    ((_, (_, *params)),) = [node for node in nodes.values() if node[0] == "ConvBlock"]
    assert len(params) == 12
    info = path.with_suffix(".info").read_text()
    line = re.search(r"= ConvBlock\[(.*?)\]\(", info).group(1)
    shifts = [int(v) for v in re.search(r"shifts = \[([-\d, ]+)\]", line).group(1).split(",")]
    (k, pre_shift), (n, post_shift) = (
        [int(v) for v in re.search(name + r" = \[(\d+), (\d+)\]", line).groups()] for name in ("pre", "post")
    )
    assert (k, n) == (24 if pair else 12, 12)
    io = ptq_espdl.io_of(graph)
    want = ptq_espdl.Simulator(graph, patches)(x) / 2.0**io.output_exponent
    laid = (x[0].transpose(1, 2, 0) / 2.0**io.input_exponent).astype(np.int64)
    values = [tensors[p] for p in params]
    middle = pointwise_espdl(laid.reshape(laid.shape[0], -1, k), values[8], values[9], pre_shift, 24)
    block = conv_block_espdl(middle, values[:8], shifts)
    assert np.array_equal(pointwise_espdl(block, values[10], values[11], post_shift, n), want[0].transpose(1, 2, 0))
    off = pointwise_espdl(block, values[10], values[11], post_shift + 1, n)
    assert not np.array_equal(off, want[0].transpose(1, 2, 0))


def test_conv_block_leaves_a_block_whose_middle_map_another_op_reads(tmp_path: Path) -> None:
    graph, _ = res_block(tmp_path, 24, middle=True)
    info = export_info(graph, tmp_path / "middle.espdl", ["requantise_graph_inputs", "aligned_pointwise", "conv_block"])
    assert not re.findall(r"= ConvBlock\[", info)
    assert len(re.findall(r"= Relu\[", info)) == 1
