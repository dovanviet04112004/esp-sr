"""Fixes to ESP-PPQ 1.3.11's quantisation, simulation and esp-dl export, in force wherever the branch whose config names
them runs ESP-PPQ (KEHOACH 3.14); a branch that names none runs exactly as ESP-PPQ does.

Each fix states the bug, what board B showed without it and the test that pins it (tests/test_esp_ppq_patches.py);
when the ESP-PPQ pin moves, re-check each one upstream.
"""

from __future__ import annotations

import contextlib
import functools
import math
from collections.abc import Callable, Iterator, Sequence

# esp-dl runs convolutions channels last, so time, the first spatial axis of every conv streamed here, is axis 1.
CONV_TIME_AXIS = 1
SQRT_NEWTON_STOP = 1e-5  # dl_math.hpp EN, esp-dl 3.3.11


def requantise_graph_input_readers(op, graph) -> None:
    """Requantise each graph input op reads at another scale than the input's first reader, whose scale esp-dl gives
    the input."""
    import torch
    from esp_ppq.IR.quantize import QuantableOperation
    from esp_ppq.parser.espdl.espdl_graph_utils import insert_requantize_node
    from esp_ppq.parser.espdl.espdl_typedef import QUANT_OP_SET, ExporterPatternInfo
    from esp_ppq.parser.espdl.export_patterns import EspdlQuantHelper

    def exportable(config, var) -> bool:
        return EspdlQuantHelper.TQC_Exportable_Check(TQC=config, bounded_var=var)

    if not isinstance(op, QuantableOperation) or op.type in QUANT_OP_SET:
        return
    for config, var in list(zip(op.input_quant_config, op.inputs, strict=True)):
        if var.name not in graph.inputs or var.source_op is not None or not exportable(config, var):
            continue
        readers = [o for o in graph.topological_sort() if isinstance(o, QuantableOperation) and var in o.inputs]
        pairs = (pair for o in readers for pair in zip(o.input_quant_config, o.inputs, strict=True))
        upstream = next(c for c, v in pairs if v is var and exportable(c, v))
        if upstream.num_of_bits == config.num_of_bits and torch.equal(upstream.scale, config.scale):
            continue
        created = insert_requantize_node(graph=graph, var=var, upstream_config=upstream, config=config, op=op)
        info = ExporterPatternInfo()
        if perm := info.get_var_permute(var.name):
            info.add_var_permute(created.outputs[0].name, perm)


@contextlib.contextmanager
def requantise_graph_inputs() -> Iterator[None]:
    """InsertRequantNodePattern requantises only variables an op produces, so a graph input two readers take at two
    scales reaches esp-dl at the first reader's, and an Add or Sub aligned to its output adds integers of two scales.
    Board B, ctc_lay (the layer reads its input in a Conv, an Add and the bypass Sub): model->test() off by 8 steps.
    """
    from esp_ppq.parser import espdl_exporter

    base = espdl_exporter.InsertRequantNodePattern

    class Pattern(base):
        def export(self, op, graph, **kwargs):
            op = super().export(op, graph, **kwargs)
            requantise_graph_input_readers(op, graph)
            return op

    espdl_exporter.InsertRequantNodePattern = Pattern
    try:
        yield
    finally:
        espdl_exporter.InsertRequantNodePattern = base


@contextlib.contextmanager
def conv_caches_along_time() -> Iterator[None]:
    """A cache registered without a frame axis takes the graph input's, which is the channel axis of a convolution's
    tensor once the input reaches the convolutions through a layout-agnostic op; this gives such caches time.
    Board B, ctc_net (a Slice feeds the front's 2D convolutions): esp-dl asserts in Reshape while building.
    """
    from esp_ppq.parser.espdl.espdl_streaming import StreamingTable

    add = StreamingTable.add

    def along_time(self, var_name, op_name, window_size, frame_axis=None):
        return add(self, var_name, op_name, window_size, CONV_TIME_AXIS if frame_axis is None else frame_axis)

    StreamingTable.add = along_time
    try:
        yield
    finally:
        StreamingTable.add = add


@contextlib.contextmanager
def fuse_passive_ops_on_graph_inputs() -> Iterator[None]:
    """QuantizeFusionPass gives each passive op its input's scale, except one that reads a graph input, which ESP-PPQ
    then rescales at its output; esp-dl's passive modules copy integers, so the chip skips that rescale.
    Board B, command_ctc on percentile: the pitch Slice at 2^-4 under the features' 2^-3, the pitch halved on chip.
    """
    from esp_ppq.core import PASSIVE_OPERATIONS
    from esp_ppq.IR.quantize import QuantableOperation
    from esp_ppq.quantization.optim import QuantizeFusionPass

    optimize = QuantizeFusionPass.optimize

    def fused(self, graph, **kwargs):
        optimize(self, graph, **kwargs)
        for op in graph.operations.values():
            reads_input = isinstance(op, QuantableOperation) and op.inputs[0].source_op is None
            if self.fuse_passive_op and op.type in PASSIVE_OPERATIONS and reads_input:
                for config in op.output_quant_config:
                    config.dominated_by = op.input_quant_config[0]

    QuantizeFusionPass.optimize = fused
    try:
        yield
    finally:
        QuantizeFusionPass.optimize = optimize


