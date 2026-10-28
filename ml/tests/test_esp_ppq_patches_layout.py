"""esp_ppq_patches.layout: export patches that keep esp-dl's ops give the same integers through fewer or faster
nodes."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from torch import nn  # noqa: E402

from esp_ppq_cases import (  # noqa: E402
    BANDS,
    HOPS,
    RUNGS,
    CopiedStem,
    SoftmaxReadTwice,
    WeightedChannels,
    calib,
    espdl_graph,
    export_info,
    filters,
)
from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl  # noqa: E402


def test_chain_as_one_finds_the_one_transpose_a_chain_moves_values_by() -> None:
    assert esp_ppq_patches.chain_as_one([2, 3, 4], [0, 2, 1], [2, 4, 3], [0, 2, 1]) == ([0, 1, 2], [2, 3, 4])
    assert esp_ppq_patches.chain_as_one([2, 3, 4, 5], [0, 1, 3, 2], [2, 3, 20], [0, 2, 1]) == ([0, 3, 2, 1], [2, 20, 3])
    assert esp_ppq_patches.chain_as_one([2, 6], [1, 0], [3, 4], [1, 0]) is None


class MapsTo1d(nn.Module):
    """A 2-d convolution, its map flattened to 1-d as ReDimNet2's to1d does, then two 1-d convolutions on it."""

    def __init__(self) -> None:
        super().__init__()
        self.conv2d = nn.Conv2d(1, 4, 3, padding=1)
        self.a, self.b = nn.Conv1d(4 * 6, 8, 1), nn.Conv1d(4 * 6, 8, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = self.conv2d(x)
        n, c, f, t = y.shape
        flat = y.permute(0, 2, 1, 3).reshape(n, f * c, t)
        return self.a(flat) + self.b(flat)


def test_lean_transposes_exports_fewer_transposes_and_simulates_as_before(tmp_path: Path) -> None:
    torch.manual_seed(1)
    rng = np.random.default_rng(14)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 1, 6, HOPS)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(MapsTo1d().eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    x = calib[0].numpy()

    def transposes(patches: list[str], name: str) -> int:
        info = export_info(graph, tmp_path / f"{name}.espdl", patches)
        return len(re.findall(r"^  %\S+ = Transpose\[", info, re.MULTILINE))

    before = ptq_espdl.Simulator(graph)(x)
    assert transposes(["lean_transposes"], "lean") < transposes([], "as_is")
    assert np.array_equal(ptq_espdl.Simulator(graph)(x), before)


def test_lean_quantizers_exports_one_quantize_node_per_grid(tmp_path: Path) -> None:
    torch.manual_seed(2)
    rng = np.random.default_rng(15)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, HOPS, BANDS)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(SoftmaxReadTwice().eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})

    def quantizers(patches: list[str], name: str) -> int:
        info = export_info(graph, tmp_path / f"{name}.espdl", patches)
        return len(re.findall(r"= QuantizeLinear\[.*Softmax", info))

    assert quantizers([], "as_is") == 2
    assert quantizers(["lean_quantizers"], "lean") == 1


