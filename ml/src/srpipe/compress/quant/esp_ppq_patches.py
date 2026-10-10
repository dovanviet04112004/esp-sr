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
CHANNEL_STEP = 16  # dl_base_conv2d.cpp: S3 vector path, 16 channels a step
DATA_CACHE_BYTES = 0x10000  # CONFIG_ESP32S3_DATA_CACHE_SIZE of the firmware apps
ELEMENTWISE_OPS = frozenset(
    {
        "Add",
        "Sub",
        "Mul",
        "Div",
        "Sqrt",
        "Relu",
        "Tanh",
        "Sigmoid",
        "QuantizeLinear",
        "DequantizeLinear",
        "RequantizeLinear",
    }
)
REDUCE_OPS = frozenset({"ReduceMean", "ReduceSum", "ReduceMax", "ReduceMin"})


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


def fma32(a, b, c):
    """a * b + c on float32 tensors rounded once to float32, as the S3's madd.s gives it: the exact product summed in
    float64 and rounded to odd there, which the one rounding to float32 then leaves as the fused result."""
    import torch

    product, addend = a.double() * b.double(), c.double()
    total = product + addend
    back = total - product
    error = (product - (total - back)) + (addend - back)
    toward = torch.where(error > 0, torch.full_like(total, math.inf), torch.full_like(total, -math.inf))
    even = (total.view(torch.int64) & 1) == 0
    return torch.where((error != 0) & even, torch.nextafter(total, toward), total).float()


def layernorm_like_espdl(op, values: list, ctx=None, *, simulate: Callable, platforms: frozenset, **kwargs):
    """op's output as esp-dl's LayerNormalization computes it once op, on one of platforms, has its input and output
    quantised: each float32 step as GCC builds forward_template for the S3 under esp-dl's -ffast-math -O3, which
    reorders the source's steps and fuses its multiply-adds into madd.s; simulate's output otherwise."""
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
        s_in, s_out = given.scale.to(floating.device).float(), out.scale.to(floating.device).float()
        q = torch.round(x / s_in)
        n = q.shape[-1]
        mean = (q.double().sum(-1, keepdim=True) * (1.0 / n)).float()
        squares = torch.zeros_like(mean)
        for j in range(n):
            squares = fma32(q[..., j : j + 1] - mean, q[..., j : j + 1] - mean, squares)
        per_value = torch.ones_like(s_in) / torch.full_like(s_in, n) * (s_in * s_in)
        eps = torch.full_like(squares, op.attributes.get("epsilon", 1e-5))
        inv_std = torch.ones_like(squares) / sqrt_newton(fma32(per_value, squares, eps))
        result = gamma * (inv_std * s_in) * (q - mean)
        if len(values) > 2:
            result = result + values[2].to(floating.device)
        y = torch.clamp(torch.floor(result * (torch.ones_like(s_out) / s_out) + 0.5), out.quant_min, out.quant_max)
    return floating - floating.detach() + y * s_out


@contextlib.contextmanager
def layernorm_as_espdl() -> Iterator[None]:
    """ESP-PPQ simulates LayerNormalization with torch's layer_norm rounded at its output; esp-dl's kernel, built with
    -ffast-math, takes the mean through a reciprocal, sums the squares with fused multiply-adds and scales by gamma
    before the centred value, so an output on a half step can round the other way. ReDimNet2 b0 (KEHOACH 3.17) on board
    B: the stem's norm left the simulation on speech-calibrated grids, as stage0's first did without the patch.
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


def softmax_like_espdl(op, values: list, ctx=None, *, simulate: Callable, platforms: frozenset, **kwargs):
    """op's output as esp-dl's Softmax computes it from an int8 input on one of platforms: each step's exp from a
    256-entry float32 table, the row summed in float32 one value after another, each multiplied by the float32
    reciprocal of that sum, as -ffast-math builds the source's division; simulate's output otherwise."""
    import torch
    from esp_ppq.core import QuantizationStates
    from esp_ppq.IR.quantize import QuantableOperation

    floating = simulate(op, values, ctx, **kwargs)
    if not isinstance(op, QuantableOperation) or op.platform not in platforms:
        return floating
    given = op.input_quant_config[0]
    if not QuantizationStates.is_activated(given.dominated_by.state) or given.num_of_bits != 8:
        return floating
    axis = op.attributes.get("axis", -1)
    with torch.no_grad():
        x, s = values[0].to(floating.device), given.scale.to(floating.device)
        q = torch.clamp(torch.round(x / s), -128, 127).long()
        steps = torch.arange(-128, 128, dtype=torch.float64, device=floating.device)
        e = torch.exp(steps * s.double()).float()[q + 128].movedim(axis, -1)
        total = torch.zeros_like(e[..., :1])
        for i in range(e.shape[-1]):
            total = total + e[..., i : i + 1]
        p = (e * (torch.ones_like(total) / total)).movedim(-1, axis)
    return floating - floating.detach() + p


@contextlib.contextmanager
def softmax_as_espdl() -> Iterator[None]:
    """ESP-PPQ simulates Softmax as torch's, the maximum taken out before exp; esp-dl's int8 kernel looks exp up in a
    table built at the input's exponent and sums the row in order, so a probability near a half step of the next op's
    grid can round the other way. ReDimNet2 b0 (KEHOACH 3.17) on board B: after layernorm_as_espdl, stage1's attention
    was the first op off the simulation, 6 of 87 616 probabilities a step apart.
    """
    from esp_ppq.api.espdl_interface import get_target_platform
    from esp_ppq.executor.base import OPERATION_FORWARD_TABLE

    from srpipe.compress.quant.ptq_espdl import BITS, TARGET, WIDE_BITS

    platforms = frozenset(get_target_platform(TARGET, bits) for bits in (BITS, WIDE_BITS))
    # Every esp-dl platform shares one table, so the patched forward checks the platform itself.
    tables = list({id(t): t for t in (OPERATION_FORWARD_TABLE[p] for p in platforms)}.values())
    simulated = [table["Softmax"] for table in tables]
    for table, simulate in zip(tables, simulated, strict=True):
        table["Softmax"] = functools.partial(softmax_like_espdl, simulate=simulate, platforms=platforms)
    try:
        yield
    finally:
        for table, simulate in zip(tables, simulated, strict=True):
            table["Softmax"] = simulate


def scalar_and_other(op):
    """op's one single-valued parameter and its other input, or None when op has not exactly that."""
    params = [v for v in op.inputs if v.is_parameter and v.value is not None and v.value.numel() == 1]
    others = [v for v in op.inputs if not v.is_parameter]
    if len(params) != 1 or len(others) != 1:
        return None
    return float(params[0].value.flatten()[0]), others[0]