def rmsnorm_like_espdl(op, values: list, ctx=None, *, simulate: Callable, platforms: frozenset, **kwargs):
    """op's output as esp-dl's S3 kernel computes it once op, on one of platforms, has its input and output quantised,
    each float32 step in the kernel's order; simulate's float output otherwise. The gradient is simulate's."""
    import torch
    from esp_ppq.core import QuantizationStates
    from esp_ppq.IR.quantize import QuantableOperation

    floating = simulate(op, values, ctx, **kwargs)
    if not isinstance(op, QuantableOperation) or op.platform not in platforms:
        return floating
    given, out = op.input_quant_config[0], op.output_quant_config[0]
    if not all(QuantizationStates.is_activated(c.dominated_by.state) for c in (given, out)):
        return floating
    with torch.no_grad():
        x, weight = values[0].to(floating.device), values[1].to(floating.device)
        s_in, s_out = given.scale.to(floating.device), out.scale.to(floating.device)
        axes = list(range(x.ndim - weight.ndim, x.ndim))
        q = x / s_in
        sum_sq = (q.double() ** 2).sum(axes, keepdim=True).float()
        # Tensor by tensor: torch divides a CUDA tensor by a Python number through its reciprocal.
        mean_sq = sum_sq * (s_in * s_in) / torch.full_like(sum_sq, math.prod(x.shape[a] for a in axes))
        eps = torch.full_like(mean_sq, op.attributes.get("epsilon", 1e-5))
        rms = torch.ones_like(mean_sq) / torch.sqrt(mean_sq + eps) * (s_in / s_out)
        y = torch.clamp(torch.floor(q * rms * weight + 0.5), out.quant_min, out.quant_max) * s_out
    return floating - floating.detach() + y


@contextlib.contextmanager
def rmsnorm_as_espdl() -> Iterator[None]:
    """ESP-PPQ simulates RMSNormalization as x / sqrt(mean(x^2) + eps) * scale rounded at its output; esp-dl's S3 kernel
    multiplies the integers by 1 / sqrtf of the mean square, already scaled between the grids, then by the float scale,
    and rounds half up. Board B, command_ctc qat: 2 of 198 Gate 3 windows scored a step off the simulation.
    """
    from esp_ppq.api.espdl_interface import get_target_platform
    from esp_ppq.executor.base import OPERATION_FORWARD_TABLE

    from srpipe.compress.quant.ptq_espdl import BITS, TARGET, WIDE_BITS

    platforms = frozenset(get_target_platform(TARGET, bits) for bits in (BITS, WIDE_BITS))
    # Every esp-dl platform shares one table, so the patched forward checks the platform itself.
    tables = list({id(t): t for t in (OPERATION_FORWARD_TABLE[p] for p in platforms)}.values())
    simulated = [table["RMSNormalization"] for table in tables]
    for table, simulate in zip(tables, simulated, strict=True):
        table["RMSNormalization"] = functools.partial(rmsnorm_like_espdl, simulate=simulate, platforms=platforms)
    try:
        yield
    finally:
        for table, simulate in zip(tables, simulated, strict=True):
            table["RMSNormalization"] = simulate


def sqrt_newton(x):
    """esp-dl's dl::math::sqrt_newton on a float32 tensor: Newton steps from x until one moves by at most 1e-5."""
    root, moving = x.clone(), x != 0
    root[~moving] = 0
    while moving.any():
        step = (root[moving] + x[moving] / root[moving]) * 0.5
        still = (step - root[moving]).abs() > SQRT_NEWTON_STOP
        root[moving] = step
        moving[moving.clone()] = still
    return root


def layernorm_like_espdl(op, values: list, ctx=None, *, simulate: Callable, platforms: frozenset, **kwargs):
    """op's output as esp-dl's int8 LayerNormalization computes it once op, on one of platforms, has its input and
    output quantised, each float32 step in dl_module_layer_normalization.hpp's order; simulate's output otherwise."""
    import torch
    from esp_ppq.core import QuantizationStates
    from esp_ppq.IR.quantize import QuantableOperation

    floating = simulate(op, values, ctx, **kwargs)
    if not isinstance(op, QuantableOperation) or op.platform not in platforms:
        return floating
    given, out = op.input_quant_config[0], op.output_quant_config[0]
    if not all(QuantizationStates.is_activated(c.dominated_by.state) for c in (given, out)):
        return floating
    with torch.no_grad():
        x, gamma = values[0].to(floating.device), values[1].to(floating.device)
        beta = values[2].to(floating.device) if len(values) > 2 else torch.zeros_like(gamma)
        s_in, s_out = given.scale.to(floating.device).float(), out.scale.to(floating.device).float()
        q = torch.round(x / s_in)
        n = q.shape[-1]
        mean = (q.double().sum(-1, keepdim=True) / n).float()
        variance = torch.zeros_like(mean)
        for j in range(n):
            variance = variance + (q[..., j : j + 1] - mean) ** 2
        variance = variance * (s_in * s_in) / n
        eps = torch.full_like(variance, op.attributes.get("epsilon", 1e-5))
        inv_std = torch.ones_like(variance) / sqrt_newton(variance + eps)
        result = (q * s_in - mean * s_in) * inv_std * gamma + beta
        y = torch.clamp(torch.floor(result * (torch.ones_like(s_out) / s_out) + 0.5), out.quant_min, out.quant_max)
    return floating - floating.detach() + y * s_out


