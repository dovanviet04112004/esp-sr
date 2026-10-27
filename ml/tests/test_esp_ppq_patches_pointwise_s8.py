"""esp_ppq_patches.ops.pointwise_s8: 1x1 convolutions, residual Adds, patches and Concats of slices as
PointwiseS8."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from esp_ppq.executor.torch import TorchExecutor  # noqa: E402
from torch import nn  # noqa: E402

from esp_ppq_cases import HOPS, RUNGS, espdl_graph, exponent, round_shift  # noqa: E402
from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl  # noqa: E402


class Pointwises(nn.Module):
    """Rows of 24 values through 1x1 convolutions as ReDimNet2 b0 runs them: 24 to 24 added to the rows, as a TCM's
    pwconv1; 24 to 48 added to another such map, as a stage's exp_dim; 48 to 64, then 64 to 24 without bias."""

    def __init__(self) -> None:
        super().__init__()
        self.tcm, self.side, self.wide = nn.Conv1d(24, 24, 1), nn.Conv1d(24, 48, 1), nn.Conv1d(24, 48, 1)
        self.narrow, self.back = nn.Conv1d(48, 64, 1), nn.Conv1d(64, 24, 1, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        a = x + self.tcm(torch.relu(x))
        return self.back(self.narrow(self.side(a) + self.wide(a)))


def pointwise_s8_espdl(rows: np.ndarray, laid: np.ndarray, bias, shift: int, residual, residual_shift: int):
    """PointwiseS8's int8 output for int8 rows [T, K] from its weights [Np / 16][K][16], bias [N] or none, shift and
    residual rows [T, N] or none, as esp-dl's Conv, the RequantizeLinear of the residual and their Add give it."""
    k = rows.shape[1]
    weights = laid.astype(np.int64).transpose(0, 2, 1).reshape(-1, k)
    n = weights.shape[0] if bias is None else bias.size
    acc = rows.astype(np.int64) @ weights[:n].T + (0 if bias is None else bias.astype(np.int64))
    y = round_shift(acc, shift)
    if residual is not None:
        y = np.clip(y + round_shift(residual.astype(np.int64), residual_shift), -128, 127)
    return y


def test_pointwise_s8_exports_each_1x1_convolution_and_its_residual_add_as_one_op(tmp_path: Path) -> None:
    torch.manual_seed(6)
    rng = np.random.default_rng(29)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 24, HOPS)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(Pointwises().eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    with esp_ppq_patches.applied(["pointwise_s8"]):
        path = export_espdl.export(graph, tmp_path / "pointwises.espdl")
    nodes, tensors = espdl_graph(path)
    ops = [op for op, _ in nodes.values()]
    assert ops.count("PointwiseS8") == 5
    assert "Conv" not in ops and ops.count("Add") <= 1
    info = path.with_suffix(".info").read_text()
    transposed = dict(re.findall(r"%(\S+) = Transpose\[.*?\]\(%([^)]+)\)", info))
    joined = 0
    for made, attrs, inputs in re.findall(r"%(\S+) = PointwiseS8\[(.*?)\]\(([^)]*)\)", info):
        values = dict(re.findall(r"(\w+) = (-?\d+)", attrs))
        shift, n, biased, residual_shift = (int(values[a]) for a in ("shift", "columns", "biased", "residual_shift"))
        names = [v.strip().lstrip("%") for v in inputs.split(",")]
        b = names[2] if biased else None
        maps = [names[0], made] + ([names[2 + biased]] if residual_shift >= 0 else [])
        got = TorchExecutor(graph, device="cpu").forward(
            inputs=calib[0], output_names=[transposed.get(m, m) for m in maps]
        )
        rows, want, *residual = (
            torch.round(v[0].T / 2.0 ** exponent(info, m)).to(torch.int64).numpy()
            for v, m in zip(got, maps, strict=True)
        )
        joined += bool(residual)
        bias, res = (tensors[b][:n] if b else None), (residual[0] if residual else None)
        out = pointwise_s8_espdl(rows, tensors[names[1]], bias, shift, res, residual_shift)[:, :n]
        assert np.array_equal(out, want), made
        off = pointwise_s8_espdl(rows, tensors[names[1]], bias, shift + 1, res, residual_shift)[:, :n]
        assert not np.array_equal(off, want)
    assert joined == 2


class Patches(nn.Module):
    """Convolutions whose kernel is their stride, as ReDimNet2 b0's stages 3 and 5 shrink their maps: 2x2 from 24 to
    48 channels, then 4x2 from 48 to 32."""

    def __init__(self) -> None:
        super().__init__()
        self.square, self.tall = nn.Conv2d(24, 48, 2, stride=2), nn.Conv2d(48, 32, (4, 2), stride=(4, 2))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.tall(self.square(x))


def patch_rows(laid: np.ndarray, kh: int, kw: int) -> np.ndarray:
    """A [T, F, C] map's non-overlapping kh x kw patches as rows [T / kh * F / kw, kh * kw * C], each patch's rows in
    turn, its columns, then channels."""
    t, f, c = laid.shape
    return laid.reshape(t // kh, kh, f // kw, kw, c).transpose(0, 2, 1, 3, 4).reshape(-1, kh * kw * c)


def test_pointwise_s8_reads_a_convolution_of_its_stride_as_patches(tmp_path: Path) -> None:
    torch.manual_seed(8)
    rng = np.random.default_rng(31)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 24, 16, 8)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(Patches().eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    with esp_ppq_patches.applied(["pointwise_s8"]):
        path = export_espdl.export(graph, tmp_path / "patches.espdl")
    nodes, tensors = espdl_graph(path)
    assert [op for op, _ in nodes.values()].count("PointwiseS8") == 2
    info = path.with_suffix(".info").read_text()
    transposed = dict(re.findall(r"%(\S+) = Transpose\[.*?\]\(%([^)]+)\)", info))
    for made, attrs, inputs in re.findall(r"%(\S+) = PointwiseS8\[(.*?)\]\(([^)]*)\)", info):
        kh, kw = (int(v) for v in re.search(r"patch = \[(\d+), (\d+)\]", attrs).groups())
        shift = int(re.search(r"shift = (\d+)", attrs).group(1))
        names = [v.strip().lstrip("%") for v in inputs.split(",")]
        maps = [names[0], made]
        got = TorchExecutor(graph, device="cpu").forward(
            inputs=calib[0], output_names=[transposed.get(m, m) for m in maps]
        )
        x, want = (
            torch.round(v[0].permute(1, 2, 0) / 2.0 ** exponent(info, m)).to(torch.int64).numpy()
            for v, m in zip(got, maps, strict=True)
        )
        rows, n = patch_rows(x, kh, kw), want.shape[-1]
        bias = tensors[names[2]][:n]
        out = pointwise_s8_espdl(rows, tensors[names[1]], bias, shift, None, -1)
        assert np.array_equal(out, want.reshape(-1, n)), made
        assert not np.array_equal(
            pointwise_s8_espdl(rows, tensors[names[1]], bias, shift + 1, None, -1), want.reshape(-1, n)
        )


class WidePatches(nn.Module):
    """Two 2x2 convolutions of stride 2: 96 to 192 channels, 72 KB of weights past the data cache as b3's stage 5
    holds, then 192 to 48, 36 KB between half the data cache and all of it."""

    def __init__(self) -> None:
        super().__init__()
        self.wide, self.middle = nn.Conv2d(96, 192, 2, stride=2), nn.Conv2d(192, 48, 2, stride=2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.middle(self.wide(x))


def test_pointwise_s8_reads_patches_whose_weights_outgrow_the_data_cache(tmp_path: Path) -> None:
    torch.manual_seed(9)
    rng = np.random.default_rng(33)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 96, 8, 8)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(WidePatches().eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    with esp_ppq_patches.applied(["pointwise_s8"]):
        path = export_espdl.export(graph, tmp_path / "wide.espdl")
    nodes, tensors = espdl_graph(path)
    ops = [op for op, _ in nodes.values()]
    assert ops.count("PointwiseS8") == 1 and ops.count("Conv") == 1
    info = path.with_suffix(".info").read_text()
    transposed = dict(re.findall(r"%(\S+) = Transpose\[.*?\]\(%([^)]+)\)", info))
    ((made, attrs, inputs),) = re.findall(r"%(\S+) = PointwiseS8\[(.*?)\]\(([^)]*)\)", info)
    assert re.search(r"patch = \[2, 2\]", attrs)
    shift = int(re.search(r"shift = (\d+)", attrs).group(1))
    names = [v.strip().lstrip("%") for v in inputs.split(",")]
    maps = [names[0], made]
    got = TorchExecutor(graph, device="cpu").forward(inputs=calib[0], output_names=[transposed.get(m, m) for m in maps])
    x, want = (
        torch.round(v[0].permute(1, 2, 0) / 2.0 ** exponent(info, m)).to(torch.int64).numpy()
        for v, m in zip(got, maps, strict=True)
    )
    rows, n = patch_rows(x, 2, 2), want.shape[-1]
    assert tensors[names[1]].size > esp_ppq_patches.DATA_CACHE_BYTES
    out = pointwise_s8_espdl(rows, tensors[names[1]], tensors[names[2]][:n], shift, None, -1)
    assert np.array_equal(out, want.reshape(-1, n))
    assert not np.array_equal(
        pointwise_s8_espdl(rows, tensors[names[1]], tensors[names[2]][:n], shift + 1, None, -1), want.reshape(-1, n)
    )


def test_pointwise_s8_joins_a_concat_of_its_slices_back_into_one_op(tmp_path: Path) -> None:
    torch.manual_seed(5)
    rng = np.random.default_rng(18)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 576, HOPS)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(nn.Conv1d(576, 128, 1).eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    with esp_ppq_patches.applied(["cache_sized_convolutions", "pointwise_s8"]):
        path = export_espdl.export(graph, tmp_path / "joined.espdl")
    nodes, tensors = espdl_graph(path)
    ops = [op for op, _ in nodes.values()]
    assert ops.count("PointwiseS8") == 1 and "Concat" not in ops
    info = path.with_suffix(".info").read_text()
    transposed = dict(re.findall(r"%(\S+) = Transpose\[.*?\]\(%([^)]+)\)", info))
    ((made, attrs, inputs),) = re.findall(r"%(\S+) = PointwiseS8\[(.*?)\]\(([^)]*)\)", info)
    shift, columns = (int(re.search(name + r" = (\d+)", attrs).group(1)) for name in ("shift", "columns"))
    x, w, b = (v.strip().lstrip("%") for v in inputs.split(","))
    assert columns == 128 and tensors[w].shape == (8, 576, 16)
    maps = [x, made]
    got = TorchExecutor(graph, device="cpu").forward(inputs=calib[0], output_names=[transposed.get(m, m) for m in maps])
    rows, want = (
        torch.round(v[0].T / 2.0 ** exponent(info, m)).to(torch.int64).numpy() for v, m in zip(got, maps, strict=True)
    )
    assert np.array_equal(pointwise_s8_espdl(rows, tensors[w], tensors[b], shift, None, -1), want)
    assert not np.array_equal(pointwise_s8_espdl(rows, tensors[w], tensors[b], shift + 1, None, -1), want)
