"""esp_ppq_patches.quantise: quantised as ESP-PPQ does, each graph shows the bug board B hit, and fixed it does not."""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from esp_ppq.executor.torch import TorchExecutor  # noqa: E402
from torch import nn  # noqa: E402
from torch.nn import functional  # noqa: E402

from esp_ppq_cases import BANDS, HOPS, RUNGS, calib, exponent, export_info  # noqa: E402
from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl  # noqa: E402


class InputReadTwice(nn.Module):
    """The graph input read by a convolution and by a Sub whose output needs a coarser scale than the input."""

    def __init__(self) -> None:
        super().__init__()
        self.conv = nn.Conv1d(BANDS, BANDS, 1)
        nn.init.constant_(self.conv.weight, 1.0)

    def forward(self, x):
        return self.conv(x) - x


def sub_reads(info: str) -> list[str]:
    (line,) = re.findall(r"= Sub\[.*", info)
    return re.findall(r"%([^,)\s]+)", line.split("](", 1)[1])


def test_requantise_graph_inputs_brings_the_input_to_the_subs_exponent(tmp_path: Path) -> None:
    graph = ptq_espdl.quantize(InputReadTwice(), calib(np.random.default_rng(2)), tmp_path, RUNGS)
    (name,) = graph.inputs
    info = export_info(graph, tmp_path / "patched.espdl", ["requantise_graph_inputs"])
    (out,) = re.findall(r"%(\S+) = Sub\[", info)
    requantised = dict(re.findall(r"%(\S+) = RequantizeLinear\[[^\]]*\]\(%([^,]+),", info))
    assert [requantised.get(v) for v in sub_reads(info)].count(name) == 1
    assert {exponent(info, v) for v in sub_reads(info)} == {exponent(info, out)} != {exponent(info, name)}

    as_is = export_info(graph, tmp_path / "as_is.espdl", [])
    (out,) = re.findall(r"%(\S+) = Sub\[", as_is)
    assert name in sub_reads(as_is)
    assert exponent(as_is, name) != exponent(as_is, out)


class SlicedFront(nn.Module):
    """The graph input reaches its causal convolutions through a Slice, as the ctc net's features do: a 2D one over
    (hops, bands), then a 1D one over hops."""

    def __init__(self) -> None:
        super().__init__()
        self.bands = BANDS - 2
        self.conv2d = nn.Conv2d(1, 2, (3, 3), padding=(0, 1))
        self.conv1d = nn.Conv1d(2 * self.bands, 4, 3)

    def forward(self, x):
        y = self.conv2d(functional.pad(x[:, : self.bands].transpose(1, 2).unsqueeze(1), (0, 0, 2, 0)))
        y = y.permute(0, 1, 3, 2).reshape(y.shape[0], 2 * self.bands, -1)
        return self.conv1d(functional.pad(y, (2, 0)))


def cached_lengths(info: str) -> list[int]:
    """The length each StreamingCache's input has along the cache's frame axis, in esp-dl's layout."""
    lengths = []
    for axis, var in re.findall(r"= StreamingCache\[frame_axis = (\d+),[^\]]*\]\(%([^)]+)\)", info):
        shape = re.search(rf"^%{re.escape(var)}\[\w+, ([\dx]+)\]", info, re.M).group(1).split("x")
        lengths.append(int(shape[int(axis)]))
    return lengths


def test_conv_caches_along_time_stream_along_hops_when_a_slice_feeds_the_convolutions(tmp_path: Path) -> None:
    torch.manual_seed(3)
    graph = ptq_espdl.quantize(SlicedFront(), calib(np.random.default_rng(3)), tmp_path, RUNGS)
    step = [1, BANDS, 1]
    assert cached_lengths(export_info(graph, tmp_path / "patched.espdl", ["conv_caches_along_time"], step)) == [
        HOPS,
        HOPS,
    ]
    assert HOPS not in cached_lengths(export_info(graph, tmp_path / "as_is.espdl", [], step))


class NarrowSlice(nn.Module):
    """The graph input sliced into wide and narrow bands, as the ctc net splits mel bands from pitch."""

    def __init__(self) -> None:
        super().__init__()
        self.wide = nn.Conv1d(BANDS - 2, 4, 1)
        self.narrow = nn.Conv1d(2, 4, 1)

    def forward(self, x):
        return self.wide(x[:, : BANDS - 2]) + self.narrow(x[:, BANDS - 2 :])