def tanh_gelu_chain(tanh):
    """The input, output and ops of torch's tanh GELU around tanh, 0.5 x (1 + tanh(sqrt(2/pi) (x + 0.044715 x^3))) as
    torch exports it, or None when tanh is not in one."""

    def made_by(var, kind, value=None):
        op = var.source_op
        if op is None or op.type != kind or len(var.dest_ops) != 1:
            return None
        split = scalar_and_other(op) if value is not None else None
        if value is not None and (split is None or not math.isclose(split[0], value, rel_tol=1e-6)):
            return None
        return op, (split[1] if split else None)

    def used_by(var, kind, value=None):
        if len(var.dest_ops) != 1 or var.dest_ops[0].type != kind:
            return None
        op = var.dest_ops[0]
        if value is not None:
            split = scalar_and_other(op)
            if split is None or not math.isclose(split[0], value, rel_tol=1e-6):
                return None
        return op

    found = made_by(tanh.inputs[0], "Mul", math.sqrt(2 / math.pi))
    inner = found and made_by(found[1], "Add")
    if not inner:
        return None
    for term in inner[0].inputs:
        cube = made_by(term, "Mul", 0.044715)
        power = cube and made_by(cube[1], "Pow")
        if not power or scalar_and_other(power[0]) is None or scalar_and_other(power[0])[0] != 3.0:
            continue
        x = scalar_and_other(power[0])[1]
        if x not in inner[0].inputs:
            continue
        plus_one = used_by(tanh.outputs[0], "Add", 1.0)
        product = plus_one and used_by(plus_one.outputs[0], "Mul")
        half = product and next((v for v in product.inputs if v is not plus_one.outputs[0]), None)
        halved = half is not None and made_by(half, "Mul", 0.5)
        if not halved or halved[1] is not x:
            return None
        return x, product.outputs[0], [halved[0], power[0], cube[0], inner[0], found[0], tanh, plus_one, product]
    return None


@contextlib.contextmanager
def fuse_tanh_gelu() -> Iterator[None]:
    """ESP-PPQ formats an ONNX graph without fusing torch's tanh GELU, so its Pow runs in float with a quantise and a
    dequantise around it and Mul, Add and Tanh each round once; this fuses each chain into one Gelu with approximate
    tanh, simulated and tabled for esp-dl as that function. ReDimNet2 b0 (KEHOACH 3.17): six such feed-forward
    activations in the attention blocks.
    """
    from esp_ppq.api import interface
    from esp_ppq.executor.base import OPERATION_FORWARD_TABLE
    from torch.nn import functional

    format_graph = interface.format_graph

    def formatted(graph):
        graph = format_graph(graph)
        for tanh in [op for op in graph.operations.values() if op.type == "Tanh"]:
            chain = tanh_gelu_chain(tanh) if tanh.name in graph.operations else None
            if chain is None:
                continue
            x, out, ops = chain
            inner = [v for op in ops for v in op.outputs if v is not out]
            for op in ops:
                graph.remove_operation(op)
            for var in inner:
                graph.remove_variable(var)
            graph.create_operation(op_type="Gelu", attributes={"approximate": "tanh"}, inputs=[x], outputs=[out])
        return graph

    def gelu(op, values, ctx=None, **kwargs):
        return functional.gelu(values[0], approximate=op.attributes.get("approximate", "none"))

    tables = list({id(t): t for t in OPERATION_FORWARD_TABLE.values()}.values())
    simulated = [table.get("Gelu") for table in tables]
    interface.format_graph = formatted
    for table in tables:
        table["Gelu"] = gelu
    try:
        yield
    finally:
        interface.format_graph = format_graph
        for table, simulate in zip(tables, simulated, strict=True):
            table["Gelu"] = simulate