@contextlib.contextmanager
def layernorm_as_espdl() -> Iterator[None]:
    """ESP-PPQ simulates LayerNormalization with torch's layer_norm rounded at its output; esp-dl's int8 kernel sums the
    integers, accumulates the variance in float32 one channel after another and takes 1 / sqrt_newton of it, so an
    output on a half step can round the other way. ReDimNet2 b0 (KEHOACH 3.17) on board B: 2.5 in stage0's first norm
    became 3 on the chip and 2 in the simulation, and 160 of 192 embedding values left it.
    """
    from esp_ppq.api.espdl_interface import get_target_platform
    from esp_ppq.executor.base import OPERATION_FORWARD_TABLE

    from srpipe.compress.quant.ptq_espdl import BITS, TARGET, WIDE_BITS

    platforms = frozenset(get_target_platform(TARGET, bits) for bits in (BITS, WIDE_BITS))
    # Every esp-dl platform shares one table, so the patched forward checks the platform itself.
    tables = list({id(t): t for t in (OPERATION_FORWARD_TABLE[p] for p in platforms)}.values())
    simulated = [table["LayerNormalization"] for table in tables]
    for table, simulate in zip(tables, simulated, strict=True):
        table["LayerNormalization"] = functools.partial(layernorm_like_espdl, simulate=simulate, platforms=platforms)
    try:
        yield
    finally:
        for table, simulate in zip(tables, simulated, strict=True):
            table["LayerNormalization"] = simulate


@contextlib.contextmanager
def simplify_without_bn_fusion() -> Iterator[None]:
    """espdl_quantize_onnx simplifies the ONNX with onnxsim's defaults, whose fuse_bn pass leaves a tensor whose stored
    shape differs in rank from the one inferred after it; this runs onnxsim without that pass, ESP-PPQ fusing the
    norms itself. ReDimNet2 b0 (KEHOACH 3.17): "[ShapeInferenceError] Inferred shape and existing shape differ in rank:
    (3) vs (2)" before quantisation starts, on the PC.
    """
    from esp_ppq.api import espdl_interface

    simplify = espdl_interface.simplify
    espdl_interface.simplify = functools.partial(simplify, skip_fuse_bn=True)
    try:
        yield
    finally:
        espdl_interface.simplify = simplify


@contextlib.contextmanager
def fuse_erf_gelu() -> Iterator[None]:
    """ESP-PPQ formats an ONNX graph without fusing GELU, so torch's Div, Erf, Add, Mul, Mul stay, and esp-dl has no
    Erf; this fuses them into one Gelu by ESP-PPQ's own GraphMerger.fuse_gelu, which esp-dl runs as an int8 table
    of the exact function. ReDimNet2 b0 (KEHOACH 3.17) on board B: "Do not support Erf, please implement and register
    it first", the model never loads.
    """
    from esp_ppq.api import interface
    from esp_ppq.IR.morph import GraphMerger

    format_graph = interface.format_graph

    def formatted(graph):
        graph = format_graph(graph)
        GraphMerger(graph).fuse_gelu()
        return graph

    interface.format_graph = formatted
    try:
        yield
    finally:
        interface.format_graph = format_graph


PATCHES = {
    "requantise_graph_inputs": requantise_graph_inputs,
    "conv_caches_along_time": conv_caches_along_time,
    "fuse_passive_ops_on_graph_inputs": fuse_passive_ops_on_graph_inputs,
    "rmsnorm_as_espdl": rmsnorm_as_espdl,
    "simplify_without_bn_fusion": simplify_without_bn_fusion,
    "fuse_erf_gelu": fuse_erf_gelu,
    "layernorm_as_espdl": layernorm_as_espdl,
}


@contextlib.contextmanager
def applied(names: Sequence[str]) -> Iterator[None]:
    """The named patches, in force for the length of the block."""
    unknown = sorted(set(names) - set(PATCHES))
    if unknown:
        raise ValueError(f"unknown ESP-PPQ patches {unknown}; known: {sorted(PATCHES)}")
    with contextlib.ExitStack() as stack:
        for name in names:
            stack.enter_context(PATCHES[name]())
        yield
