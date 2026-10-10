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


def espdl_layernorm(q: np.ndarray, gamma: np.ndarray, beta: np.ndarray, s_in, s_out, eps) -> np.ndarray:
    """esp-dl's int8 LayerNormalization of q over its last axis as the S3 build runs it (-ffast-math -O3): the mean
    through a double reciprocal, squares summed by madd.s, 1.0f / n times the squared input step fused with eps,
    sqrt_newton as dl_math.hpp has it, then gamma times the inverse deviation times the centred value, plus beta."""
    f, n = np.float32, q.shape[-1]
    mean = (q.astype(np.float64).sum(-1, keepdims=True) * (1.0 / n)).astype(f)
    squares = np.zeros_like(mean)
    for j in range(n):
        squares = fused(q[..., j : j + 1].astype(f) - mean, q[..., j : j + 1].astype(f) - mean, squares)
    x = fused(np.full_like(squares, (f(1.0) / f(n)) * (f(s_in) * f(s_in))), squares, np.full_like(squares, eps))
    root, moving = x.copy(), np.ones(x.shape, dtype=bool)
    while moving.any():
        step = (root + x / root) * f(0.5)
        root, moving = np.where(moving, step, root), moving & (np.abs(step - root) > f(1e-5))
    result = gamma * ((f(1.0) / root) * f(s_in)) * (q.astype(f) - mean) + beta
    return np.clip(np.floor(result * (f(1.0) / f(s_out)) + f(0.5)), -128, 127)


def test_fma32_rounds_a_product_and_its_sum_once() -> None:
    """(1 + 2^-12)^2 - 1 keeps the product's 2^-24 that a rounded product drops; 1 + 2^-23 - 2^-24 + 2^-70 rounds up,
    though its float64 sum sits on the float32 half step between 1 and 1 + 2^-23 and would round to even."""
    f = np.float32
    cases = [
        ((f(1 + 2**-12), f(1 + 2**-12), f(-1)), f(2**-11 + 2**-24), f(f(1 + 2**-12) * f(1 + 2**-12)) + f(-1)),
        ((f(2**-12 * (1 + 2**-23)), f(-(2**-12) * (1 - 2**-23)), f(1 + 2**-23)), f(1 + 2**-23), f(1)),
    ]
    for (a, b, c), want, unfused in cases:
        args = [np.array([v]) for v in (a, b, c)]
        assert fused(*args)[0] == want != unfused
        assert esp_ppq_patches.fma32(*(torch.from_numpy(v) for v in args))[0].item() == want


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
    """esp-dl's int8 Softmax of q over its last axis as the S3 build runs forward_lut (-ffast-math): exp from a table
    of the input's grid, the row summed one value after another in float32, each value times the sum's reciprocal."""
    table = np.exp(np.arange(-128, 128, dtype=np.float64) * s_in).astype(np.float32)
    e = table[q.astype(np.int64) + 128]
    total = np.zeros((*e.shape[:-1], 1), np.float32)
    for i in range(e.shape[-1]):
        total = total + e[..., i : i + 1]
    return e * (np.float32(1) / total)


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
    prepare, reset = espdl_exporter.EspdlExporter.prepare_graph, espdl_exporter.reset_graph_layout
    norms = {platform: table.get("RMSNormalization") for platform, table in OPERATION_FORWARD_TABLE.items()}
    gelus = {platform: table.get("Gelu") for platform, table in OPERATION_FORWARD_TABLE.items()}
    with esp_ppq_patches.applied(list(esp_ppq_patches.PATCHES)):
        assert espdl_exporter.InsertRequantNodePattern is not pattern and StreamingTable.add is not add
        assert QuantizeFusionPass.optimize is not fuse and espdl_interface.simplify is not simplify
        assert interface.format_graph is not formatted
        assert espdl_exporter.EspdlExporter.prepare_graph is not prepare
        assert espdl_exporter.reset_graph_layout is not reset
        s3 = [p for p in OPERATION_FORWARD_TABLE if p.name in ("ESPDL_S3_INT8", "ESPDL_S3_INT16")]
        assert len(s3) == 2 and all(OPERATION_FORWARD_TABLE[p]["RMSNormalization"] is not norms[p] for p in s3)
    assert espdl_exporter.InsertRequantNodePattern is pattern and StreamingTable.add is add
    assert QuantizeFusionPass.optimize is fuse and espdl_interface.simplify is simplify
    assert interface.format_graph is formatted
    assert espdl_exporter.EspdlExporter.prepare_graph is prepare and espdl_exporter.reset_graph_layout is reset
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


class SoftmaxReadTwice(nn.Module):
    """Attention weights read by two products, as ReDimNet2's pool reads them."""

    def __init__(self) -> None:
        super().__init__()
        self.linear = nn.Linear(BANDS, BANDS)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = self.linear(x)
        alpha = torch.softmax(y, dim=-1)
        return torch.cat([alpha * y, alpha * (y * y)], dim=-1)


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


class CopiedStem(nn.Module):
    """A one-channel map copied into 12 channels, as ReDimNet2 b0's stem is rewritten, then a depthwise convolution."""

    def __init__(self) -> None:
        super().__init__()
        self.depthwise = nn.Conv2d(12, 12, 3, padding=1, groups=12)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.depthwise(torch.cat([x] * 12, dim=1))


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


class WeightedChannels(nn.Module):
    """A convolution, its channels weighted as ReDimNet2's stage sums weigh an earlier stage's, another convolution."""

    def __init__(self) -> None:
        super().__init__()
        self.front, self.back = nn.Conv1d(BANDS, BANDS, 3, padding=1), nn.Conv1d(BANDS, BANDS, 1)
        self.weigh = nn.Conv1d(BANDS, BANDS, 1, groups=BANDS, bias=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.back(self.weigh(self.front(x)))


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


def test_an_unknown_patch_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown ESP-PPQ patches"), esp_ppq_patches.applied(["no_such_patch"]):
        pass