def chain_as_one(shape: list[int], first: list[int], target: list[int], second: list[int]):
    """(q, out) such that transposing an array of shape by q and reshaping it to out lays its values as transposing it
    by first, reshaping to target and transposing by second does; q the identity when the chain moves nothing, None
    when no single transpose does it."""
    import itertools

    import numpy as np

    index = np.arange(math.prod(shape)).reshape(shape)
    out = index.transpose(first).reshape(target).transpose(second)
    for q in itertools.permutations(range(len(shape))):
        if np.array_equal(index.transpose(q).reshape(-1), out.reshape(-1)):
            return list(q), list(out.shape)
    return None


def share_transposes(graph) -> int:
    """Merge the Transposes reading one variable by one perm into the first; how many went. Each inserted Transpose
    carries its reader's grid, but moves values without rescaling them: the export requantises a reader after the
    shared one where its grid differs."""
    gone = 0
    for var in list(graph.variables.values()):
        kept = {}
        for op in list(var.dest_ops):
            if op.type != "Transpose" or op.name not in graph.operations:
                continue
            first = kept.setdefault(tuple(op.attributes["perm"]), op)
            twin = op.outputs[0]
            if first is op or twin.name in graph.outputs:
                continue
            for reader in list(twin.dest_ops):
                reader.inputs[:] = [first.outputs[0] if v is twin else v for v in reader.inputs]
                first.outputs[0].dest_ops.append(reader)
            twin.dest_ops.clear()
            graph.remove_operation(op)
            graph.remove_variable(twin)
            gone += 1
    return gone


def is_turn(op, perm: list[int]) -> bool:
    return op.type == "Transpose" and list(op.attributes["perm"]) == list(perm)


def layout_region(source, perm: list[int], graph, info):
    """(ops, ends) when every op reading source but its Transposes by perm, and in turn every op reading theirs,
    computes per element, per kept axis or per joined axis until Transposes by perm, all in source's own layout:
    those ops and those Transposes; None when one does not."""
    from esp_ppq.parser.espdl.espdl_graph_utils import get_default_perm

    ops, ends, todo = [], [], [op for op in source.dest_ops if not is_turn(op, perm)]
    while todo:
        op = todo.pop()
        if op in ops or op in ends:
            continue
        if is_turn(op, perm):
            ends.append(op)
            continue
        keeps = op.type in REDUCE_OPS and op.attributes.get("keepdims", 1) == 1
        if op.type not in ELEMENTWISE_OPS and not keeps and op.type != "Concat":
            return None
        for i, v in enumerate(op.inputs):
            axes = keeps and i == 1
            if v.is_parameter and not axes and (v.value is None or v.value.numel() > 1):
                return None
        for out in op.outputs:
            if out.name in graph.outputs or info.get_var_permute(out.name) not in (None, [], get_default_perm(out)):
                return None
            todo.extend(out.dest_ops)
        ops.append(op)
    made = {id(source)} | {id(out) for op in ops for out in op.outputs}
    if any(not v.is_parameter and id(v) not in made for op in ops for v in op.inputs):
        return None
    return ops, ends


def sink_layouts(graph) -> int:
    """Each variable a Transpose reads beside ops that layout_region takes: those ops moved after the Transpose, their
    axes moved with it and the Transposes ending the region dropped, so the variable feeds the Transpose alone; how
    many."""
    import torch
    from esp_ppq.parser.espdl.espdl_graph_utils import get_default_perm
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo

    info, done = ExporterPatternInfo(), 0
    for first in [op for op in graph.topological_sort() if op.type == "Transpose"]:
        if first.name not in graph.operations:
            continue
        source, perm = first.inputs[0], list(first.attributes["perm"])
        if len(source.dest_ops) < 2:
            continue
        if info.get_var_permute(source.name) not in (None, [], get_default_perm(source)):
            continue
        found = layout_region(source, perm, graph, info)
        if found is None:
            continue
        (ops, ends), laid, rank = found, first.outputs[0], len(perm)
        for op in ops:
            for _ in [v for v in op.inputs if v is source]:
                source.dest_ops.remove(op)
                laid.dest_ops.append(op)
            op.inputs[:] = [laid if v is source else v for v in op.inputs]
            for out in op.outputs:
                out.shape = [out.shape[i] for i in perm]
                info.add_var_permute(out.name, get_default_perm(out))
            if op.type in REDUCE_OPS:
                axes = op.inputs[1].value
                op.inputs[1].value = torch.tensor([perm.index(int(a) % rank) for a in axes.flatten()], dtype=axes.dtype)
            elif op.type == "Concat":
                op.attributes["axis"] = perm.index(op.attributes["axis"] % rank)
        for end in ends:
            kept, dropped = end.inputs[0], end.outputs[0]
            for reader in list(dropped.dest_ops):
                reader.inputs[:] = [kept if v is dropped else v for v in reader.inputs]
                kept.dest_ops.append(reader)
            dropped.dest_ops.clear()
            graph.remove_operation(end)
            graph.remove_variable(dropped)
        done += 1
    return done


