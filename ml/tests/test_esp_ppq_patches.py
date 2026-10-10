"""The ESP-PPQ fixes the ctc and speaker branches opt into: quantised and exported as ESP-PPQ does, each graph shows
the bug board B hit, and fixed it does not; every fix is undone when its block ends, so other branches run ESP-PPQ as
is."""

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

from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl  # noqa: E402

BANDS, HOPS = 8, 40
RUNGS = ptq_espdl.ladder("command_ctc")


def calib(rng: np.random.Generator) -> list:
    return [torch.from_numpy(rng.normal(0, 1, (1, BANDS, HOPS)).astype(np.float32)) for _ in range(4)]


def export_info(graph, out: Path, patches: list[str], streaming_input_shape: list[int] | None = None) -> str:
    with esp_ppq_patches.applied(patches):
        path = export_espdl.export(graph, out, streaming_input_shape=streaming_input_shape)
    return path.with_suffix(".info").read_text()


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


def exponent(info: str, var: str) -> int:
    return int(re.search(rf"%{re.escape(var)}\[[^\]]*\], exponents: \[(-?\d+)\]", info).group(1))


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


NORM_HOPS, NORM_WIDTH, NORM_EPS = 4096, 256, 1e-5


class ChannelNorm(nn.Module):
    """A norm over the last axis with a float scale a channel, in the form ESP-PPQ fuses into RMSNormalization."""

    def __init__(self) -> None:
        super().__init__()
        self.scale = nn.Parameter(torch.linspace(0.5, 1.5, NORM_WIDTH))

    def forward(self, x):
        return x / torch.sqrt(x.pow(2).mean(dim=-1, keepdim=True) + NORM_EPS) * self.scale