class NarrowPointwise(nn.Module):
    """A 1-d map of 8 bands of 12 channels laid out as a 2-d one, as ReDimNet2's to2d does, through narrow
    convolutions: a pointwise one and a ReLU, a depthwise pointwise one, one over each 2 bands, one to 12 channels."""

    def __init__(self) -> None:
        super().__init__()
        self.pointwise = nn.Conv2d(12, 24, 1)
        self.depthwise = nn.Conv2d(24, 24, 1, groups=24)
        self.pairs = nn.Conv2d(24, 24, (1, 2), stride=(1, 2))
        self.narrow = nn.Conv2d(24, 12, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        n, _, t = x.shape
        y = torch.relu(self.pointwise(x.reshape(n, 8, 12, t).permute(0, 2, 3, 1)))
        return self.narrow(self.pairs(self.depthwise(y)))


def test_aligned_pointwise_runs_narrow_convolutions_block_diagonal_over_merged_columns(tmp_path: Path) -> None:
    torch.manual_seed(3)
    rng = np.random.default_rng(16)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 96, 6)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(NarrowPointwise().eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    plain = espdl_graph(export_espdl.export(graph, tmp_path / "plain.espdl"))
    with esp_ppq_patches.applied(["aligned_pointwise"]):
        aligned = espdl_graph(export_espdl.export(graph, tmp_path / "aligned.espdl"))
    for name, k in {"/pointwise/Conv": 4, "/depthwise/Conv": 2, "/pairs/Conv": 2, "/narrow/Conv": 4}.items():
        (_, (_, w, b)), (_, (_, w_k, b_k)) = plain[0][name], aligned[0][name]
        weights, merged = plain[1][w], aligned[1][w_k]
        if weights.shape[-1] == 1:
            assert np.array_equal(merged, np.tile(weights, (1, 1, k, 1)))
        else:
            want = np.kron(np.eye(k, dtype=np.int8), filters(weights))
            assert np.array_equal(filters(merged), want)
        assert np.array_equal(aligned[1][b_k], np.tile(plain[1][b], k))
    reshapes = [sum(op == "Reshape" for op, _ in nodes.values()) for nodes in (plain[0], aligned[0])]
    assert reshapes[1] - reshapes[0] == 8


def test_copies_by_broadcast_writes_the_copies_as_an_add_of_zeros(tmp_path: Path) -> None:
    torch.manual_seed(4)
    rng = np.random.default_rng(17)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 1, 6, 8)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(CopiedStem().eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    plain = espdl_graph(export_espdl.export(graph, tmp_path / "plain.espdl"))
    with esp_ppq_patches.applied(["copies_by_broadcast"]):
        nodes, tensors = espdl_graph(export_espdl.export(graph, tmp_path / "broadcast.espdl"))
    assert {"Concat", "Transpose"} <= {op for op, _ in plain[0].values()}
    assert not {"Concat", "Transpose"} & {op for op, _ in nodes.values()}
    ((_, (_, zeros)),) = [node for node in nodes.values() if node[0] == "Add"]
    assert tensors[zeros].shape == (1, 1, 1, 12) and not tensors[zeros].any()


def test_cache_sized_convolutions_slice_a_wide_projection_along_its_outputs(tmp_path: Path) -> None:
    torch.manual_seed(5)
    rng = np.random.default_rng(18)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 576, 6)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(nn.Conv1d(576, 128, 1).eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    plain = espdl_graph(export_espdl.export(graph, tmp_path / "plain.espdl"))
    with esp_ppq_patches.applied(["cache_sized_convolutions"]):
        nodes, tensors = espdl_graph(export_espdl.export(graph, tmp_path / "sliced.espdl"))
    ((_, (_, w, b)),) = [node for node in plain[0].values() if node[0] == "Conv"]
    parts = [ins for op, ins in nodes.values() if op == "Conv"]
    assert [tensors[ins[1]].shape[-1] for ins in parts] == [48, 48, 32]
    assert [op for op, _ in nodes.values()].count("Concat") == 1
    weights = np.concatenate([filters(tensors[ins[1]]) for ins in parts])
    assert np.array_equal(weights, filters(plain[1][w]))
    assert np.array_equal(np.concatenate([tensors[ins[2]] for ins in parts]), plain[1][b])


def test_cache_sized_convolutions_leave_whole_a_projection_of_one_slice(tmp_path: Path) -> None:
    """72 outputs, no multiple of 16, make one slice: no Concat of that one input, which esp-dl's Concat refuses."""
    torch.manual_seed(5)
    rng = np.random.default_rng(18)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 1152, 6)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(nn.Conv1d(1152, 72, 1).eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    with esp_ppq_patches.applied(["cache_sized_convolutions"]):
        nodes, _ = espdl_graph(export_espdl.export(graph, tmp_path / "whole.espdl"))
    ops = [op for op, _ in nodes.values()]
    assert ops.count("Conv") == 1 and "Concat" not in ops


class OneWeightSum(nn.Module):
    """A convolution, a weighted sum of its output alone as ReDimNet2's stage sums give it, a depthwise convolution
    by 1, and another convolution; equalisation scales a sum between convolutions, not b0's after a LayerNorm."""

    def __init__(self) -> None:
        super().__init__()
        self.front, self.back = nn.Conv1d(BANDS, BANDS, 3, padding=1), nn.Conv1d(BANDS, BANDS, 1)
        self.sum = nn.Conv1d(BANDS, BANDS, 1, groups=BANDS, bias=False)
        nn.init.constant_(self.sum.weight, 1.0)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.back(self.sum(self.front(x)))


def test_identity_convolutions_dropped_gives_the_sums_reader_its_input(tmp_path: Path) -> None:
    torch.manual_seed(6)
    rungs = RUNGS | {"equalization": None, "calibration": "minmax"}
    graph = ptq_espdl.quantize(OneWeightSum().eval(), calib(np.random.default_rng(19)), tmp_path, rungs)
    plain = espdl_graph(export_espdl.export(graph, tmp_path / "plain.espdl"))[0]
    with esp_ppq_patches.applied(["identity_convolutions_dropped"]):
        nodes = espdl_graph(export_espdl.export(graph, tmp_path / "dropped.espdl"))[0]
    assert "/sum/Conv" in plain and "/sum/Conv" not in nodes
    assert nodes["/back/Conv"][1][0] == plain["/sum/Conv"][1][0]


class SquareBeside(nn.Module):
    """A 2-d map flattened to 1-d, read by a convolution and squared, the square times the convolution's output, as
    ReDimNet2's pool does with its input."""

    def __init__(self) -> None:
        super().__init__()
        self.conv2d, self.conv1d = nn.Conv2d(1, 4, 3, padding=1), nn.Conv1d(4 * 6, 4 * 6, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        flat = self.conv2d(x).reshape(1, 4 * 6, HOPS)
        return self.conv1d(flat) * (flat * flat)


def test_lean_transposes_squares_the_channels_last_map_the_convolution_reads(tmp_path: Path) -> None:
    torch.manual_seed(7)
    rng = np.random.default_rng(20)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 1, 6, HOPS)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(SquareBeside().eval(), calib, tmp_path, RUNGS)

    from esp_ppq.parser.espdl.FlatBuffers.Dl import Model

    with esp_ppq_patches.applied(["lean_transposes"]):
        path = export_espdl.export(graph, tmp_path / "lean.espdl")
    nodes, flat = espdl_graph(path)[0], Model.Model.GetRootAs(path.read_bytes()[16:], 0).Graph()
    made = {flat.Node(i).Name().decode(): flat.Node(i).Output(0).decode() for i in range(flat.NodeLength())}
    (square,) = [name for name, (op, ins) in nodes.items() if op == "Mul" and ins[0] == ins[1]]
    assert nodes[square][1][0] == nodes["/conv1d/Conv"][1][0]
    assert not [op for op, ins in nodes.values() if op == "Transpose" and made[square] in ins]


class ContextBranch(nn.Module):
    """A 2-d map flattened to 1-d, projected per frame, and its mean and spread over time projected and added, as
    ReDimNet2's pool takes its context."""

    def __init__(self) -> None:
        super().__init__()
        self.conv2d = nn.Conv2d(1, 4, 3, padding=1)
        self.frames, self.context = nn.Conv1d(4 * 6, 8, 1), nn.Conv1d(2 * 4 * 6, 8, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        flat = self.conv2d(x).reshape(1, 4 * 6, HOPS)
        mean = flat.mean(dim=-1, keepdim=True)
        spread = ((flat - mean) * (flat - mean)).mean(dim=-1, keepdim=True)
        return self.frames(flat) + self.context(torch.cat([mean, spread], dim=1))


def test_lean_transposes_take_the_context_over_time_on_the_channels_last_map(tmp_path: Path) -> None:
    torch.manual_seed(8)
    rng = np.random.default_rng(21)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 1, 6, HOPS)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(ContextBranch().eval(), calib, tmp_path, RUNGS)
    with esp_ppq_patches.applied(["lean_transposes"]):
        nodes = espdl_graph(export_espdl.export(graph, tmp_path / "lean.espdl"))[0]
    means = [ins[0] for op, ins in nodes.values() if op == "ReduceMean"]
    assert means and means[0] == nodes["/frames/Conv"][1][0]
    assert sum(op == "Transpose" for op, _ in nodes.values()) <= 1
    assert nodes["/context/Conv"][1][0] in {name + "_output_0" for name, (op, _) in nodes.items() if op == "Concat"}


def test_channel_scales_as_mul_weighs_the_channels_by_the_same_integers(tmp_path: Path) -> None:
    torch.manual_seed(9)
    graph = ptq_espdl.quantize(WeightedChannels().eval(), calib(np.random.default_rng(22)), tmp_path, RUNGS)
    plain = espdl_graph(export_espdl.export(graph, tmp_path / "plain.espdl"))
    with esp_ppq_patches.applied(["channel_scales_as_mul"]):
        nodes, tensors = espdl_graph(export_espdl.export(graph, tmp_path / "mul.espdl"))
    assert "/weigh/Conv" in plain[0] and "/weigh/Conv" not in nodes
    ((_, (_, weights)),) = [node for node in nodes.values() if node[0] == "Mul"]
    assert tensors[weights].shape == (1, 1, BANDS)
    assert np.array_equal(tensors[weights].ravel(), plain[1][plain[0]["/weigh/Conv"][1][1]].ravel())


class TrailingAxis(nn.Module):
    """A convolution's map given a trailing axis and copied along it, then laid flat, as command_ctc's stacks repeat
    their frames."""

    def __init__(self) -> None:
        super().__init__()
        self.conv = nn.Conv1d(BANDS, BANDS, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = self.conv(x).unsqueeze(-1)
        return torch.cat([y, y], dim=-1).reshape(1, BANDS, 2 * HOPS)


def test_unsqueeze_axes_positive_writes_the_axis_unsqueeze_names(tmp_path: Path) -> None:
    torch.manual_seed(10)
    graph = ptq_espdl.quantize(TrailingAxis().eval(), calib(np.random.default_rng(23)), tmp_path, RUNGS)
    plain, plain_tensors = espdl_graph(export_espdl.export(graph, tmp_path / "plain.espdl"))
    with esp_ppq_patches.applied(["unsqueeze_axes_positive"]):
        nodes, tensors = espdl_graph(export_espdl.export(graph, tmp_path / "positive.espdl"))
    ((_, (_, axes)),) = [node for node in nodes.values() if node[0] == "Unsqueeze"]
    ((_, (_, plain_axes)),) = [node for node in plain.values() if node[0] == "Unsqueeze"]
    assert plain_tensors[plain_axes].tolist() == [-1] and tensors[axes].tolist() == [3]
    assert all(np.array_equal(tensors[k], v) for k, v in plain_tensors.items() if k != plain_axes)