def fold_transpose_chains(graph) -> int:
    """Fold each Transpose, Reshape, Transpose whose middle values nothing else reads into one Transpose and the
    Reshape, or the Reshape alone when the chain moves nothing; how many folded."""
    import torch
    from esp_ppq.parser.espdl.espdl_graph_utils import fuse_downstream_operation, get_default_perm
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo

    folded = 0
    for first in [op for op in graph.topological_sort() if op.type == "Transpose"]:
        if first.name not in graph.operations:
            continue
        middle = first.outputs[0]
        if len(middle.dest_ops) != 1 or middle.dest_ops[0].type != "Reshape" or middle.name in graph.outputs:
            continue
        reshape = middle.dest_ops[0]
        flat = reshape.outputs[0]
        if len(flat.dest_ops) != 1 or flat.dest_ops[0].type != "Transpose" or flat.name in graph.outputs:
            continue
        second, laid = flat.dest_ops[0], ExporterPatternInfo().get_var_permute(first.inputs[0].name)
        shape = [first.inputs[0].shape[i] for i in laid] if laid else list(first.inputs[0].shape)
        target = [int(v) for v in reshape.inputs[1].value.flatten().tolist()]
        moved = [shape[i] for i in first.attributes["perm"]]
        target = [moved[i] if d == 0 else d for i, d in enumerate(target)]
        if -1 in target:
            target[target.index(-1)] = math.prod(moved) // -math.prod(target)
        found = chain_as_one(shape, first.attributes["perm"], target, second.attributes["perm"])
        if found is None or found[1] != list(second.outputs[0].shape):
            continue
        q, out = found
        reshape.inputs[1].value = torch.tensor(out, dtype=torch.int64)
        fuse_downstream_operation(graph, second, keep_coherence=True)
        if q == list(range(len(q))):
            graph.remove_operation(first, keep_coherence=True)
        else:
            first.attributes["perm"] = q
            middle.shape = [shape[i] for i in q]
            ExporterPatternInfo().add_var_permute(middle.name, get_default_perm(middle))
        folded += 1
    return folded


@contextlib.contextmanager
def lean_transposes() -> Iterator[None]:
    """ESP-PPQ's export puts a Transpose before each reader that needs another layout and merges only adjacent ones: a
    variable read by seven ops got seven equal Transposes, every Transpose, Reshape, Transpose chain kept both. After
    its layout pass this merges equal Transposes of one variable (share_transposes), moves elementwise ops of one
    variable after its Transpose (sink_layouts) and folds each chain into the one Transpose, or none, moving the
    same values. ReDimNet2 b0 (KEHOACH 3.17) on board B: 50 inserted Transposes took 0.55 s of 2.5 s a window.
    """
    from esp_ppq.parser import espdl_exporter
    from esp_ppq.parser.espdl.layout_patterns import FuseTransposePattern

    reset = espdl_exporter.reset_graph_layout

    def leaner(graph):
        reset(graph)
        share_transposes(graph)
        sink_layouts(graph)
        fold_transpose_chains(graph)
        pattern = FuseTransposePattern()
        for op in graph.topological_sort():
            pattern.export(op, graph)

    espdl_exporter.reset_graph_layout = leaner
    try:
        yield
    finally:
        espdl_exporter.reset_graph_layout = reset


def laid_shape(var, info) -> list[int]:
    """var's shape as the export lays it in the chip's memory."""
    perm = info.get_var_permute(var.name)
    return [var.shape[i] for i in perm] if perm else list(var.shape)


def column_merge(op, info) -> tuple[int, int] | None:
    """(s, k) for op, a convolution whose channels esp-dl's S3 kernels cannot align and whose kernel spans s columns
    at a stride of s and one of all else: s columns laid as channels make it pointwise, and k of those laid together
    align it, its block-diagonal weights within the data cache; None when op is no such convolution or no k does."""
    from esp_ppq.IR.quantize import QuantableOperation

    if op.type != "Conv" or not isinstance(op, QuantableOperation) or len(op.inputs[0].shape or []) < 3:
        return None
    (x, w), y, group = op.inputs[:2], op.outputs[0], op.attributes.get("group", 1)
    (px, py), kernel = (laid_shape(x, info), laid_shape(y, info)), op.attributes["kernel_shape"]
    s, rest = kernel[-1], [*kernel[:-1], *op.attributes.get("dilations", [])]
    (c_in, c_out), strides = (px[-1], py[-1]), op.attributes.get("strides", [1] * len(kernel))
    if strides != [1] * (len(kernel) - 1) + [s] or any(v != 1 for v in rest) or any(op.attributes.get("pads", [])):
        return None
    if w.value.shape[0] != c_out or w.value.shape[1] * group != c_in or px[-2] != s * py[-2]:
        return None
    if (group != 1 and (group != c_in or c_in != c_out or s != 1)) or not (c_in % CHANNEL_STEP or c_out % CHANNEL_STEP):
        return None
    for k in (1, 2, 4, 8, CHANNEL_STEP):
        weights = k * c_out * (1 if group != 1 else k * s * c_in)
        if py[-2] % k == 0 and not (k * s * c_in % CHANNEL_STEP or k * c_out % CHANNEL_STEP):
            return (s, k) if weights <= DATA_CACHE_BYTES else None
    return None


