"""esp_ppq_patches.ops.group_norm_s8: a GroupNorm chain of ReDimNet2's agg_gnorm as one GroupNormS8, giving the
integers of its LayerNorm, Mul and Add."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from esp_ppq.executor.torch import TorchExecutor  # noqa: E402
from torch import nn  # noqa: E402
from torch.nn import functional  # noqa: E402

from esp_ppq_cases import HOPS, RUNGS, espdl_graph, exponent, export_info, round_shift  # noqa: E402
from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl  # noqa: E402

NORMED_CHANNELS, GROUPS, MAP_BANDS = 24, 4, 4
# StageSum takes terms of 16 channels a step.
SUMMED_CHANNELS = 32


class GroupNormed(nn.Module):
    """A 1x1 convolution, a GroupNorm of GROUPS groups as quant.GroupNorm1d writes ReDimNet2's agg_gnorm, another 1x1
    convolution: the norm's map laid channels last on either side, as in b3."""

    def __init__(self, channels: int = NORMED_CHANNELS) -> None:
        super().__init__()
        self.front = nn.Conv1d(channels, channels, 1)
        self.back = nn.Conv1d(channels, channels, 1)
        self.weight = nn.Parameter(torch.linspace(0.4, 1.6, channels))
        self.bias = nn.Parameter(torch.linspace(-0.5, 0.7, channels))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.front(x)
        b, c, t = x.shape
        grouped = x.reshape(b, GROUPS, c // GROUPS * t)
        normed = functional.layer_norm(grouped, (grouped.shape[-1],), eps=1e-5).reshape(b, c, t)
        return self.back(normed * self.weight[:, None] + self.bias[:, None])


class MapGroupNormed(GroupNormed):
    """GroupNormed over a 2-d map: a 1x1 convolution over (c, frames, bands), the map read as quant.To1d reads it,
    bands times channels, then the same chain, as b3's stem_gnorm reads its stem."""

    def __init__(self) -> None:
        super().__init__()
        self.front = nn.Conv2d(NORMED_CHANNELS // MAP_BANDS, NORMED_CHANNELS // MAP_BANDS, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = self.front(x)
        b, c, t, f = y.shape
        grouped = y.permute(0, 3, 1, 2).reshape(b, GROUPS, f * c // GROUPS * t)
        normed = functional.layer_norm(grouped, (grouped.shape[-1],), eps=1e-5).reshape(b, f * c, t)
        return self.back(normed * self.weight[:, None] + self.bias[:, None])


class RepeatedGroupNormed(GroupNormed):
    """GroupNormed over a map whose frames a nearest Resize doubles, as b3's stages 2-5 upsample their frames before
    their agg_gnorm."""

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = functional.interpolate(self.front(x)[:, :, ::2], scale_factor=2.0, mode="nearest")
        b, c, t = y.shape
        grouped = y.reshape(b, GROUPS, c // GROUPS * t)
        normed = functional.layer_norm(grouped, (grouped.shape[-1],), eps=1e-5).reshape(b, c, t)
        return self.back(normed * self.weight[:, None] + self.bias[:, None])


class RepeatedNormSummed(RepeatedGroupNormed):
    """RepeatedGroupNormed whose norm, weighted per channel, adds to the doubled map's source weighted per channel,
    as b3's stages 2-5 reach weigth1d."""

    def __init__(self) -> None:
        super().__init__(SUMMED_CHANNELS)
        self.weights = nn.ModuleList(
            nn.Conv1d(SUMMED_CHANNELS, SUMMED_CHANNELS, 1, groups=SUMMED_CHANNELS, bias=False) for _ in range(2)
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        source = self.front(x)
        y = functional.interpolate(source[:, :, ::2], scale_factor=2.0, mode="nearest")
        b, c, t = y.shape
        grouped = y.reshape(b, GROUPS, c // GROUPS * t)
        normed = functional.layer_norm(grouped, (grouped.shape[-1],), eps=1e-5).reshape(b, c, t)
        summed = self.weights[0](source) + self.weights[1](normed * self.weight[:, None] + self.bias[:, None])
        return self.back(summed)


def group_norm_s8_espdl(q: np.ndarray, w: np.ndarray, b: np.ndarray, attrs: dict, e_x: int) -> np.ndarray:
    """GroupNormS8's int8 output [C, T] for int8 q [C, T]: each group's LayerNorm as layernorm_as_espdl takes it, its
    squares summed channel after channel, frame after frame; then the Mul's rounding and the saturating Add."""
    f32, w, b = torch.float32, w.reshape(-1), b.reshape(-1)
    c, t = q.shape
    width, s_in = c // attrs["groups"], torch.tensor(2.0**e_x, dtype=f32)
    n = width * t
    per_value = torch.ones(1) / torch.full((1,), float(n)) * (s_in * s_in)
    out = np.empty_like(q)
    for g in range(attrs["groups"]):
        rows = torch.from_numpy(q[g * width : (g + 1) * width].astype(np.float32))
        mean = torch.tensor(float(rows.double().sum()) * (1.0 / n), dtype=torch.float64).to(f32)
        squares = torch.zeros(1)
        for v in rows.flatten():
            d = v.reshape(1) - mean
            squares = esp_ppq_patches.fma32(d, d, squares)
        eps = torch.full((1,), attrs["epsilon"], dtype=f32)
        scale = torch.ones(1) / esp_ppq_patches.sqrt_newton(esp_ppq_patches.fma32(per_value, squares, eps)) * s_in
        r = torch.tensor(attrs["gamma"], dtype=f32) * scale * (rows - mean) + torch.tensor(attrs["beta"], dtype=f32)
        normed = torch.clamp(torch.floor(r * torch.tensor(2.0 ** -attrs["norm_exponent"], dtype=f32) + 0.5), -128, 127)
        product = normed.numpy().astype(np.int64) * w[g * width : (g + 1) * width, None].astype(np.int64)
        summed = round_shift(product, attrs["mul_shift"]) + b[g * width : (g + 1) * width, None].astype(np.int64)
        out[g * width : (g + 1) * width] = np.clip(summed, -128, 127)
    return out


@pytest.mark.parametrize("kind", ["rows", "map", "repeated"])
def test_group_norm_s8_runs_the_chain_as_one_op_giving_its_integers(tmp_path: Path, kind: str) -> None:
    torch.manual_seed(11)
    rng = np.random.default_rng(41)
    on_map = kind == "map"
    shape = (1, NORMED_CHANNELS // MAP_BANDS, HOPS, MAP_BANDS) if on_map else (1, NORMED_CHANNELS, HOPS)
    calib = [torch.from_numpy(rng.normal(0, 1, shape).astype(np.float32)) for _ in range(2)]
    net = {"rows": GroupNormed, "map": MapGroupNormed, "repeated": RepeatedGroupNormed}[kind]()
    graph = ptq_espdl.quantize(net.eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    with esp_ppq_patches.applied(["layernorm_s8", "group_norm_s8"]):
        path = export_espdl.export(graph, tmp_path / "grouped.espdl")
    nodes, tensors = espdl_graph(path)
    ops = [op for op, _ in nodes.values()]
    assert ops.count("GroupNormS8") == 1
    assert not {"LayerNormS8", "LayerNormalization", "Mul", "Add", "Transpose"} & set(ops)
    info = path.with_suffix(".info").read_text()
    ((made, attrs, inputs),) = re.findall(r"%(\S+) = GroupNormS8\[(.*?)\]\(([^)]*)\)", info)
    x, w, b = (v.strip().lstrip("%") for v in inputs.split(","))
    found = dict(re.findall(r"(\w+) = (-?[\d.e+-]+)", attrs))
    fields = {k: int(found[k]) for k in ("groups", "norm_exponent", "mul_shift", "repeat")}
    assert fields["repeat"] == (2 if kind == "repeated" else 1)
    resized = re.findall(r"= Resize\[.*?\]\(%([^,)\s]+)", info)
    assert resized == ([made] if kind == "repeated" else [])
    frames = int(re.search(rf"%{re.escape(made)}\[INT8, 1x(\d+)x", info).group(1))
    assert frames == HOPS // fields["repeat"]
    fields |= {k: float(found[k]) for k in ("epsilon", "gamma", "beta")}
    (add,) = [op.outputs[0].name for op in graph.operations.values() if op.type == "Add"]
    with esp_ppq_patches.applied(["layernorm_as_espdl"]):
        got = TorchExecutor(graph, device="cpu").forward(inputs=calib[0], output_names=[x, add])
    q, want = (
        torch.round(v[0] / 2.0 ** exponent(info, m)).to(torch.int64).numpy()
        for v, m in zip(got, (x, made), strict=True)
    )
    if on_map:
        q = q.transpose(2, 0, 1).reshape(NORMED_CHANNELS, HOPS)
    q = np.repeat(q, fields["repeat"], axis=1)
    assert np.array_equal(group_norm_s8_espdl(q, tensors[w], tensors[b], fields, exponent(info, x)), want)
    off = fields | {"mul_shift": fields["mul_shift"] + 1}
    assert not np.array_equal(group_norm_s8_espdl(q, tensors[w], tensors[b], off, exponent(info, x)), want)


def test_group_norm_s8_leaves_its_resize_to_the_stage_sum_reading_it(tmp_path: Path) -> None:
    torch.manual_seed(11)
    rng = np.random.default_rng(41)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, SUMMED_CHANNELS, HOPS)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(RepeatedNormSummed().eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    patches = ["channel_scales_as_mul", "layernorm_s8", "group_norm_s8", "stage_sum"]
    info = export_info(graph, tmp_path / "summed.espdl", patches)
    assert re.findall(r"= GroupNormS8\[.*?repeat = (\d+)", info) == ["2"]
    assert re.findall(r"= StageSum\[.*rows = \[([\d, ]+)\]", info) == ["1, 2"]
    assert not re.findall(r"= (?:Resize|Mul|Add)\[", info)