def narrow_calib(rng: np.random.Generator) -> list:
    """Wide bands eight times the spread of the narrow ones, so the narrow ones alone fit a finer exponent."""
    spread = np.r_[np.full(BANDS - 2, 4.0), np.full(2, 0.5)][:, None].astype(np.float32)
    return [torch.from_numpy(rng.normal(0, 1, (1, BANDS, HOPS)).astype(np.float32) * spread) for _ in range(4)]


def slice_exponents(info: str) -> dict[str, int]:
    return {out: exponent(info, out) for out in re.findall(r"%(\S+) = Slice\[", info)}


def test_fuse_passive_ops_on_graph_inputs_keeps_each_slice_on_the_inputs_exponent(tmp_path: Path) -> None:
    calib = narrow_calib(np.random.default_rng(6))
    as_is = ptq_espdl.quantize(NarrowSlice(), calib, tmp_path / "as_is", RUNGS)
    assert len(export_espdl.rescaling_passive_ops(as_is)) == 1
    with pytest.raises(ValueError, match="rescale at their output"):
        export_espdl.export(as_is, tmp_path / "as_is.espdl")

    with esp_ppq_patches.applied(["fuse_passive_ops_on_graph_inputs"]):
        graph = ptq_espdl.quantize(NarrowSlice(), calib, tmp_path / "fused", RUNGS)
    assert export_espdl.rescaling_passive_ops(graph) == []
    info = export_info(graph, tmp_path / "fused.espdl", [])
    (name,) = graph.inputs
    assert set(slice_exponents(info).values()) == {exponent(info, name)}


def test_simplify_without_bn_fusion_hands_onnxsim_skip_fuse_bn(monkeypatch) -> None:
    from esp_ppq.api import espdl_interface

    seen = []
    monkeypatch.setattr(espdl_interface, "simplify", lambda model, **kwargs: seen.append(kwargs) or (model, True))
    with esp_ppq_patches.applied(["simplify_without_bn_fusion"]):
        espdl_interface.simplify("graph")
    espdl_interface.simplify("graph")
    assert seen == [{"skip_fuse_bn": True}, {}]


def test_fuse_erf_gelu_loads_torch_gelu_as_one_gelu(tmp_path: Path) -> None:
    from esp_ppq.api.interface import load_onnx_graph

    from srpipe.compress.quant import onnx_export

    torch.manual_seed(0)
    model = nn.Sequential(nn.Conv1d(BANDS, BANDS, 1), nn.GELU()).eval()
    x = np.random.default_rng(0).normal(0, 1, (1, BANDS, HOPS)).astype(np.float32)
    path = str(onnx_export.checked(model, (x,), tmp_path / "gelu.onnx", RUNGS["onnx_rtol"]))
    plain = {op.type for op in load_onnx_graph(path).operations.values()}
    with esp_ppq_patches.applied(["fuse_erf_gelu"]):
        fused = {op.type for op in load_onnx_graph(path).operations.values()}
    assert "Erf" in plain and "Gelu" not in plain
    assert "Gelu" in fused and "Erf" not in fused


class TanhGelu(nn.Module):
    """A linear layer, then GELU in its tanh form written as transformers' NewGELUActivation writes it."""

    def __init__(self) -> None:
        super().__init__()
        self.linear = nn.Linear(BANDS, BANDS)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = self.linear(x)
        return 0.5 * y * (1.0 + torch.tanh(math.sqrt(2.0 / math.pi) * (y + 0.044715 * torch.pow(y, 3.0))))


def test_fuse_tanh_gelu_loads_the_chain_as_one_gelu_of_the_same_function(tmp_path: Path) -> None:
    from esp_ppq.api.interface import load_onnx_graph

    from srpipe.compress.quant import onnx_export

    torch.manual_seed(0)
    model = TanhGelu().eval()
    x = np.random.default_rng(0).normal(0, 2, (1, HOPS, BANDS)).astype(np.float32)
    path = str(onnx_export.checked(model, (x,), tmp_path / "gelu.onnx", RUNGS["onnx_rtol"]))
    plain = {op.type for op in load_onnx_graph(path).operations.values()}
    with esp_ppq_patches.applied(["fuse_tanh_gelu"]):
        graph = load_onnx_graph(path)
        fused = [op for op in graph.operations.values() if op.type == "Gelu"]
        got = TorchExecutor(graph, device="cpu").forward(inputs=[torch.from_numpy(x)])[0].numpy()
    assert {"Tanh", "Pow"} <= plain and "Gelu" not in plain
    assert not {"Tanh", "Pow"} & {op.type for op in graph.operations.values()}
    assert [op.attributes["approximate"] for op in fused] == ["tanh"]
    with torch.no_grad():
        np.testing.assert_allclose(got, model(torch.from_numpy(x)).numpy(), rtol=1e-5, atol=1e-6)