def merge_columns(graph, op, s: int, k: int, info) -> None:
    """op, as column_merge gives s and k for it, as a pointwise convolution over its map with each s times k
    neighbouring input columns and k output ones laid as channels: block-diagonal weights between two Reshapes, which
    move nothing in channels-last memory and share their input's buffer on the chip."""
    import torch
    from esp_ppq.core import DataType, OperationQuantizationConfig, QuantizationStates
    from esp_ppq.IR.quantize import QuantableOperation
    from esp_ppq.parser.espdl.espdl_graph_utils import get_default_perm, insert_reshape_node

    x, y, group = op.inputs[0], op.outputs[0], op.attributes.get("group", 1)
    px, py = laid_shape(x, info), laid_shape(y, info)
    w = op.inputs[1].value
    if group == 1:
        w = w.movedim(-1, 1).reshape(w.shape[0], -1, *[1] * (w.dim() - 2))
        value = torch.zeros(k * w.shape[0], k * w.shape[1], *w.shape[2:], dtype=w.dtype)
        for p in range(k):
            value[p * w.shape[0] : (p + 1) * w.shape[0], p * w.shape[1] : (p + 1) * w.shape[1]] = w
    else:
        value = w.repeat(k, *[1] * (w.dim() - 1))
        op.attributes["group"] = k * group
    op.attributes["kernel_shape"] = [1] * len(op.attributes["kernel_shape"])
    op.attributes["strides"] = [1] * len(op.attributes["kernel_shape"])
    values = [value] + [p.value.repeat(k) for p in op.inputs[2:]]
    for i, v in enumerate(values, start=1):
        config = op.input_quant_config[i].copy()
        if config.scale is not None and config.scale.dim() > 0:
            config.scale, config.offset = config.scale.repeat(k), config.offset.repeat(k)
        old = op.inputs[i]
        param = graph.create_variable(value=v, is_parameter=True)
        param.dtype, param.dest_ops[:] = old.dtype, [op]
        old.dest_ops.clear()
        graph.remove_variable(old)
        op.inputs[i], op.config.input_quantization_config[i] = param, config

    gathered = insert_reshape_node(graph, x, op, [*px[:-2], px[-2] // (s * k), s * k * px[-1]]).outputs[0]
    info.add_var_permute(gathered.name, get_default_perm(gathered))

    out_config = op.output_quant_config[0]
    shape_config = out_config.copy()
    shape_config.state = QuantizationStates.FP32
    spread = graph.create_operation(op_type="Reshape", attributes={"allowzero": 0})
    spread = QuantableOperation(
        spread, OperationQuantizationConfig([out_config, shape_config], [out_config]), op.platform
    )
    graph.operations[spread.name] = spread
    graph.insert_op_after(A=spread, B=op)
    op.outputs[0].shape, op.outputs[0].dtype = [*py[:-2], py[-2] // k, k * py[-1]], y.dtype
    info.add_var_permute(op.outputs[0].name, get_default_perm(op.outputs[0]))
    target = graph.create_variable(value=torch.tensor(py, dtype=torch.int64), is_parameter=True)
    target.dtype, target.dest_ops[:] = DataType.INT64, [spread]
    spread.inputs.append(target)


def align_pointwise(graph) -> int:
    """Each convolution column_merge finds s and k for, merged by merge_columns; how many."""
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo

    info, done = ExporterPatternInfo(), 0
    for op in list(graph.topological_sort()):
        found = column_merge(op, info)
        if found is not None:
            merge_columns(graph, op, *found, info)
            done += 1
    return done


@contextlib.contextmanager
def aligned_pointwise() -> Iterator[None]:
    """esp-dl's S3 convolution kernels take their vector path only when both channel counts are multiples of 16, and
    run 12 or 24 channels about ten times slower a multiply-add. After the export's layout pass this lays each such
    pointwise convolution over neighbouring columns merged into channels (merge_columns): the same integers, from
    twice or four times the multiply-adds at the vector rate. ReDimNet2 b0 (KEHOACH 3.17) on board B: 1x1 convolutions
    of 24 channels ran at 1.4 cycles a multiply-add against 0.15 for aligned ones, 0.32 s of 1.59 s a window.
    """
    from esp_ppq.parser import espdl_exporter

    reset = espdl_exporter.reset_graph_layout

    def aligned(graph):
        reset(graph)
        align_pointwise(graph)

    espdl_exporter.reset_graph_layout = aligned
    try:
        yield
    finally:
        espdl_exporter.reset_graph_layout = reset


def output_sizes(outputs: int, weight_bytes: int, weight_bytes_max: int) -> list[int]:
    """Slices of outputs, each a multiple of CHANNEL_STEP and holding at most weight_bytes_max of the convolution's
    weight_bytes, as even as that allows; one slice when it holds them all."""
    per_output = weight_bytes // outputs
    step = max(weight_bytes_max // per_output // CHANNEL_STEP, 1) * CHANNEL_STEP
    if outputs % CHANNEL_STEP or outputs <= step:
        return [outputs]
    size = -(-outputs // -(-outputs // step) // CHANNEL_STEP) * CHANNEL_STEP
    return [size] * (outputs // size) + ([outputs % size] if outputs % size else [])


def slice_outputs(graph, op, sizes: list[int], info) -> None:
    """op, an ungrouped pointwise convolution, as one convolution per slice of its outputs, each on its own slice of
    the weights and bias and on op's grids, concatenated along the channels into op's output."""
    from esp_ppq.core import OperationQuantizationConfig
    from esp_ppq.IR.quantize import QuantableOperation
    from esp_ppq.parser.espdl.espdl_graph_utils import get_default_perm

    (x, *params), y, platform = op.inputs, op.outputs[0], op.platform
    configs, out_config, laid = op.input_quant_config, op.output_quant_config[0], laid_shape(y, info)
    values = [p.value for p in params]
    attributes = dict(op.attributes)
    graph.remove_operation(op)
    parts, start = [], 0
    for size in sizes:
        sliced = []
        for value, config in zip(values, configs[1:], strict=True):
            param = graph.create_variable(value=value[start : start + size].clone(), is_parameter=True)
            config = config.copy()
            if config.scale is not None and config.scale.dim() > 0:
                config.scale, config.offset = config.scale[start : start + size], config.offset[start : start + size]
            sliced.append((param, config))
        part = graph.create_variable()
        part.shape, part.dtype = [*laid[:-1], size], y.dtype
        info.add_var_permute(part.name, get_default_perm(part))
        conv = graph.create_operation(op_type="Conv", attributes=dict(attributes))
        config = OperationQuantizationConfig([configs[0], *(c for _, c in sliced)], [out_config])
        linked(graph, QuantableOperation(conv, config, platform), [x, *(p for p, _ in sliced)], [part])
        parts.append(part)
        start += size
    concat = graph.create_operation(op_type="Concat", attributes={"axis": len(laid) - 1})
    config = OperationQuantizationConfig([out_config] * len(parts), [out_config])
    linked(graph, QuantableOperation(concat, config, platform), parts, [y])


def slice_wide_convolutions(graph) -> int:
    """Each ungrouped pointwise convolution over more than one position whose int8 weights outgrow the data cache, as
    slice_outputs lays it by output_sizes into slices of at most half of it; how many."""
    from esp_ppq.IR.quantize import QuantableOperation
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo

    info, done = ExporterPatternInfo(), 0
    for op in list(graph.topological_sort()):
        if op.type != "Conv" or not isinstance(op, QuantableOperation) or op.attributes.get("group", 1) != 1:
            continue
        w, laid = op.inputs[1].value, laid_shape(op.outputs[0], info)
        if any(k != 1 for k in op.attributes["kernel_shape"]) or math.prod(laid[:-1]) < 2:
            continue
        if w.numel() > DATA_CACHE_BYTES:
            slice_outputs(graph, op, output_sizes(laid[-1], w.numel(), DATA_CACHE_BYTES // 2), info)
            done += 1
    return done


@contextlib.contextmanager
def cache_sized_convolutions() -> Iterator[None]:
    """esp-dl's pointwise convolution kernel reads all of its weights again at every output position, so weights
    larger than the S3's data cache come from PSRAM at each one. After the export's layout pass this runs each such
    convolution as slices of its outputs, each slice's weights within half the data cache, concatenated: the same
    integers. ReDimNet2 b0 (KEHOACH 3.17) on board B: its pool's two per-frame projections, 72 KB of weights each,
    took 94 and 77 ms of a 1.79 s window, two cycles a multiply-add against 0.15 for cached weights.
    """
    from esp_ppq.parser import espdl_exporter

    reset = espdl_exporter.reset_graph_layout

    def sliced(graph):
        reset(graph)
        slice_wide_convolutions(graph)

    espdl_exporter.reset_graph_layout = sliced
    try:
        yield
    finally:
        espdl_exporter.reset_graph_layout = reset


def scales_channels(op, info) -> bool:
    """op is a depthwise pointwise convolution on a channels-last map without bias or activation: one product per
    value, shifted to its output grid, as a Mul by the weights broadcast along the channels computes it."""
    from esp_ppq.IR.quantize import QuantableOperation

    if op.type != "Conv" or not isinstance(op, QuantableOperation) or len(op.inputs) != 2:
        return False
    spans = op.attributes["kernel_shape"] + op.attributes.get("strides", []) + op.attributes.get("dilations", [])
    if any(v != 1 for v in spans) or any(op.attributes.get("pads", [])):
        return False
    laid = laid_shape(op.inputs[0], info)
    held = op.input_quant_config[1].scale
    return (
        op.attributes.get("activation", "Linear") == "Linear"
        and op.attributes.get("group", 1) == laid[-1] > 1
        and laid == laid_shape(op.outputs[0], info)
        and held is not None
        and held.numel() == 1
    )


def scales_as_muls(graph) -> int:
    """Each convolution scales_channels finds as a Mul of its input by its weights laid along the last axis, on the
    convolution's grids; how many."""
    from esp_ppq.core import OperationQuantizationConfig
    from esp_ppq.IR.quantize import QuantableOperation
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo

    info, done = ExporterPatternInfo(), 0
    for op in list(graph.topological_sort()):
        if not scales_channels(op, info):
            continue
        (x, w), y, platform = op.inputs, op.outputs[0], op.platform
        configs, out_config = list(op.input_quant_config), op.output_quant_config[0]
        weights = w.value.reshape([1] * (len(laid_shape(x, info)) - 1) + [-1]).clone()
        graph.remove_operation(op)
        param = graph.create_variable(value=weights, is_parameter=True)
        mul = graph.create_operation(op_type="Mul")
        config = OperationQuantizationConfig([configs[0], configs[1].copy()], [out_config])
        linked(graph, QuantableOperation(mul, config, platform), [x, param], [y])
        done += 1
    return done


@contextlib.contextmanager
def channel_scales_as_mul() -> Iterator[None]:
    """ESP-PPQ quantises a per-channel scaling, a weighted sum's weights say, to a depthwise convolution by one value
    per channel. After the export's layout pass this runs each one without bias as a Mul by those values broadcast
    along the channels (scales_as_muls): the same product, shift and rounding. ReDimNet2 b0 (KEHOACH 3.17) on board
    B: its stage sums' 27 Muls run as fast as their convolutions, at the PSRAM's rate; the window is 7 ms faster.
    """
    from esp_ppq.parser import espdl_exporter

    reset = espdl_exporter.reset_graph_layout

    def multiplied(graph):
        reset(graph)
        scales_as_muls(graph)

    espdl_exporter.reset_graph_layout = multiplied
    try:
        yield
    finally:
        espdl_exporter.reset_graph_layout = reset


def gives_input_back(op, info) -> bool:
    """op is a depthwise pointwise convolution without bias or activation whose weights are all 1, held exactly on
    their grid, from one grid and layout to the same: on the chip it shifts each input up and back down."""
    import torch
    from esp_ppq.IR.quantize import QuantableOperation

    if op.type != "Conv" or not isinstance(op, QuantableOperation) or len(op.inputs) != 2:
        return False
    (x, w), y, (given, held), made = op.inputs, op.outputs[0], op.input_quant_config, op.output_quant_config[0]
    spans = op.attributes["kernel_shape"] + op.attributes.get("strides", []) + op.attributes.get("dilations", [])
    if (
        any(v != 1 for v in spans)
        or any(op.attributes.get("pads", []))
        or op.attributes.get("activation", "Linear") != "Linear"
    ):
        return False
    if op.attributes.get("group", 1) != laid_shape(x, info)[-1] or laid_shape(x, info) != laid_shape(y, info):
        return False
    if given.scale is None or made.scale is None or held.scale is None or held.scale.numel() != 1:
        return False
    return (
        bool(torch.all(w.value == 1))
        and float(1 / held.scale) <= held.quant_max
        and torch.equal(given.scale, made.scale)
    )


def drop_identities(graph) -> int:
    """Each convolution gives_input_back finds, removed and its readers given its input; how many."""
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo

    info, done = ExporterPatternInfo(), 0
    for op in list(graph.topological_sort()):
        if not gives_input_back(op, info) or op.outputs[0].name in graph.outputs:
            continue
        x, y = op.inputs[0], op.outputs[0]
        for reader in list(y.dest_ops):
            reader.inputs[:] = [x if v is y else v for v in reader.inputs]
            x.dest_ops.append(reader)
        y.dest_ops.clear()
        graph.remove_operation(op)
        graph.remove_variable(y)
        done += 1
    return done


@contextlib.contextmanager
def identity_convolutions_dropped() -> Iterator[None]:
    """A weighted sum of one input has the weight 1, which ESP-PPQ quantises to a depthwise convolution that gives
    its input back on the same grid. After the export's layout pass this removes such convolutions (drop_identities),
    the same integers. ReDimNet2 b0 (KEHOACH 3.17) on board B: stage 0's sum of the stem output alone took 4.5 ms.
    """
    from esp_ppq.parser import espdl_exporter

    reset = espdl_exporter.reset_graph_layout

    def dropped(graph):
        reset(graph)
        drop_identities(graph)

    espdl_exporter.reset_graph_layout = dropped
    try:
        yield
    finally:
        espdl_exporter.reset_graph_layout = reset


def linked(graph, op, inputs: list, outputs: list):
    """op, quantised and with no variables yet, registered in graph and linked to inputs and outputs."""
    graph.operations[op.name] = op
    for var in inputs:
        op.inputs.append(var)
        var.dest_ops.append(op)
    for var in outputs:
        op.outputs.append(var)
        var.source_op = op
    return op


def broadcast_copies(graph) -> int:
    """Each Concat of copies of one variable, of size 1 along the joined axis, read only by a Transpose, as an Add of
    that variable laid as the Transpose lays it, which moves nothing, and zeros along the copies; how many."""
    import torch
    from esp_ppq.core import DataType, OperationQuantizationConfig, QuantizationStates
    from esp_ppq.IR.quantize import QuantableOperation
    from esp_ppq.parser.espdl.espdl_graph_utils import get_default_perm
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo

    info, done = ExporterPatternInfo(), 0
    for concat in [op for op in graph.topological_sort() if op.type == "Concat"]:
        (source, *_), joined = concat.inputs, concat.outputs[0]
        if not isinstance(concat, QuantableOperation) or any(v is not source for v in concat.inputs):
            continue
        if len(joined.dest_ops) != 1 or joined.dest_ops[0].type != "Transpose" or joined.name in graph.outputs:
            continue
        turn, laid = joined.dest_ops[0], laid_shape(source, info)
        axis, perm, count = concat.attributes["axis"] % len(laid), turn.attributes["perm"], len(concat.inputs)
        if laid[axis] != 1 or laid_shape(joined, info) != [count if i == axis else d for i, d in enumerate(laid)]:
            continue
        if laid_shape(turn.outputs[0], info) != [count if p == axis else laid[p] for p in perm]:
            continue
        y, platform = turn.outputs[0], concat.platform
        x_config, y_config = concat.input_quant_config[0], turn.output_quant_config[0]
        graph.remove_operation(turn)
        graph.remove_operation(concat)
        graph.remove_variable(joined)
        target = [laid[i] for i in perm]
        shape_config, zeros_config = x_config.copy(), x_config.copy()
        shape_config.state, zeros_config.state = QuantizationStates.FP32, QuantizationStates.ACTIVATED
        relaid = graph.create_variable()
        relaid.shape, relaid.dtype = target, source.dtype
        info.add_var_permute(relaid.name, get_default_perm(relaid))
        shape = graph.create_variable(value=torch.tensor(target, dtype=torch.int64), is_parameter=True)
        shape.dtype = DataType.INT64
        reshape = graph.create_operation(op_type="Reshape", attributes={"allowzero": 0})
        config = OperationQuantizationConfig([x_config, shape_config], [x_config])
        linked(graph, QuantableOperation(reshape, config, platform), [source, shape], [relaid])
        zeros = graph.create_variable(value=torch.zeros([count if p == axis else 1 for p in perm]), is_parameter=True)
        add = graph.create_operation(op_type="Add")
        config = OperationQuantizationConfig([x_config, zeros_config], [y_config])
        linked(graph, QuantableOperation(add, config, platform), [relaid, zeros], [y])
        done += 1
    return done


@contextlib.contextmanager
def copies_by_broadcast() -> Iterator[None]:
    """A one-channel map repeated into several channels concatenates channels first, so ESP-PPQ's export follows the
    Concat with a Transpose to channels last, a generic copy of every value. After the export's layout pass this
    writes the copies by broadcast_copies instead: an Add of zeros on the input's own grid, the same integers.
    ReDimNet2 b0 (KEHOACH 3.17) on board B: its stem's twelve copies of the log-mel took 2.5 ms to join and 33.6 ms
    to transpose.
    """
    from esp_ppq.parser import espdl_exporter

    reset = espdl_exporter.reset_graph_layout

    def broadcast(graph):
        reset(graph)
        broadcast_copies(graph)

    espdl_exporter.reset_graph_layout = broadcast
    try:
        yield
    finally:
        espdl_exporter.reset_graph_layout = reset


def same_quantizer(a, b, info) -> bool:
    """a and b, quantise nodes of one variable, give the same integers on the same grid, laid the same way."""
    import torch

    outs = a.outputs[0], b.outputs[0]
    return (
        a.type == b.type
        and outs[0].dtype == outs[1].dtype
        and all(torch.equal(p.value, q.value) for p, q in zip(a.inputs[1:], b.inputs[1:], strict=True))
        and info.get_var_exponents(outs[0].name) == info.get_var_exponents(outs[1].name)
        and info.get_var_permute(outs[0].name) == info.get_var_permute(outs[1].name)
    )


def share_quantizers(graph) -> int:
    """Merge the QuantizeLinear, RequantizeLinear and DequantizeLinear nodes that read one variable onto one grid into
    the first; how many went."""
    from esp_ppq.parser.espdl.espdl_typedef import QUANT_OP_SET, ExporterPatternInfo

    info, gone = ExporterPatternInfo(), 0
    for var in list(graph.variables.values()):
        kept = []
        for op in list(dict.fromkeys(var.dest_ops)):
            if op.type not in QUANT_OP_SET or op.name not in graph.operations or op.inputs[0] is not var:
                continue
            twin = op.outputs[0]
            first = next((k for k in kept if same_quantizer(k, op, info)), None)
            if first is None or twin.name in graph.outputs:
                kept.append(op)
                continue
            for reader in list(twin.dest_ops):
                reader.inputs[:] = [first.outputs[0] if v is twin else v for v in reader.inputs]
                first.outputs[0].dest_ops.append(reader)
            twin.dest_ops.clear()
            graph.remove_operation(op)
            graph.remove_variable(twin)
            gone += 1
    return gone


@contextlib.contextmanager
def lean_quantizers() -> Iterator[None]:
    """ESP-PPQ's export puts a QuantizeLinear, or a RequantizeLinear, before each reader that takes a variable on
    another grid than it was made on, one per reader even when several share that grid. esp-dl's int8 Softmax gives
    float32, so each reader of one quantises the whole of it again. After the export's passes this merges the equal
    ones of each variable. ReDimNet2 b0 (KEHOACH 3.17) on board B: its pool's two readers of the attention weights took
    two equal QuantizeLinear, 9 ms each, of 1.59 s a window.
    """
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def leaner(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        share_quantizers(graph)
        return graph

    EspdlExporter.prepare_graph = leaner
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare


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
    "softmax_as_espdl": softmax_as_espdl,
    "fuse_tanh_gelu": fuse_tanh_gelu,
    "lean_transposes": lean_transposes,
    "lean_quantizers": lean_quantizers,
    "aligned_pointwise": aligned_pointwise,
    "copies_by_broadcast": copies_by_broadcast,
    "cache_sized_convolutions": cache_sized_convolutions,
    "identity_convolutions_dropped": identity_convolutions_dropped,
    "channel_scales_as_mul": channel_scales_as_mul,
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