def norm_graph(tmp_path: Path):
    rng = np.random.default_rng(8)
    calib = [torch.from_numpy(rng.normal(0, 2, (1, NORM_HOPS, NORM_WIDTH)).astype(np.float32)) for _ in range(2)]
    with esp_ppq_patches.applied(["rmsnorm_as_espdl"]):
        graph = ptq_espdl.quantize(ChannelNorm(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    (op,) = [op for op in graph.operations.values() if op.type == "RMSNormalization"]
    return graph, op


def espdl_rmsnorm(q: np.ndarray, scale: np.ndarray, e_in: int, e_out: int) -> np.ndarray:
    """esp-dl's S3 int8 RMSNormalization of q over its last axis, each float32 step as dl_module_rms_normalization.hpp
    and dl_tie728_rmsnorm_s8 take it."""
    s_in, s_out = np.float32(2.0**e_in), np.float32(2.0**e_out)
    sum_sq = (q.astype(np.int64) ** 2).sum(-1, keepdims=True).astype(np.float32)
    mean_sq = sum_sq * (s_in * s_in) / np.float32(q.shape[-1])
    rms = np.float32(1.0) / np.sqrt(mean_sq + np.float32(NORM_EPS)) * (s_in / s_out)
    return np.clip(np.floor(q.astype(np.float32) * rms * scale + np.float32(0.5)), -128, 127)


def test_rmsnorm_as_espdl_simulates_each_norm_as_the_chip_rounds_it(tmp_path: Path) -> None:
    """ESP-PPQ as is rounds about one output in a few million a step off the chip; four batches of this seed hold
    two such."""
    graph, op = norm_graph(tmp_path)
    e_in, e_out = (ptq_espdl.exponent_of(c) for c in (op.input_quant_config[0], op.output_quant_config[0]))
    scale = op.inputs[1].value.detach().numpy().astype(np.float32)
    patched, as_is = ptq_espdl.Simulator(graph, ["rmsnorm_as_espdl"]), ptq_espdl.Simulator(graph)
    rng, step, flips = np.random.default_rng(11), np.float32(2.0**e_out), 0
    for _ in range(4):
        q = rng.normal(0, 40, (1, NORM_HOPS, NORM_WIDTH)).round().clip(-128, 127)
        x, want = q.astype(np.float32) * np.float32(2.0**e_in), espdl_rmsnorm(q, scale, e_in, e_out)
        assert np.array_equal(patched(x) / step, want)
        off = as_is(x) / step - want
        assert np.abs(off).max() <= 1
        flips += np.count_nonzero(off)
    assert flips > 0


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


def espdl_layernorm(q: np.ndarray, gamma: np.ndarray, beta: np.ndarray, s_in, s_out, eps) -> np.ndarray:
    """esp-dl's int8 LayerNormalization of q over its last axis, each float32 step as dl_module_layer_normalization.hpp
    takes it, sqrt_newton as dl_math.hpp has it."""
    f = np.float32
    mean = (q.astype(np.float64).sum(-1, keepdims=True) / q.shape[-1]).astype(f)
    variance = np.zeros_like(mean)
    for j in range(q.shape[-1]):
        variance = variance + (q[..., j : j + 1].astype(f) - mean) ** 2
    variance = variance * (f(s_in) * f(s_in)) / f(q.shape[-1])
    x = variance + f(eps)
    root, moving = x.copy(), np.ones(x.shape, dtype=bool)
    while moving.any():
        step = (root + x / root) * f(0.5)
        root, moving = np.where(moving, step, root), moving & (np.abs(step - root) > f(1e-5))
    inv_std = f(1.0) / root
    result = (q.astype(f) * f(s_in) - mean * f(s_in)) * inv_std * gamma + beta
    return np.clip(np.floor(result * (f(1.0) / f(s_out)) + f(0.5)), -128, 127)


def test_layernorm_as_espdl_simulates_each_norm_as_the_chip_rounds_it(tmp_path: Path) -> None:
    """ESP-PPQ as is rounds a half step now and then the other way from the chip; this seed holds such outputs."""
    graph, op = layernorm_graph(tmp_path)
    s_in, s_out = (float(c.scale) for c in (op.input_quant_config[0], op.output_quant_config[0]))
    gamma, beta = (op.inputs[i].value.detach().numpy().astype(np.float32) for i in (1, 2))
    gamma = np.clip(np.floor(gamma / float(op.input_quant_config[1].scale) + 0.5), -128, 127)
    gamma = (gamma * float(op.input_quant_config[1].scale)).astype(np.float32)
    beta = np.clip(np.floor(beta / float(op.input_quant_config[2].scale) + 0.5), -128, 127)
    beta = (beta * float(op.input_quant_config[2].scale)).astype(np.float32)
    patched, as_is = ptq_espdl.Simulator(graph, ["layernorm_as_espdl"]), ptq_espdl.Simulator(graph)
    rng, flips = np.random.default_rng(12), 0
    for _ in range(4):
        q = rng.normal(0, 30, (1, LN_HOPS, LN_WIDTH)).round().clip(-128, 127)
        x = q.astype(np.float32) * np.float32(s_in)
        want = espdl_layernorm(q, gamma, beta, s_in, s_out, op.attributes.get("epsilon", 1e-5))
        assert np.array_equal(patched(x) / np.float32(s_out), want)
        off = as_is(x) / np.float32(s_out) - want
        assert np.abs(off).max() <= 1
        flips += np.count_nonzero(off)
    assert flips > 0


SOFTMAX_ROWS, SOFTMAX_WIDTH = 512, 148


def espdl_softmax(q: np.ndarray, s_in: float) -> np.ndarray:
    """esp-dl's int8 Softmax of q over its last axis as dl_module_softmax.hpp's forward_lut takes it: exp from a table
    of the input's grid, the row summed one value after another in float32, each value divided by the sum."""
    table = np.exp(np.arange(-128, 128, dtype=np.float64) * s_in).astype(np.float32)
    e = table[q.astype(np.int64) + 128]
    total = np.zeros((*e.shape[:-1], 1), np.float32)
    for i in range(e.shape[-1]):
        total = total + e[..., i : i + 1]
    return e / total


def test_softmax_as_espdl_simulates_each_row_as_the_chip_sums_it(tmp_path: Path) -> None:
    """ESP-PPQ as is takes the row's maximum out before exp and sums otherwise, so its float probabilities are not the
    chip's; quantised at the next op's grid they stay within a step, apart only on a half step."""
    net = nn.Sequential(nn.Linear(SOFTMAX_WIDTH, SOFTMAX_WIDTH), nn.Softmax(dim=-1), nn.Linear(SOFTMAX_WIDTH, 8))
    rng = np.random.default_rng(13)
    calib = [torch.from_numpy(rng.normal(0, 2, (1, SOFTMAX_ROWS, SOFTMAX_WIDTH)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(net.eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    (op,) = [op for op in graph.operations.values() if op.type == "Softmax"]
    (reader,) = op.outputs[0].dest_ops
    s_in, s_next = float(op.input_quant_config[0].scale), np.float32(reader.input_quant_config[0].scale)
    executor, name = TorchExecutor(graph, device="cpu"), op.outputs[0].name
    for _ in range(4):
        x = torch.from_numpy(rng.normal(0, 2, (1, SOFTMAX_ROWS, SOFTMAX_WIDTH)).astype(np.float32))
        q = np.round(executor.forward(inputs=[x], output_names=[op.inputs[0].name])[0].numpy() / s_in)
        want = espdl_softmax(q, s_in)
        with esp_ppq_patches.applied(["softmax_as_espdl"]):
            patched = executor.forward(inputs=[x], output_names=[name])[0].numpy()
        as_is = executor.forward(inputs=[x], output_names=[name])[0].numpy()
        assert np.array_equal(patched, want)
        assert not np.array_equal(as_is, want)
        steps = [np.floor(p * (np.float32(1) / s_next) + np.float32(0.5)) for p in (as_is, want)]
        assert np.abs(steps[0] - steps[1]).max() <= 1


def test_rmsnorm_as_espdl_passes_the_gradient_of_the_float_norm(tmp_path: Path) -> None:
    from esp_ppq.executor.op.torch.default import RMSNormalization_forward

    _, op = norm_graph(tmp_path)
    e_in = ptq_espdl.exponent_of(op.input_quant_config[0])
    q = np.random.default_rng(10).normal(0, 40, (1, 8, NORM_WIDTH)).round().clip(-128, 127)
    x = torch.from_numpy(q.astype(np.float32) * np.float32(2.0**e_in))
    grads, outs = [], []
    patched = {"simulate": RMSNormalization_forward, "platforms": frozenset({op.platform})}
    elsewhere = patched | {"platforms": frozenset()}
    for kwargs in ({}, patched, elsewhere):
        forward = esp_ppq_patches.rmsnorm_like_espdl if kwargs else RMSNormalization_forward
        xs, scale = x.clone().requires_grad_(), op.inputs[1].value.detach().clone().requires_grad_()
        outs.append(forward(op, [xs, scale], **kwargs))
        (outs[-1] * torch.linspace(-1, 1, NORM_WIDTH)).sum().backward()
        grads.append((xs.grad, scale.grad))
    assert all(torch.equal(a, b) and torch.equal(a, c) for a, b, c in zip(*grads, strict=True))
    assert torch.equal(outs[2], outs[0]) and not torch.equal(outs[1], outs[0])


def test_every_patch_is_undone_when_its_block_ends() -> None:
    from esp_ppq.api import espdl_interface, interface
    from esp_ppq.executor.base import OPERATION_FORWARD_TABLE
    from esp_ppq.parser import espdl_exporter
    from esp_ppq.parser.espdl.espdl_streaming import StreamingTable
    from esp_ppq.quantization.optim import QuantizeFusionPass

    pattern, add, fuse = espdl_exporter.InsertRequantNodePattern, StreamingTable.add, QuantizeFusionPass.optimize
    simplify, formatted = espdl_interface.simplify, interface.format_graph
    norms = {platform: table.get("RMSNormalization") for platform, table in OPERATION_FORWARD_TABLE.items()}
    gelus = {platform: table.get("Gelu") for platform, table in OPERATION_FORWARD_TABLE.items()}
    with esp_ppq_patches.applied(list(esp_ppq_patches.PATCHES)):
        assert espdl_exporter.InsertRequantNodePattern is not pattern and StreamingTable.add is not add
        assert QuantizeFusionPass.optimize is not fuse and espdl_interface.simplify is not simplify
        assert interface.format_graph is not formatted
        s3 = [p for p in OPERATION_FORWARD_TABLE if p.name in ("ESPDL_S3_INT8", "ESPDL_S3_INT16")]
        assert len(s3) == 2 and all(OPERATION_FORWARD_TABLE[p]["RMSNormalization"] is not norms[p] for p in s3)
    assert espdl_exporter.InsertRequantNodePattern is pattern and StreamingTable.add is add
    assert QuantizeFusionPass.optimize is fuse and espdl_interface.simplify is simplify
    assert interface.format_graph is formatted
    assert all(table.get("RMSNormalization") is norms[p] for p, table in OPERATION_FORWARD_TABLE.items())
    assert all(table.get("Gelu") is gelus[p] for p, table in OPERATION_FORWARD_TABLE.items())


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
    from esp_ppq.executor.torch import TorchExecutor

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


def test_an_unknown_patch_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown ESP-PPQ patches"), esp_ppq_patches.applied(["no_such_patch"]):
        pass
