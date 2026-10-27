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

import numpy as np

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


def rms_like_espdl(sum_sq, n: int, s_in, s_out, eps: float):
    """1 / sqrt(mean square + eps) times the input step over the output step, from a row's integer sum of squares over
    n values, as the S3 build of esp-dl's RMSNormalization takes it under -ffast-math: the mean square through
    (1.0f / n) * s_in^2 and fused with eps by madd.s, then sqrtf and a float32 division."""
    import torch

    # Tensor by tensor: torch divides a CUDA tensor by a Python number through its reciprocal.
    per_value = torch.ones_like(s_in) / torch.full_like(s_in, n) * (s_in * s_in)
    root = torch.sqrt(fma32(sum_sq, per_value, torch.full_like(sum_sq, eps)))
    return torch.ones_like(sum_sq) / root * (s_in / s_out)


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
        n = math.prod(x.shape[a] for a in axes)
        rms = rms_like_espdl(sum_sq, n, s_in, s_out, op.attributes.get("epsilon", 1e-5))
        y = torch.clamp(torch.floor(q * rms * weight + 0.5), out.quant_min, out.quant_max) * s_out
    return floating - floating.detach() + y


@contextlib.contextmanager
def rmsnorm_as_espdl() -> Iterator[None]:
    """ESP-PPQ simulates RMSNormalization as x / sqrt(mean(x^2) + eps) * scale rounded at its output; esp-dl's S3 kernel
    multiplies the integers by 1 / sqrtf of the mean square, already scaled between the grids, then by the float scale,
    and rounds half up, its mean square fused with eps under -ffast-math. Board B, command_ctc qat: 2 of 198 Gate 3
    windows scored a step off the simulation.
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


def float_of(word: int) -> np.float32:
    return np.array([word], dtype=np.uint32).view(np.float32)[0]


def word_of(x: np.float32) -> int:
    return int(np.array([x], dtype=np.float32).view(np.int32)[0])


# The constants of picolibc's sf_exp.c as its object for the S3 holds them; P2 and P4 stored positive, subtracted.
EXPF_LN2_HI = (float_of(0x3F317180), float_of(0xBF317180))
EXPF_LN2_LO = (float_of(0x3717F7D1), float_of(0xB717F7D1))
EXPF_HALF = (np.float32(0.5), np.float32(-0.5))
EXPF_INVLN2, EXPF_HUGE, EXPF_TWOM100 = float_of(0x3FB8AA3B), float_of(0x7149F2CA), float_of(0x0D800000)
EXPF_P1, EXPF_P2, EXPF_P3 = float_of(0x3E2AAAAB), float_of(0x3B360B61), float_of(0x388AB355)
EXPF_P4, EXPF_P5 = float_of(0x35DDEA0E), float_of(0x3331BB4C)


def expf_s3(x: float) -> np.float32:
    """expf as the S3 runs it: picolibc's libm/math/sf_exp.c, fdlibm's algorithm, read off its machine code. Every
    step is one float32 operation rounded once, none fused, the division __divsf3's; it can differ from the correctly
    rounded exp by an ulp."""
    one, two = np.float32(1.0), np.float32(2.0)
    x = np.float32(x)
    sx = word_of(x)
    xsb, hx = int(sx < 0), sx & 0x7FFFFFFF
    if hx > 0x7F800000:
        return x + x
    if hx == 0x7F800000:
        return np.float32(0.0) if xsb else x
    if sx > 0x42B17217:
        return np.float32(np.inf)
    if xsb and hx > 0x42CFF1B5:
        return np.float32(0.0)
    k, hi, lo = 0, np.float32(0.0), np.float32(0.0)
    if hx > 0x3EB17218:
        if hx <= 0x3F851591:
            hi, lo, k = x - EXPF_LN2_HI[xsb], EXPF_LN2_LO[xsb], 1 - xsb - xsb
        else:
            k = int(x * EXPF_INVLN2 + EXPF_HALF[xsb])
            t = np.float32(k)
            hi, lo = x - t * EXPF_LN2_HI[0], t * EXPF_LN2_LO[0]
        x = hi - lo
    elif hx <= 0x33FFFFFF and EXPF_HUGE + x > one:
        return one + x
    t = x * x
    c = x - ((((t * EXPF_P5 - EXPF_P4) * t + EXPF_P3) * t - EXPF_P2) * t + EXPF_P1) * t
    xc = x * c
    if k == 0:
        return one - (xc / (c - two) - x)
    y = word_of(one - ((lo - xc / (two - c)) - hi))
    if k >= -125:
        return np.array([y + (k << 23)], dtype=np.int32).view(np.float32)[0]
    return np.array([y + ((k + 100) << 23)], dtype=np.int32).view(np.float32)[0] * EXPF_TWOM100


@functools.lru_cache(maxsize=64)
def exp_table_s3(scale: float) -> np.ndarray:
    """esp-dl's Softmax table at an input scale, as tool::gen_lut_8bit fills it on the S3: entry i is the S3's expf of
    scale times i - 128, a float32 product that is exact for esp-dl's power-of-two scales."""
    s = np.float32(scale)
    return np.array([expf_s3(s * np.float32(i - 128)) for i in range(256)], dtype=np.float32)


def softmax_like_espdl(op, values: list, ctx=None, *, simulate: Callable, platforms: frozenset, **kwargs):
    """op's output as esp-dl's Softmax computes it from an int8 input on one of platforms: each step's exp from its
    256-entry float32 table of the S3's expf, the row summed in float32 one value after another, each multiplied by
    the float32 reciprocal of that sum, as -ffast-math builds the source's division; simulate's output otherwise."""
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
        table = torch.from_numpy(exp_table_s3(float(s.flatten()[0]))).to(floating.device)
        e = table[q + 128].movedim(axis, -1)
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
    slice_outputs lays it by output_sizes into slices of at most half of it, when they make more than one; how many."""
    from esp_ppq.IR.quantize import QuantableOperation
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo

    info, done = ExporterPatternInfo(), 0
    for op in list(graph.topological_sort()):
        if op.type != "Conv" or not isinstance(op, QuantableOperation) or op.attributes.get("group", 1) != 1:
            continue
        w, laid = op.inputs[1].value, laid_shape(op.outputs[0], info)
        if any(k != 1 for k in op.attributes["kernel_shape"]) or math.prod(laid[:-1]) < 2:
            continue
        sizes = output_sizes(laid[-1], w.numel(), DATA_CACHE_BYTES // 2) if w.numel() > DATA_CACHE_BYTES else []
        if len(sizes) > 1:
            slice_outputs(graph, op, sizes, info)
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


SOFTMAX_QUANT = "SoftmaxQuant"


def softmax_quant_from(graph, softmax) -> bool:
    """softmax and the QuantizeLinear alone reading its float32 output replaced by one SoftmaxQuant writing that
    node's int8 output, when softmax reads one int8 grid and the node quantises onto one int8 grid with no offset."""
    from esp_ppq.core import DataType
    from esp_ppq.IR.quantize import QuantableOperation

    if softmax.type != "Softmax" or not isinstance(softmax, QuantableOperation) or len(softmax.outputs) != 1:
        return False
    probs, given = softmax.outputs[0], softmax.input_quant_config[0]
    if probs.name in graph.outputs or len(probs.dest_ops) != 1 or given.num_of_bits != 8 or given.scale.numel() != 1:
        return False
    (quant,) = probs.dest_ops
    if quant.type != "QuantizeLinear" or quant.inputs[0] is not probs or quant.outputs[0].dtype != DataType.INT8:
        return False
    scale, *offset = (v.value for v in quant.inputs[1:])
    if scale is None or scale.numel() != 1 or any(o is not None and bool(o.any()) for o in offset):
        return False
    out = quant.outputs[0]
    graph.remove_operation(quant)
    graph.remove_variable(probs)
    softmax.outputs.append(out)
    out.source_op = softmax
    softmax.type = SOFTMAX_QUANT
    return True


def scaled_by_constant(graph, var, info) -> tuple | None:
    """(mul, map, constant, shift) when var, read by one op alone, is an int8 Mul of a map by one int8 constant, its
    rows at least CHANNEL_STEP long so esp-dl's S3 vector kernel runs it, the shift onto var's exponent to the right;
    None otherwise."""
    from esp_ppq.IR.quantize import QuantableOperation

    mul = var.source_op
    if mul is None or mul.type != "Mul" or not isinstance(mul, QuantableOperation) or len(mul.inputs) != 2:
        return None
    if var.name in graph.outputs or len(var.dest_ops) != 1 or laid_shape(var, info)[-1] < CHANNEL_STEP:
        return None
    constants = [v for v in mul.inputs if v.is_parameter and v.value is not None and v.value.numel() == 1]
    maps = [v for v in mul.inputs if not v.is_parameter]
    if len(constants) != 1 or len(maps) != 1 or not all(int8_tensor(c) for c in mul.input_quant_config):
        return None
    exponents = [exponent_of(v, info) for v in (var, maps[0], constants[0])]
    if None in exponents or exponents[0] - exponents[1] - exponents[2] < 0:
        return None
    return mul, maps[0], int(constants[0].value.item()), exponents[0] - exponents[1] - exponents[2]


def products_by_byte(constant: int, shift: int) -> np.ndarray:
    """int8 x * constant by the byte of x, as tie728_s8_mul_w1_16_w2_1 gives it: the QACC product rounded half up
    by shift as tie728_s8_vector_round_result does, saturated."""
    x = np.arange(256, dtype=np.int64).astype(np.uint8).view(np.int8).astype(np.int64)
    product = x * constant
    if shift:
        product = ((product >> (shift - 1)) + 1) >> 1
    return np.clip(product, -128, 127).astype(np.int8)


def softmax_scale_from(graph, softmax, info) -> bool:
    """A SoftmaxQuant reading the int8 product of a map by one constant reads the map instead, the products by the
    map's byte as a second input on the product's exponent; the Mul and its product go."""
    import torch
    from esp_ppq.core import DataType

    if softmax.type != SOFTMAX_QUANT or len(softmax.inputs) != 1:
        return False
    found = scaled_by_constant(graph, softmax.inputs[0], info)
    if found is None:
        return False
    mul, source, constant, shift = found
    product, exponent = mul.outputs[0], exponent_of(softmax.inputs[0], info)
    configs = [mul.input_quant_config[mul.inputs.index(source)], softmax.input_quant_config[0]]
    product.dest_ops.clear()
    graph.remove_operation(mul)
    graph.remove_variable(product)
    table = graph.create_variable(value=torch.from_numpy(products_by_byte(constant, shift)), is_parameter=True)
    table.dtype = DataType.INT8
    info.add_var_exponents(table.name, exponent)
    softmax.inputs[:] = [source, table]
    source.dest_ops.append(softmax)
    table.dest_ops.append(softmax)
    softmax.config.input_quantization_config[:] = configs
    return True


@contextlib.contextmanager
def softmax_scale() -> Iterator[None]:
    """Each SoftmaxQuant reading an int8 map times one constant reads the map, the products by its byte as a table
    (KEHOACH 3.14 step 2.5): esp-dl's Mul wrote every scaled score through PSRAM for the op to read back. The op looks
    each byte up in the table before its exp table. Run after softmax_quant. ReDimNet2 b0 (KEHOACH 3.17) on board B:
    each attention at 148 frames scaled its scores in 3.1 ms.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.operations.values()):
            softmax_scale_from(graph, op, info)
        return graph

    EspdlExporter.prepare_graph = joining
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare


@contextlib.contextmanager
def softmax_quant() -> Iterator[None]:
    """Each int8 Softmax and the QuantizeLinear alone reading it exported as one SoftmaxQuant (KEHOACH 3.14 step 2.5):
    esp-dl's Softmax writes float32 a column apart through PSRAM for the quantiser to read back. The op takes the same
    table, sums and rounding and writes int8. Run after lean_quantizers, which leaves one quantiser per grid.
    ReDimNet2 b0 (KEHOACH 3.17) on board B: each attention at 148 frames spent 24 ms in the pair.
    """
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        for op in list(graph.operations.values()):
            softmax_quant_from(graph, op)
        return graph

    EspdlExporter.prepare_graph = joining
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare


LAYERNORM_S8 = "LayerNormS8"


def layernorm_s8_from(op) -> bool:
    """op renamed LayerNormS8 when it is an int8 LayerNormalization over the last axis, one grid each for its input,
    output, gamma and beta, with no mean or deviation output."""
    from esp_ppq.IR.quantize import QuantableOperation

    if op.type != "LayerNormalization" or not isinstance(op, QuantableOperation) or len(op.outputs) != 1:
        return False
    configs = [*op.input_quant_config, *op.output_quant_config]
    if op.attributes.get("axis", -1) != -1 or any(c.num_of_bits != 8 or c.scale.numel() != 1 for c in configs):
        return False
    op.type = LAYERNORM_S8
    return True


@contextlib.contextmanager
def layernorm_s8() -> Iterator[None]:
    """Each int8 LayerNormalization over the last axis exported as LayerNormS8 (KEHOACH 3.14 step 2.5): esp-dl's kernel
    takes each mean in double, which the S3 runs in software, and rescales gamma, beta and its rounding per value. The
    op takes layernorm_as_espdl's float32 steps in order, the mean as a float division where that gives the same.
    ReDimNet2 b0 (KEHOACH 3.17) on board B: the stem's norm took 65 ms of 1.17 s a window.
    """
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def renaming(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        for op in graph.operations.values():
            layernorm_s8_from(op)
        return graph

    EspdlExporter.prepare_graph = renaming
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare


STAGE_SUM = "StageSum"


def int8_tensor(config) -> bool:
    return config.num_of_bits == 8 and config.scale.numel() == 1


def repeated_rows(resize, info) -> int:
    """How many times resize repeats each row of its int8 [N, T, C] input, as a nearest, asymmetric, floor Resize by
    an integer factor on one exponent does; 0 when it does anything else."""
    if resize is None or resize.type != "Resize":
        return 0
    attrs = resize.attributes
    modes = attrs.get("mode"), attrs.get("coordinate_transformation_mode"), attrs.get("nearest_mode")
    if modes != ("nearest", "asymmetric", "floor"):
        return 0
    src, out = resize.inputs[0], resize.outputs[0]
    a, b = laid_shape(src, info), laid_shape(out, info)
    exponents = info.get_var_exponents(src.name), info.get_var_exponents(out.name)
    if (
        len(a) != 3
        or len(b) != 3
        or a[::2] != b[::2]
        or b[1] % a[1]
        or not exponents[0]
        or len(set(map(tuple, exponents))) != 1
    ):
        return 0
    return b[1] // a[1]


def stage_term(graph, var, info) -> tuple | None:
    """(mul, map, weights, rows) when var, read by one op alone, is an int8 product of a [N, T, C] map by its C channel
    weights: map is before a Resize that only repeats its rows `rows` times; None otherwise."""
    from esp_ppq.IR.quantize import QuantableOperation

    mul = var.source_op
    if mul is None or mul.type != "Mul" or not isinstance(mul, QuantableOperation) or var.name in graph.outputs:
        return None
    if len(var.dest_ops) != 1 or not all(int8_tensor(c) for c in [*mul.input_quant_config, *mul.output_quant_config]):
        return None
    weights = [v for v in mul.inputs if v.is_parameter]
    maps = [v for v in mul.inputs if not v.is_parameter]
    if len(weights) != 1 or len(maps) != 1:
        return None
    shape = laid_shape(maps[0], info)
    if len(shape) != 3 or shape[-1] % CHANNEL_STEP or math.prod(laid_shape(weights[0], info)) != shape[-1]:
        return None
    rows = repeated_rows(maps[0].source_op, info)
    source = maps[0].source_op.inputs[0] if rows else maps[0]
    return mul, source, weights[0], max(rows, 1)


def stage_chain(graph, add, info) -> tuple[list, list] | None:
    """(terms, adds) when add ends a chain of Adds, each adding one stage_term to the sum before it and the first one
    adding two; the terms in the order they are added. None otherwise."""
    from esp_ppq.IR.quantize import QuantableOperation

    if add.type != "Add" or not isinstance(add, QuantableOperation) or len(add.inputs) != 2:
        return None
    for prev, last in (add.inputs, add.inputs[::-1]):
        term = stage_term(graph, last, info)
        if term is None:
            continue
        first = stage_term(graph, prev, info)
        if first is not None:
            return [first, term], [add]
        source = prev.source_op
        if source is not None and prev.dest_ops == [add] and prev.name not in graph.outputs:
            before = stage_chain(graph, source, info)
            if before is not None:
                return [*before[0], term], [*before[1], add]
    return None


def stage_sum_from(graph, add, info) -> bool:
    """add and the chain of stage_chain ending at it replaced by one StageSum reading each term's map and weights, on
    the one exponent every product and sum of the chain shares; Resizes no op reads any more go too."""
    from esp_ppq.core import OperationQuantizationConfig

    found = stage_chain(graph, add, info)
    if found is None:
        return False
    terms, adds = found
    sums = [t[0].outputs[0] for t in terms] + [a.outputs[0] for a in adds]
    exponents = {tuple(info.get_var_exponents(v.name) or ()) for v in sums}
    if len(exponents) != 1 or () in exponents:
        return False
    configs, resizes = [], set()
    for mul, _, weights, rows in terms:
        x = next(v for v in mul.inputs if not v.is_parameter)
        configs += [mul.input_quant_config[mul.inputs.index(x)], mul.input_quant_config[mul.inputs.index(weights)]]
        if rows > 1:
            resizes.add(x.source_op)
    for op in [t[0] for t in terms] + adds:
        for v in op.inputs:
            v.dest_ops.remove(op)
        op.inputs.clear()
    for op in [t[0] for t in terms] + adds[:-1]:
        product = op.outputs[0]
        graph.remove_operation(op)
        graph.remove_variable(product)
    for _, source, weights, _ in terms:
        for v in (source, weights):
            add.inputs.append(v)
            v.dest_ops.append(add)
    add.type = STAGE_SUM
    add.attributes["rows"] = [t[3] for t in terms]
    add.config = OperationQuantizationConfig(configs, add.output_quant_config)
    for resize in resizes:
        if not resize.outputs[0].dest_ops and resize.outputs[0].name not in graph.outputs:
            out = resize.outputs[0]
            graph.remove_operation(resize)
            graph.remove_variable(out)
    return True


@contextlib.contextmanager
def stage_sum() -> Iterator[None]:
    """Each chain of Adds summing int8 per-channel products of stage outputs exported as one StageSum (KEHOACH 3.14
    step 2.5), ReDimNet2's weighted sums of the stages: each product and sum went through PSRAM. The op takes esp-dl's
    Mul rounding and saturating Add per value, reading each map once a row, before a Resize that repeats its rows.
    ReDimNet2 b0 (KEHOACH 3.17) on board B: 27 Muls, 21 Adds and 4 Resizes, ~210 ms of 1.17 s a window.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in reversed(list(graph.topological_sort())):
            if op.name in graph.operations:
                stage_sum_from(graph, op, info)
        return graph

    EspdlExporter.prepare_graph = joining
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare


CONV_BLOCK = "ConvBlock"
# (kernel, depthwise, bias, activation) of a ReDimNet2 conv block's convolutions from its input on.
BLOCK_STEPS = (
    (3, True, False, "Linear"),
    (1, False, True, "Relu"),
    (1, True, True, "Linear"),
    (3, True, False, "Linear"),
    (1, False, True, "Linear"),
)


def exponent_of(var, info) -> int | None:
    found = info.get_var_exponents(var.name)
    return found[0] if found and len(found) == 1 else None


def behind_reshapes(graph, var) -> tuple:
    """(the op making var, the Reshapes passed) going back from var through Reshapes, each variable on the way read
    by one op alone and no graph output; (None, []) when one is not."""
    passed = []
    while var.name not in graph.outputs and len(var.dest_ops) == 1 and var.source_op is not None:
        if var.source_op.type != "Reshape":
            return var.source_op, passed
        passed.append(var.source_op)
        var = var.source_op.inputs[0]
    return None, []


def block_conv(op, kernel: int, depthwise: bool, bias: bool, activation: str, info) -> bool:
    """op is an int8 convolution of one grid a tensor, kernel by kernel at a stride of 1 padded to keep the map's
    size, depthwise or not, with or without bias, then activation."""
    from esp_ppq.IR.quantize import QuantableOperation

    if op is None or op.type != "Conv" or not isinstance(op, QuantableOperation) or len(op.inputs) != 2 + bias:
        return False
    attrs, laid = op.attributes, laid_shape(op.inputs[0], info)
    return (
        len(laid) == 4
        and laid == laid_shape(op.outputs[0], info)
        and attrs.get("kernel_shape") == [kernel, kernel]
        and attrs.get("strides", [1, 1]) == [1, 1]
        and attrs.get("dilations", [1, 1]) == [1, 1]
        and attrs.get("pads", [0] * 4) == [kernel // 2] * 4
        and attrs.get("group", 1) == (laid[-1] if depthwise else 1)
        and attrs.get("activation", "Linear") == activation
        and all(int8_tensor(c) for c in [*op.input_quant_config[:2], *op.output_quant_config])
    )


def relu_of_sum(graph, relu, info) -> bool:
    """relu is an int8 Relu giving max(0, x) on its input's exponent, of an Add read by it alone."""
    import torch
    from esp_ppq.IR.quantize import QuantableOperation

    if relu.type != "Relu" or not isinstance(relu, QuantableOperation):
        return False
    s, add = relu.inputs[0], relu.inputs[0].source_op
    if add is None or add.type != "Add" or len(add.inputs) != 2 or s.dest_ops != [relu] or s.name in graph.outputs:
        return False
    if exponent_of(s, info) is None or exponent_of(s, info) != exponent_of(relu.outputs[0], info):
        return False
    lut = relu.attributes.get("lut")
    table = None if lut is None else info.get_lut(lut)
    return lut is None or (
        table is not None and torch.equal(table.flatten().long().cpu(), torch.arange(-128, 128).clamp(min=0))
    )


def block_shifts(x, convs, total, info) -> list[int] | None:
    """Each convolution's mac shift, the five read in turn from x, when each takes the exponent the one before gives
    and its bias that of its products; total, the exponent of the residual added, last. None otherwise."""
    shifts, e_in = [], exponent_of(x, info)
    for op in convs:
        e_w, e_out = exponent_of(op.inputs[1], info), exponent_of(op.outputs[0], info)
        if None in (e_in, e_w, e_out) or exponent_of(op.inputs[0], info) != e_in:
            return None
        if len(op.inputs) == 3 and exponent_of(op.inputs[2], info) != e_in + e_w:
            return None
        shifts.append(int(e_out - e_in - e_w))
        e_in = e_out
    return [*shifts, int(total - exponent_of(x, info))]


def conv_block_parts(graph, relu, info) -> tuple | None:
    """(x, convs, reshapes, requantiser, shifts) when relu ends a ReDimNet2 conv block on an int8 [1, T, F, C] map x,
    C a multiple of 8: the convolutions of BLOCK_STEPS, Reshapes alone between them, added to x, or to x requantised
    for the sum alone, on the sum's exponent, then ReLU; shifts as ConvBlock takes them, each convolution's 1 or more
    to the right, the residual's 0 or more. None otherwise."""
    if not relu_of_sum(graph, relu, info):
        return None
    add, total = relu.inputs[0].source_op, exponent_of(relu.inputs[0], info)
    for e, r in (add.inputs, add.inputs[::-1]):
        convs, reshapes, var = [], [], e
        for step in reversed(BLOCK_STEPS):
            op, passed = behind_reshapes(graph, var)
            if not block_conv(op, *step, info):
                break
            convs.insert(0, op)
            reshapes += passed
            var = op.inputs[0]
        else:
            x, requantiser = var, r.source_op
            if r is x:
                requantiser = None
            elif (
                requantiser is None
                or requantiser.type != "RequantizeLinear"
                or requantiser.inputs[0] is not x
                or r.dest_ops != [add]
                or r.name in graph.outputs
            ):
                return None
            laid = laid_shape(x, info)
            if laid[0] != 1 or laid[-1] % (CHANNEL_STEP // 2) or laid_shape(relu.outputs[0], info) != laid:
                return None
            if total != exponent_of(e, info) or exponent_of(r, info) != total:
                return None
            shifts = block_shifts(x, convs, total, info)
            if shifts is None or min(shifts[:-1]) < 1 or shifts[-1] < 0:
                return None
            return x, convs, reshapes, requantiser, shifts
    return None


def depthwise_taps(stored: np.ndarray, n: int, taps: int) -> np.ndarray:
    """A depthwise convolution's (taps, n) weights from ESP-PPQ's S3 layout of them: each 16 channels' taps, the
    channels innermost, then the rest's taps (ResetParamLayoutPattern)."""
    flat, aligned = stored.reshape(-1), n // CHANNEL_STEP * CHANNEL_STEP
    head = flat[: aligned * taps].reshape(n // CHANNEL_STEP, taps, CHANNEL_STEP).transpose(1, 0, 2)
    return np.concatenate([head.reshape(taps, aligned), flat[aligned * taps :].reshape(taps, n - aligned)], axis=1)


def dense_filters(stored: np.ndarray, n: int) -> np.ndarray:
    """A pointwise convolution's (n outputs, inputs) weights from ESP-PPQ's S3 layout of them, each 16 outputs
    interleaved innermost and the rest as they are (ResetParamLayoutPattern)."""
    flat, aligned = stored.reshape(-1), n // CHANNEL_STEP * CHANNEL_STEP
    per = flat.size // n
    head = flat[: aligned * per].reshape(n // CHANNEL_STEP, per, CHANNEL_STEP).transpose(0, 2, 1)
    return np.concatenate([head.reshape(aligned, per), flat[aligned * per :].reshape(n - aligned, per)])


def first_of_repeats(values: np.ndarray, k: int, c: int) -> np.ndarray | None:
    """values[..., :c] when the last axis holds it k times over, as merge_columns repeats a depthwise op's values."""
    return (
        values[..., :c] if values.shape[-1] == k * c and np.array_equal(values, np.tile(values[..., :c], k)) else None
    )


def block_values(op, c: int, info) -> list[np.ndarray] | None:
    """op's weights and bias as ConvBlock's kernels take them, its channels c padded to Cp, a multiple of 16, with
    zeros: taps [kernel * kernel][Cp], or pointwise weights [Cp / 16][c][16], the outputs padded; bias [Cp]. op may
    run over k columns merged into its channels, the same block of weights each (merge_columns). None when it does
    not."""
    stored = [v.value.cpu().numpy() for v in op.inputs[1:]]
    n = laid_shape(op.inputs[0], info)[-1]
    k, cp = n // c, -(-c // CHANNEL_STEP) * CHANNEL_STEP
    if op.attributes.get("group", 1) == 1:
        merged = dense_filters(stored[0], n)
        if n != k * c or not np.array_equal(merged, np.kron(np.eye(k, dtype=merged.dtype), merged[:c, :c])):
            return None
        padded = np.pad(merged[:c, :c], [(0, cp - c), (0, 0)])
        weights = padded.reshape(cp // CHANNEL_STEP, CHANNEL_STEP, c).transpose(0, 2, 1)
    else:
        kernel = op.attributes["kernel_shape"][0]
        taps = first_of_repeats(depthwise_taps(stored[0], n, kernel * kernel), k, c)
        weights = None if taps is None else np.pad(taps, [(0, 0), (0, cp - c)])
    biases = [first_of_repeats(b.reshape(-1), k, c) for b in stored[1:]]
    if weights is None or any(b is None for b in biases):
        return None
    return [np.ascontiguousarray(weights), *(np.pad(b, (0, cp - c)) for b in biases)]


def conv_block_from(graph, relu, info) -> bool:
    """relu and the conv block conv_block_parts finds before it replaced by one ConvBlock reading x, then each
    convolution's weights and bias in turn, laid as block_values lays them; the block's ops and middle maps go."""
    import torch
    from esp_ppq.core import OperationQuantizationConfig

    found = conv_block_parts(graph, relu, info)
    if found is None:
        return False
    x, convs, reshapes, requantiser, shifts = found
    c = laid_shape(x, info)[-1]
    laid = [block_values(op, c, info) for op in convs]
    if any(v is None for v in laid):
        return False
    params, configs = [], [convs[0].input_quant_config[0]]
    for op, values in zip(convs, laid, strict=True):
        for v, value, config in zip(op.inputs[1:], values, op.input_quant_config[1:], strict=True):
            v.value = torch.from_numpy(value).to(v.value.dtype)
            v.dest_ops.remove(op)
            params.append(v)
            configs.append(config)
        del op.inputs[1:]
    add = relu.inputs[0].source_op
    relu.inputs[0].dest_ops.remove(relu)
    relu.inputs.clear()
    gone = [add, *convs, *reshapes, *([requantiser] if requantiser is not None else [])]
    middles = [v for op in gone for v in op.outputs]
    for op in gone:
        graph.remove_operation(op)
    for v in middles:
        graph.remove_variable(v)
    for v in [x, *params]:
        relu.inputs.append(v)
        v.dest_ops.append(relu)
    relu.type = CONV_BLOCK
    relu.attributes.pop("lut", None)
    relu.attributes["shifts"] = shifts
    relu.config = OperationQuantizationConfig(configs, relu.output_quant_config)
    return True


# Values a pixel the ConvBlock kernels take for a 1x1 convolution joined in front of a block, and give behind it.
BLOCK_PRE_INPUTS = (12, 24)
BLOCK_POST_OUTPUTS = (12,)


def unmerged_pointwise(op, c_in: int | None, c_out: int | None, info) -> tuple | None:
    """(weights [n, k], bias [n], shift) of op, an int8 1x1 convolution with bias, as it runs on each pixel before
    merge_columns laid m of them as channels: m from c_out, or from c_in when c_out is None, op's laid channel counts
    being m * k in and m * n out; None when op is no such convolution or its weights are not m blocks of one."""
    from esp_ppq.IR.quantize import QuantableOperation

    if op is None or op.type != "Conv" or not isinstance(op, QuantableOperation) or len(op.inputs) != 3:
        return None
    attrs = op.attributes
    ones = [*attrs.get("kernel_shape", [0]), *attrs.get("strides", []), *attrs.get("dilations", [])]
    if attrs.get("group", 1) != 1 or any(v != 1 for v in ones) or any(attrs.get("pads", [])):
        return None
    if attrs.get("activation", "Linear") != "Linear":
        return None
    if not all(int8_tensor(c) for c in [*op.input_quant_config[:2], *op.output_quant_config]):
        return None
    km, nm = laid_shape(op.inputs[0], info)[-1], laid_shape(op.outputs[0], info)[-1]
    m = nm // c_out if c_out else km // c_in
    if m < 1 or km % m or nm % m or (c_out and nm != m * c_out) or (c_in and km != m * c_in):
        return None
    k, n = km // m, nm // m
    dense = dense_filters(op.inputs[1].value.cpu().numpy(), nm)
    base = dense[:n, :k]
    if not np.array_equal(dense, np.kron(np.eye(m, dtype=dense.dtype), base)):
        return None
    bias = first_of_repeats(op.inputs[2].value.cpu().numpy().reshape(-1), m, n)
    e_x, e_w, e_y = (exponent_of(v, info) for v in op.inputs[:2] + op.outputs[:1])
    if bias is None or None in (e_x, e_w, e_y) or e_y - e_x - e_w < 1 or exponent_of(op.inputs[2], info) != e_x + e_w:
        return None
    return base, bias, int(e_y - e_x - e_w)


def laid_pointwise(weights: np.ndarray, bias: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """weights [n, k] as the pointwise kernels read them, [Np / 16][k][16], and bias [Np], n padded with zeros to a
    multiple of 16."""
    n, k = weights.shape
    np_ = -(-n // CHANNEL_STEP) * CHANNEL_STEP
    padded = np.pad(weights, [(0, np_ - n), (0, 0)])
    laid = np.ascontiguousarray(padded.reshape(np_ // CHANNEL_STEP, CHANNEL_STEP, k).transpose(0, 2, 1))
    return laid, np.pad(bias, (0, np_ - n))


def join_block_params(block, conv, laid: tuple, configs: list) -> None:
    """conv's weights and bias, laid as given, appended to block's inputs with their configs; conv loses them."""
    import torch

    for v, value in zip(conv.inputs[1:3], laid, strict=True):
        v.value = torch.from_numpy(value).to(v.value.dtype)
        v.dest_ops.remove(conv)
        block.inputs.append(v)
        v.dest_ops.append(block)
    configs += list(conv.input_quant_config[1:3])
    del conv.inputs[1:]


def block_pre(graph, block, info) -> bool:
    """The 1x1 convolution making block's x, through Reshapes each read by one op alone, joined into block when it
    reads K in BLOCK_PRE_INPUTS values a pixel of x: its input becomes block's first, its weights and bias follow the
    block's, "pre" holds K and its shift, "out_shape" block's output's laid shape."""
    from esp_ppq.core import OperationQuantizationConfig

    x = block.inputs[0]
    op, passed = behind_reshapes(graph, x)
    lx = laid_shape(x, info)
    found = unmerged_pointwise(op, None, lx[-1], info) if len(lx) == 4 else None
    if found is None or found[0].shape[1] not in BLOCK_PRE_INPUTS:
        return False
    weights, bias, shift = found
    k, source = weights.shape[1], op.inputs[0]
    ls = laid_shape(source, info)
    if len(ls) != 4 or ls[:2] != lx[:2] or ls[2] * ls[3] != lx[2] * k:
        return False
    configs = [op.input_quant_config[0], *block.config.input_quantization_config[1:]]
    join_block_params(block, op, laid_pointwise(weights, bias), configs)
    middles = [op.outputs[0], *(r.outputs[0] for r in passed)]
    source.dest_ops.remove(op)
    block.inputs.remove(x)
    x.dest_ops.remove(block)
    for gone in [op, *passed]:
        graph.remove_operation(gone)
    for v in middles:
        graph.remove_variable(v)
    block.inputs.insert(0, source)
    source.dest_ops.append(block)
    block.attributes["pre"] = [int(k), shift]
    block.attributes["out_shape"] = [int(v) for v in laid_shape(block.outputs[0], info)]
    block.config = OperationQuantizationConfig(configs, block.output_quant_config)
    return True


def block_post(graph, block, info) -> bool:
    """The 1x1 convolution alone reading block's output, through Reshapes each read by one op alone, joined into
    block when it gives N in BLOCK_POST_OUTPUTS values a pixel: its output becomes block's, its weights and bias
    follow block's inputs, "post" holds N and its shift, "out_shape" the new output's laid shape."""
    from esp_ppq.core import OperationQuantizationConfig

    y, passed = block.outputs[0], []
    var = y
    while var.name not in graph.outputs and len(var.dest_ops) == 1 and var.dest_ops[0].type == "Reshape":
        passed.append(var.dest_ops[0])
        var = var.dest_ops[0].outputs[0]
    if var.name in graph.outputs or len(var.dest_ops) != 1:
        return False
    op, ly = var.dest_ops[0], laid_shape(y, info)
    found = unmerged_pointwise(op, ly[-1], None, info) if len(ly) == 4 else None
    if found is None or found[0].shape[0] not in BLOCK_POST_OUTPUTS:
        return False
    weights, bias, shift = found
    n, out = weights.shape[0], op.outputs[0]
    lo = laid_shape(out, info)
    if len(lo) != 4 or lo[:2] != ly[:2] or lo[2] * lo[3] != ly[2] * n:
        return False
    configs = list(block.config.input_quantization_config)
    join_block_params(block, op, laid_pointwise(weights, bias), configs)
    out_config = op.output_quant_config[0]
    middles = [y, *(r.outputs[0] for r in passed)]
    for gone in [*passed, op]:
        graph.remove_operation(gone)
    for v in middles:
        graph.remove_variable(v)
    block.outputs.append(out)
    out.source_op = block
    block.attributes["post"] = [int(n), shift]
    block.attributes["out_shape"] = [int(v) for v in lo]
    block.config = OperationQuantizationConfig(configs, [out_config])
    return True


@contextlib.contextmanager
def conv_block() -> Iterator[None]:
    """Each int8 ReDimNet2 conv block exported as one ConvBlock (KEHOACH 3.14 step 2.5): esp-dl runs its eight ops
    one map at a time through PSRAM, its depthwise ones a pixel a call, 24 channels unaligned. The op runs the block
    a row at a time with its maps' rows in the cache, each op's integers as esp-dl's kernels give them. Run after
    aligned_pointwise, whose merged columns it reads back. ReDimNet2 b0 (KEHOACH 3.17) on board B: its 21 blocks took
    ~500 ms of 0.96 s a window.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.topological_sort()):
            if op.name in graph.operations and conv_block_from(graph, op, info):
                block_pre(graph, op, info)
                block_post(graph, op, info)
        return graph

    EspdlExporter.prepare_graph = joining
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare


MATMUL_S8 = "MatMulS8"


def bias_after(graph, out, info) -> tuple | None:
    """(add, bias) when out, read by one Add alone, gets an int8 bias of its last axis added on its own exponent, the
    sum's too; None otherwise."""
    if out.name in graph.outputs or len(out.dest_ops) != 1:
        return None
    (add,) = out.dest_ops
    if add.type != "Add" or len(add.inputs) != 2:
        return None
    biases = [v for v in add.inputs if v.is_parameter and v.value is not None]
    if len(biases) != 1 or biases[0].value.numel() != laid_shape(out, info)[-1]:
        return None
    e = exponent_of(out, info)
    if e is None or exponent_of(biases[0], info) != e or exponent_of(add.outputs[0], info) != e:
        return None
    return add, biases[0]


def matmul_s8_from(graph, op, info) -> bool:
    """op, an int8 MatMul of a map's rows [..., K] by constant int8 weights [K, N], K and N multiples of 8 and its mac
    shift 1 or more, as one MatMulS8 taking also the bias Add alone reading it: weights [Np / 16][K][16], bias
    [Np], Np being N padded with zeros to a multiple of 16."""
    import torch
    from esp_ppq.core import OperationQuantizationConfig
    from esp_ppq.IR.quantize import QuantableOperation

    if op.type != "MatMul" or not isinstance(op, QuantableOperation) or len(op.inputs) != 2:
        return False
    (x, w), out = op.inputs, op.outputs[0]
    if x.is_parameter or not w.is_parameter or w.value is None or w.value.dim() != 2:
        return False
    k, n = w.value.shape
    if laid_shape(x, info)[-1] != k or k % (CHANNEL_STEP // 2) or n % (CHANNEL_STEP // 2):
        return False
    if not all(int8_tensor(c) for c in [*op.input_quant_config, *op.output_quant_config]):
        return False
    activation, exponents = op.attributes.get("activation", "Linear"), [exponent_of(v, info) for v in (out, x, w)]
    if activation not in ("Linear", "Relu") or None in exponents or exponents[0] - exponents[1] - exponents[2] < 1:
        return False
    padded = -(-n // CHANNEL_STEP) * CHANNEL_STEP
    weights = np.pad(dense_filters(w.value.cpu().numpy(), n), [(0, padded - n), (0, 0)])
    w.value = torch.from_numpy(
        np.ascontiguousarray(weights.reshape(padded // CHANNEL_STEP, CHANNEL_STEP, k).transpose(0, 2, 1))
    )
    configs, out_config = list(op.input_quant_config), op.output_quant_config[0]
    found = bias_after(graph, out, info)
    if found is not None:
        add, bias = found
        configs.append(add.input_quant_config[add.inputs.index(bias)])
        out_config, total = add.output_quant_config[0], add.outputs[0]
        add.inputs.remove(bias)
        bias.dest_ops.remove(add)
        graph.remove_operation(add)
        graph.remove_variable(out)
        op.outputs.append(total)
        total.source_op = op
        bias.value = torch.from_numpy(np.pad(bias.value.cpu().numpy().reshape(-1), (0, padded - n)))
        op.inputs.append(bias)
        bias.dest_ops.append(op)
    op.type = MATMUL_S8
    for name in [a for a in op.attributes if a != "quant_type"]:
        del op.attributes[name]
    op.attributes.update(
        shift=int(exponents[0] - exponents[1] - exponents[2]), relu=int(activation == "Relu"), columns=int(n)
    )
    op.config = OperationQuantizationConfig(configs, [out_config])
    return True


@contextlib.contextmanager
def matmul_s8() -> Iterator[None]:
    """Each int8 MatMul of a map's rows by constant weights exported as one MatMulS8 (KEHOACH 3.14 step 2.5), with the
    bias Add after it: esp-dl runs a 24-column product's first 16 columns through its unaligned kernel and the rest
    through C, then the Add through PSRAM. The op sums each row as a pointwise convolution, rounds as esp-dl's
    MatMul, then adds the bias saturating. ReDimNet2 b0 (KEHOACH 3.17) on board B: its TCMs' 36 projections took
    ~0.7 ms each, 2.25 cycles a multiply-add.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.operations.values()):
            if op.name in graph.operations:
                matmul_s8_from(graph, op, info)
        return graph

    EspdlExporter.prepare_graph = joining
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare


ATTENTION_CORE = "AttentionCore"
KEYS_PERM, ROWS_PERM, MERGE_PERM = [0, 2, 1, 3], [0, 2, 3, 1], [0, 3, 1, 2]


def read_once(graph, var) -> bool:
    return var.name not in graph.outputs and len(var.dest_ops) == 1


def head_split(graph, var, perm: list[int], info) -> tuple | None:
    """(source, ops, heads) when var is Reshape(Transpose(Reshape(source), perm)) of an int8 [1, T, H * D] source cut
    into heads [1, T, H, D], each variable after source read by the next op alone, on source's exponent."""
    last = var.source_op
    if last is None or last.type != "Reshape" or not read_once(graph, var):
        return None
    moved = last.inputs[0]
    turn = moved.source_op
    if turn is None or turn.type != "Transpose" or turn.attributes.get("perm") != perm or not read_once(graph, moved):
        return None
    cut = turn.inputs[0]
    first = cut.source_op
    if first is None or first.type != "Reshape" or not read_once(graph, cut):
        return None
    source = first.inputs[0]
    shape, heads = laid_shape(source, info), laid_shape(cut, info)
    if len(shape) != 3 or shape[0] != 1 or len(heads) != 4 or heads[:2] != shape[:2] or heads[2] * heads[3] != shape[2]:
        return None
    if exponent_of(source, info) is None or exponent_of(var, info) != exponent_of(source, info):
        return None
    return source, [first, turn, last], heads[2]


def head_merge(graph, var, info) -> tuple | None:
    """(out, ops) when var, the heads [H, D, T], goes through Reshape, Transpose MERGE_PERM and Reshape into out
    [1, T, H * D] on var's exponent, each variable on the way read by the next op alone; None otherwise."""
    ops, at = [], var
    for kind in ("Reshape", "Transpose", "Reshape"):
        if not read_once(graph, at):
            return None
        (op,) = at.dest_ops
        if op.type != kind or (kind == "Transpose" and op.attributes.get("perm") != MERGE_PERM):
            return None
        ops.append(op)
        at = op.outputs[0]
    shape = laid_shape(at, info)
    if len(shape) != 3 or shape[0] != 1 or exponent_of(at, info) != exponent_of(var, info):
        return None
    return at, ops


def int8_product(op) -> bool:
    """op is an int8 MatMul of two maps on one grid each."""
    from esp_ppq.IR.quantize import QuantableOperation

    return (
        op is not None
        and op.type == "MatMul"
        and isinstance(op, QuantableOperation)
        and op.attributes.get("activation", "Linear") == "Linear"
        and not any(v.is_parameter for v in op.inputs)
        and all(int8_tensor(c) for c in [*op.input_quant_config, *op.output_quant_config])
    )


def attention_core_from(graph, softmax, info) -> bool:
    """softmax, a SoftmaxQuant over axis 1 of the scores of keys [H, T, D] by queries [H, D, T], each cut into heads
    from its projection by head_split, read by one product of values [H, D, T] whose heads head_merge lays back,
    replaced by one AttentionCore reading the three projections and writing the merged heads; each product's shift
    1 or more. The ops and middle maps go."""
    from esp_ppq.core import OperationQuantizationConfig

    if softmax.type != SOFTMAX_QUANT or softmax.attributes.get("axis") != 1 or not read_once(graph, softmax.outputs[0]):
        return False
    scores, probs = softmax.inputs[0], softmax.outputs[0]
    product, (weigh,) = scores.source_op, probs.dest_ops
    if (
        not int8_product(product)
        or not int8_product(weigh)
        or not read_once(graph, scores)
        or weigh.inputs[1] is not probs
    ):
        return False
    found = [
        head_split(graph, product.inputs[0], KEYS_PERM, info),
        head_split(graph, product.inputs[1], ROWS_PERM, info),
        head_split(graph, weigh.inputs[0], ROWS_PERM, info),
    ]
    merged = head_merge(graph, weigh.outputs[0], info)
    if None in found or merged is None or len({f[2] for f in found}) != 1:
        return False
    sources = [f[0] for f in found]
    if len({tuple(laid_shape(v, info)) for v in sources}) != 1 or laid_shape(merged[0], info) != laid_shape(
        sources[0], info
    ):
        return False
    e = {name: exponent_of(v, info) for name, v in (("k", sources[0]), ("q", sources[1]), ("v", sources[2]))}
    e.update(s=exponent_of(scores, info), p=exponent_of(probs, info), o=exponent_of(weigh.outputs[0], info))
    if None in e.values() or e["s"] - e["k"] - e["q"] < 1 or e["o"] - e["v"] - e["p"] < 1:
        return False
    out, extra = merged[0], softmax.inputs[1:]
    configs = [*product.input_quant_config, weigh.input_quant_config[0], *softmax.input_quant_config[1:]]
    out_config = merged[1][-1].output_quant_config[0]
    gone = [product, weigh, *(op for f in found for op in f[1]), *merged[1]]
    middles = [v for op in gone for v in op.outputs if v is not out] + [probs]
    for v in softmax.inputs:
        v.dest_ops.remove(softmax)
    softmax.inputs.clear()
    probs.source_op = None
    softmax.outputs.clear()
    for op in gone:
        graph.remove_operation(op)
    for v in middles:
        graph.remove_variable(v)
    for v in [*sources, *extra]:
        softmax.inputs.append(v)
        v.dest_ops.append(softmax)
    softmax.outputs.append(out)
    out.source_op = softmax
    softmax.type = ATTENTION_CORE
    del softmax.attributes["axis"]
    softmax.attributes.update(
        heads=int(found[0][2]),
        score_shift=int(e["s"] - e["k"] - e["q"]),
        value_shift=int(e["o"] - e["v"] - e["p"]),
        score_exponent=int(e["s"]),
        prob_exponent=int(e["p"]),
    )
    softmax.config = OperationQuantizationConfig(configs, [out_config])
    return True


@contextlib.contextmanager
def attention_core() -> Iterator[None]:
    """Each attention esp-dl runs as head splits, a score MatMul, a softmax over axis 1, a weighing MatMul and a head
    merge exported as one AttentionCore (KEHOACH 3.14 step 2.5): every step wrote a whole map through PSRAM. The op
    lays each head once and runs both products as pointwise rows, 16 query frames at a time, each block's softmax as
    SoftmaxQuant does. Run after softmax_quant and softmax_scale. ReDimNet2 b0 (KEHOACH 3.17) on board B: each
    attention at 148 frames took ~17 ms.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.operations.values()):
            if op.name in graph.operations:
                attention_core_from(graph, op, info)
        return graph

    EspdlExporter.prepare_graph = joining
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare


STEM_CONV = "StemConv"
WORD_BYTES = 4


def stem_conv_from(graph, conv, info) -> bool:
    """conv, an int8 depthwise 3x3 convolution with bias keeping a [1, T, F, C] map's size, C a multiple of 4 up to
    16, of an Add alone reading a map [1, T, F, 1] and int8 zeros [1, 1, 1, C] on the map's exponent, as one StemConv
    reading the map: taps [9][16] and bias [16], C padded with zeros; its shift 1 or more. The Add and its copies go."""
    import torch
    from esp_ppq.core import OperationQuantizationConfig

    if not block_conv(conv, 3, True, True, "Linear", info):
        return False
    copies, c = conv.inputs[0], laid_shape(conv.inputs[0], info)[-1]
    add = copies.source_op
    if c % WORD_BYTES or c > CHANNEL_STEP or add is None or add.type != "Add" or not read_once(graph, copies):
        return False
    zeros = [v for v in add.inputs if v.is_parameter and v.value is not None]
    maps = [v for v in add.inputs if not v.is_parameter]
    if len(zeros) != 1 or len(maps) != 1 or bool(zeros[0].value.any()) or len(add.inputs) != 2:
        return False
    x, w, b = maps[0], conv.inputs[1], conv.inputs[2]
    if laid_shape(x, info) != [*laid_shape(copies, info)[:-1], 1] or exponent_of(x, info) != exponent_of(copies, info):
        return False
    shifts = block_shifts(x, [conv], 0, info)
    values = block_values(conv, c, info)
    if shifts is None or shifts[0] < 1 or values is None:
        return False
    configs = [add.input_quant_config[add.inputs.index(x)], *conv.input_quant_config[1:]]
    for v, value in zip((w, b), values, strict=True):
        v.value = torch.from_numpy(value).to(v.value.dtype)
    copies.dest_ops.remove(conv)
    graph.remove_operation(add)
    graph.remove_variable(copies)
    conv.inputs[0] = x
    x.dest_ops.append(conv)
    conv.type = STEM_CONV
    for name in [a for a in conv.attributes if a != "quant_type"]:
        del conv.attributes[name]
    conv.attributes.update(shift=int(shifts[0]), channels=int(c))
    conv.config = OperationQuantizationConfig(configs, conv.output_quant_config)
    return True


@contextlib.contextmanager
def stem_conv() -> Iterator[None]:
    """b0's stem, the log-mel broadcast into 12 channels by an Add of zeros (copies_by_broadcast) and their depthwise
    3x3, exported as one StemConv (KEHOACH 3.14 step 2.5): the Add wrote 12 copies through PSRAM and esp-dl ran the
    12-channel depthwise unaligned, a pixel a call. The op spreads each byte across a vector and runs ConvBlock's
    depthwise kernel with the bias. ReDimNet2 b0 (KEHOACH 3.17) on board B: 7.5 ms for the Add, 12.0 ms for the Conv.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.operations.values()):
            if op.name in graph.operations:
                stem_conv_from(graph, op, info)
        return graph

    EspdlExporter.prepare_graph = joining
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare


STRIDE_DEPTHWISE = "StrideDepthwise"
STRIDES = (2, 4)


def stride_depthwise_from(op, info) -> bool:
    """op, an int8 depthwise convolution with bias of k frames at a stride of k, k in STRIDES, over a [1, T, F, C] map,
    C a multiple of 8 and its shift 1 or more, renamed StrideDepthwise: taps [Cp / 16][k][16] and bias [Cp], Cp
    being C padded with zeros to a multiple of 16."""
    import torch
    from esp_ppq.IR.quantize import QuantableOperation

    if op.type != "Conv" or not isinstance(op, QuantableOperation) or len(op.inputs) != 3:
        return False
    attrs, (x, w, b) = op.attributes, op.inputs
    k = attrs.get("kernel_shape", [0])[0]
    laid, out = laid_shape(x, info), laid_shape(op.outputs[0], info)
    if (
        k not in STRIDES
        or attrs.get("kernel_shape") != [k, 1]
        or attrs.get("strides") != [k, 1]
        or any(attrs.get("pads", []))
        or attrs.get("dilations", [1, 1]) != [1, 1]
        or attrs.get("activation", "Linear") != "Linear"
        or len(laid) != 4
        or laid[0] != 1
        or attrs.get("group", 1) != laid[-1]
        or laid[-1] % (CHANNEL_STEP // 2)
        or out != [1, laid[1] // k, laid[2], laid[3]]
        or not all(int8_tensor(c) for c in [*op.input_quant_config[:2], *op.output_quant_config])
    ):
        return False
    shifts = block_shifts(x, [op], 0, info)
    if shifts is None or shifts[0] < 1:
        return False
    c = laid[-1]
    cp = -(-c // CHANNEL_STEP) * CHANNEL_STEP
    taps = np.pad(depthwise_taps(w.value.cpu().numpy(), c, k), [(0, 0), (0, cp - c)])
    laid_taps = taps.reshape(k, cp // CHANNEL_STEP, CHANNEL_STEP).transpose(1, 0, 2)
    w.value = torch.from_numpy(np.ascontiguousarray(laid_taps)).to(w.value.dtype)
    b.value = torch.from_numpy(np.pad(b.value.cpu().numpy().reshape(-1), (0, cp - c))).to(b.value.dtype)
    op.type = STRIDE_DEPTHWISE
    for name in [a for a in op.attributes if a != "quant_type"]:
        del op.attributes[name]
    op.attributes.update(shift=int(shifts[0]), stride=int(k))
    return True


@contextlib.contextmanager
def stride_depthwise() -> Iterator[None]:
    """Each int8 depthwise convolution of 2 or 4 frames at that stride exported as StrideDepthwise (KEHOACH 3.14 step
    2.5): esp-dl runs a depthwise kernel not 3x3 a pixel a call, and 24 channels unaligned. The op sums each output
    vector's k taps in QACC over rows padded to 16-byte vectors. ReDimNet2 b0 (KEHOACH 3.17) on board B: stage 2's and
    stage 4's took 6.6 and 2.3 ms at 12.4 and 4.3 cycles a multiply-add.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def renaming(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in graph.operations.values():
            stride_depthwise_from(op, info)
        return graph

    EspdlExporter.prepare_graph = renaming
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare


WEIGHTED_STATS = "WeightedStats"
# Frames whose int8 products the op sums within int16.
MAX_SUMMED_FRAMES = 255


def int8_map_product(op) -> bool:
    """op is an int8 Mul of two maps, one grid a tensor each."""
    from esp_ppq.IR.quantize import QuantableOperation

    return (
        op is not None
        and op.type == "Mul"
        and isinstance(op, QuantableOperation)
        and len(op.inputs) == 2
        and not any(v.is_parameter for v in op.inputs)
        and all(int8_tensor(c) for c in [*op.input_quant_config, *op.output_quant_config])
    )


def time_sum(graph, op, info):
    """The Mul whose int8 product [1, T, C], C a multiple of 16 and T up to MAX_SUMMED_FRAMES, op alone reads and
    sums over T into [1, C] as an int8 ReduceSum; None otherwise."""
    from esp_ppq.IR.quantize import QuantableOperation

    if op.type != "ReduceSum" or not isinstance(op, QuantableOperation) or op.attributes.get("keepdims", 1) != 0:
        return None
    product, axes = op.inputs[0], op.inputs[1] if len(op.inputs) > 1 else None
    mul = product.source_op
    if not int8_map_product(mul) or not read_once(graph, product) or axes is None or axes.value is None:
        return None
    laid = laid_shape(product, info)
    if len(laid) != 3 or laid[0] != 1 or laid[1] > MAX_SUMMED_FRAMES or laid[2] % CHANNEL_STEP:
        return None
    if axes.value.reshape(-1).tolist() != [1] or laid_shape(op.outputs[0], info) != [1, laid[2]]:
        return None
    return mul


def weighted_stats_from(graph, op, info) -> bool:
    """op, an int8 ReduceSum over time of p * (x * x), and the one of p * x, both by time_sum, each product and the
    square of x read once, replaced by one WeightedStats reading p and x and writing both sums; the shifts of x * x,
    p * x and p * (x * x) 1 or more. The Muls, the other ReduceSum and their maps go."""
    from esp_ppq.core import OperationQuantizationConfig

    weigh_square = time_sum(graph, op, info)
    if weigh_square is None:
        return False
    squares = [
        (weights, square)
        for weights, square in (weigh_square.inputs, weigh_square.inputs[::-1])
        if int8_map_product(square.source_op)
        and square.source_op.inputs[0] is square.source_op.inputs[1]
        and read_once(graph, square)
    ]
    if len(squares) != 1:
        return False
    p, square = squares[0]
    squaring = square.source_op
    x = squaring.inputs[0]
    found = []
    for reader in p.dest_ops:
        if reader is weigh_square or not int8_map_product(reader) or set(reader.inputs) != {p, x}:
            continue
        product = reader.outputs[0]
        if read_once(graph, product) and time_sum(graph, product.dest_ops[0], info) is reader:
            found.append((product.dest_ops[0], reader))
    if len(found) != 1 or len(p.dest_ops) != 2 or laid_shape(p, info) != laid_shape(x, info):
        return False
    total, weigh = found[0]
    e = {k: exponent_of(v, info) for k, v in (("x", x), ("p", p), ("q", square), ("w", weigh.outputs[0]))}
    e.update(ws=exponent_of(weigh_square.outputs[0], info), s=exponent_of(total.outputs[0], info))
    e.update(sq=exponent_of(op.outputs[0], info))
    if None in e.values():
        return False
    shifts = [
        e["q"] - 2 * e["x"],
        e["w"] - e["p"] - e["x"],
        e["ws"] - e["p"] - e["q"],
        e["s"] - e["w"],
        e["sq"] - e["ws"],
    ]
    if min(shifts[:3]) < 1:
        return False
    sums, squares = total.outputs[0], op.outputs[0]
    configs = [weigh.input_quant_config[weigh.inputs.index(p)], weigh.input_quant_config[weigh.inputs.index(x)]]
    out_configs = [total.output_quant_config[0], op.output_quant_config[0]]
    gone = [squaring, weigh, weigh_square, total]
    middles = [square, weigh.outputs[0], weigh_square.outputs[0]]
    for v in (sums, squares):
        v.source_op.outputs.remove(v)
        v.source_op = None
    for unit in gone:
        graph.remove_operation(unit)
    for v in middles:
        graph.remove_variable(v)
    for v in list(op.inputs):
        graph.remove_variable(v)
    for v in (p, x):
        op.inputs.append(v)
        v.dest_ops.append(op)
    op.outputs.extend([sums, squares])
    for v in (sums, squares):
        v.source_op = op
    op.type = WEIGHTED_STATS
    for name in [a for a in op.attributes if a != "quant_type"]:
        del op.attributes[name]
    op.attributes["shifts"] = [int(s) for s in shifts]
    op.config = OperationQuantizationConfig(configs, out_configs)
    return True


@contextlib.contextmanager
def weighted_stats() -> Iterator[None]:
    """An attentive pool's sums over time of p * x and of p * (x * x) exported as one WeightedStats (KEHOACH 3.14
    step 2.5): esp-dl wrote x * x and both products through PSRAM, then summed each. The op reads p and x once a row,
    takes each product as esp-dl's int8 Mul rounds it and sums them in int16 per channel, then shifts each sum as
    ReduceSum does. ReDimNet2 b0 (KEHOACH 3.17) on board B: the pool's three Muls and two ReduceSums took ~18.5 ms.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.operations.values()):
            if op.name in graph.operations:
                weighted_stats_from(graph, op, info)
        return graph

    EspdlExporter.prepare_graph = joining
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare


GLOBAL_STATS = "GlobalStats"


def time_mean(op, info) -> bool:
    """op is an int8 ReduceMean over T of a [1, T, C] map into [1, 1, C], C a multiple of 16, T up to
    MAX_SUMMED_FRAMES."""
    from esp_ppq.IR.quantize import QuantableOperation

    if op is None or op.type != "ReduceMean" or not isinstance(op, QuantableOperation):
        return False
    axes = op.inputs[1] if len(op.inputs) > 1 else None
    laid = laid_shape(op.inputs[0], info)
    return (
        op.attributes.get("keepdims", 1) == 1
        and axes is not None
        and axes.value is not None
        and axes.value.reshape(-1).tolist() == [1]
        and len(laid) == 3
        and laid[0] == 1
        and laid[1] <= MAX_SUMMED_FRAMES
        and laid[2] % CHANNEL_STEP == 0
        and laid_shape(op.outputs[0], info) == [1, 1, laid[2]]
        and all(int8_tensor(c) for c in [op.input_quant_config[0], *op.output_quant_config])
    )


def global_stats_from(graph, op, info) -> bool:
    """op, a time_mean of the squares of x less its time_mean (requantised, or not, for the Sub alone), each square
    and difference read by the next op alone, replaced by one GlobalStats reading x and writing the mean and op's
    output; the Sub on one exponent throughout, the requantisation's shift 0 or more, the square's 1 or more."""
    from esp_ppq.core import OperationQuantizationConfig
    from esp_ppq.IR.quantize import QuantableOperation

    if not time_mean(op, info):
        return False
    square = op.inputs[0]
    squaring = square.source_op
    if not int8_map_product(squaring) or squaring.inputs[0] is not squaring.inputs[1] or not read_once(graph, square):
        return False
    centred = squaring.inputs[0]
    sub = centred.source_op
    if (
        sub is None
        or sub.type != "Sub"
        or not isinstance(sub, QuantableOperation)
        or set(centred.dest_ops) != {squaring}
    ):
        return False
    x, centre = sub.inputs
    requantiser = (
        centre.source_op if centre.source_op is not None and centre.source_op.type == "RequantizeLinear" else None
    )
    mean = requantiser.inputs[0] if requantiser is not None else centre
    if requantiser is not None and not read_once(graph, centre):
        return False
    if not time_mean(mean.source_op, info) or mean.source_op.inputs[0] is not x or centred.name in graph.outputs:
        return False
    e = {k: exponent_of(v, info) for k, v in (("x", x), ("c", centre), ("d", centred), ("m", mean), ("s", square))}
    if None in e.values() or not e["x"] == e["c"] == e["d"] or e["c"] < e["m"] or e["s"] - 2 * e["d"] < 1:
        return False
    averaging, total = mean.source_op, op.outputs[0]
    shifts = [int(e["c"] - e["m"]), int(e["s"] - 2 * e["d"])]
    configs, out_configs = (
        [averaging.input_quant_config[0]],
        [averaging.output_quant_config[0], op.output_quant_config[0]],
    )
    gone = [averaging, sub, squaring, *([requantiser] if requantiser is not None else [])]
    middles = [centred, square, *([centre] if requantiser is not None else [])]
    for v in (mean, total):
        v.source_op.outputs.remove(v)
        v.source_op = None
    for unit in gone:
        graph.remove_operation(unit)
    for v in middles:
        graph.remove_variable(v)
    for v in list(op.inputs):
        graph.remove_variable(v)
    op.inputs.append(x)
    x.dest_ops.append(op)
    op.outputs.extend([mean, total])
    for v in (mean, total):
        v.source_op = op
    op.type = GLOBAL_STATS
    for name in [a for a in op.attributes if a != "quant_type"]:
        del op.attributes[name]
    op.attributes.update(shifts=shifts, square_exponent=int(e["s"]))
    op.config = OperationQuantizationConfig(configs, out_configs)
    return True


@contextlib.contextmanager
def global_stats() -> Iterator[None]:
    """An attentive pool's mean of x over time and the mean of the squares of x less it exported as one GlobalStats
    (KEHOACH 3.14 step 2.5): esp-dl wrote the difference and its square through PSRAM, a [T, C] map each, between two
    ReduceMeans. The op sums x a row at a time, takes the mean, its requantisation, the difference and the square as
    esp-dl does each, and the means by ReduceMean's float steps. ReDimNet2 b0 (KEHOACH 3.17) on board B: ~14.5 ms.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.operations.values()):
            if op.name in graph.operations:
                global_stats_from(graph, op, info)
        return graph

    EspdlExporter.prepare_graph = joining
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare


POINTWISE_S8 = "PointwiseS8"
# Input channels PointwiseS8 holds a row of in registers; others it reads 16 a vector.
HELD_INPUTS = (24, 48)


def config_of(var):
    """var's quantisation config as the op making it gives it, or as one reading it takes it; None when no
    quantised op does."""
    from esp_ppq.IR.quantize import QuantableOperation

    if isinstance(var.source_op, QuantableOperation):
        return var.source_op.output_quant_config[var.source_op.outputs.index(var)]
    for reader in var.dest_ops:
        if isinstance(reader, QuantableOperation):
            return reader.input_quant_config[reader.inputs.index(var)]
    return None


def residual_add(graph, out, info) -> tuple | None:
    """(add, other, r, requantiser, shift) when out, read by one int8 Add alone on its exponent, is added to other, a
    map of out's laid shape on that exponent too: r itself, or r through a RequantizeLinear read by that Add alone,
    shifting r's integers right by shift; None otherwise."""
    from esp_ppq.IR.quantize import QuantableOperation

    if out.name in graph.outputs or len(out.dest_ops) != 1:
        return None
    (add,) = out.dest_ops
    if add.type != "Add" or not isinstance(add, QuantableOperation) or len(add.inputs) != 2:
        return None
    configs = [*add.input_quant_config, *add.output_quant_config]
    if add.inputs[0] is add.inputs[1] or not all(int8_tensor(c) for c in configs):
        return None
    other = add.inputs[1] if add.inputs[0] is out else add.inputs[0]
    total = exponent_of(add.outputs[0], info)
    if other.is_parameter or total is None or exponent_of(out, info) != total or exponent_of(other, info) != total:
        return None
    r, requantiser = other, other.source_op
    if (
        requantiser is not None
        and requantiser.type == "RequantizeLinear"
        and other.dest_ops == [add]
        and other.name not in graph.outputs
    ):
        r = requantiser.inputs[0]
    else:
        requantiser = None
    e_r = exponent_of(r, info)
    if e_r is None or e_r > total or laid_shape(r, info) != laid_shape(out, info) or config_of(r) is None:
        return None
    return add, other, r, requantiser, int(total - e_r)


def pointwise_takes(op, info) -> bool:
    """Whether op is a 1x1 convolution of one group whose channel counts PointwiseS8 runs as they are, so
    aligned_pointwise leaves its columns unmerged."""
    attrs = op.attributes
    if op.type != "Conv" or attrs.get("group", 1) != 1 or any(v != 1 for v in attrs.get("kernel_shape", [0])):
        return False
    k, n = laid_shape(op.inputs[0], info)[-1], laid_shape(op.outputs[0], info)[-1]
    return (k in HELD_INPUTS or k % CHANNEL_STEP == 0) and n % (CHANNEL_STEP // 2) == 0


def pointwise_s8_from(graph, op, info) -> bool:
    """op, an int8 1x1 Conv of K inputs to N outputs over a map's rows, K 24, 48 or a multiple of 16, N a multiple of
    8, its mac shift 1 or more, as one PointwiseS8: weights [Np / 16][K][16], bias [Np] when it has one, Np being N
    padded with zeros to a multiple of 16. With K held in registers, the residual Add after it joins too. A 2-d one
    whose kernel [kh, kw] is its stride reads non-overlapping patches, K = kh * kw * C a multiple of 16, as "patch"."""
    import torch
    from esp_ppq.core import OperationQuantizationConfig
    from esp_ppq.IR.quantize import QuantableOperation

    if op.type != "Conv" or not isinstance(op, QuantableOperation) or len(op.inputs) not in (2, 3):
        return False
    attrs, (x, w), y = op.attributes, op.inputs[:2], op.outputs[0]
    kernel = list(attrs.get("kernel_shape", []))
    patch = kernel if len(kernel) == 2 and list(attrs.get("strides", [])) == kernel else [1] * len(kernel)
    if not kernel or any(v != 1 for v in attrs.get("dilations", [])) or kernel != patch:
        return False
    if any(v != 1 for v in attrs.get("strides", [])) and math.prod(patch) == 1:
        return False
    if any(attrs.get("pads", [])) or attrs.get("group", 1) != 1 or attrs.get("activation", "Linear") != "Linear":
        return False
    laid_x, laid_y = laid_shape(x, info), laid_shape(y, info)
    k, n = laid_x[-1] * math.prod(patch), laid_y[-1]
    if math.prod(patch) > 1:
        # Each output pixel reads all the weights again: past half the data cache they stop staying there.
        if len(laid_x) != 4 or laid_x[0] != 1 or k % CHANNEL_STEP or k * n > DATA_CACHE_BYTES // 2:
            return False
        (kh, kw), (t, f) = patch, laid_x[1:3]
        if t % kh or f % kw or laid_y != [1, t // kh, f // kw, n]:
            return False
    elif laid_x[:-1] != laid_y[:-1] or not (k in HELD_INPUTS or k % CHANNEL_STEP == 0):
        return False
    if n % (CHANNEL_STEP // 2):
        return False
    if w.value is None or w.value.numel() != k * n:
        return False
    if not all(int8_tensor(c) for c in [*op.input_quant_config[:2], *op.output_quant_config]):
        return False
    biased = int(len(op.inputs) == 3)
    e_x, e_w, e_y = (exponent_of(v, info) for v in (x, w, y))
    if None in (e_x, e_w, e_y) or e_y - e_x - e_w < 1:
        return False
    if biased and exponent_of(op.inputs[2], info) != e_x + e_w:
        return False
    padded = -(-n // CHANNEL_STEP) * CHANNEL_STEP
    weights = np.pad(dense_filters(w.value.cpu().numpy(), n), [(0, padded - n), (0, 0)])
    w.value = torch.from_numpy(
        np.ascontiguousarray(weights.reshape(padded // CHANNEL_STEP, CHANNEL_STEP, k).transpose(0, 2, 1))
    )
    if biased:
        b = op.inputs[2]
        b.value = torch.from_numpy(np.pad(b.value.cpu().numpy().reshape(-1), (0, padded - n)))
    configs, out_config, residual_shift = list(op.input_quant_config), op.output_quant_config[0], -1
    found = residual_add(graph, y, info) if k in HELD_INPUTS and math.prod(patch) == 1 else None
    if found is not None:
        add, other, r, requantiser, residual_shift = found
        r_config = add.input_quant_config[add.inputs.index(other)] if requantiser is None else config_of(r)
        out_config, total = add.output_quant_config[0], add.outputs[0]
        add.inputs.remove(other)
        other.dest_ops.remove(add)
        graph.remove_operation(add)
        graph.remove_variable(y)
        op.outputs.append(total)
        total.source_op = op
        if requantiser is not None:
            graph.remove_operation(requantiser)
            graph.remove_variable(other)
        op.inputs.append(r)
        r.dest_ops.append(op)
        configs.append(r_config)
    op.type = POINTWISE_S8
    for name in [a for a in op.attributes if a != "quant_type"]:
        del op.attributes[name]
    op.attributes.update(
        shift=int(e_y - e_x - e_w),
        columns=int(n),
        biased=biased,
        residual_shift=int(residual_shift),
        patch=[int(v) for v in (patch if len(patch) == 2 else [1, 1])],
    )
    op.config = OperationQuantizationConfig(configs, [out_config])
    return True


def pointwise_concat(graph, concat, info) -> bool:
    """concat, joining along the channels PointwiseS8 nodes alone each read by it, over one input with one shift and
    bias or none, no residual nor patch, each giving a multiple of 16 outputs on concat's exponent, as the first of
    them giving concat's output: their weights and biases one after another, "columns" their sum."""
    import torch
    from esp_ppq.core import OperationQuantizationConfig

    if concat.type != "Concat" or len(concat.inputs) < 2:
        return False
    parts = [v.source_op for v in concat.inputs]
    out = concat.outputs[0]
    if any(p is None or p.type != POINTWISE_S8 for p in parts) or len(set(id(p) for p in parts)) != len(parts):
        return False
    laid = [laid_shape(v, info) for v in concat.inputs]
    lo = laid_shape(out, info)
    if any(v[:-1] != lo[:-1] for v in laid) or sum(v[-1] for v in laid) != lo[-1]:
        return False
    first = parts[0]
    keys = ("shift", "biased", "residual_shift", "patch")
    for p, v in zip(parts, concat.inputs, strict=True):
        if v.dest_ops != [concat] or v.name in graph.outputs or p.inputs[0] is not first.inputs[0]:
            return False
        if any(p.attributes.get(k) != first.attributes.get(k) for k in keys) or p.attributes["residual_shift"] != -1:
            return False
        if p.attributes["columns"] % CHANNEL_STEP or exponent_of(v, info) != exponent_of(out, info):
            return False
    if first.attributes["patch"] != [1, 1]:
        return False
    biased = first.attributes["biased"]
    weights = np.concatenate([p.inputs[1].value.cpu().numpy() for p in parts])
    first.inputs[1].value = torch.from_numpy(np.ascontiguousarray(weights)).to(first.inputs[1].value.dtype)
    if biased:
        bias = np.concatenate([p.inputs[2].value.cpu().numpy() for p in parts])
        first.inputs[2].value = torch.from_numpy(bias).to(first.inputs[2].value.dtype)
    out_config, middles = concat.output_quant_config[0], [v for v in concat.inputs]
    for p in parts[1:]:
        p.inputs[0].dest_ops.remove(p)
        del p.inputs[0]
    graph.remove_operation(concat)
    for p in parts[1:]:
        graph.remove_operation(p)
    for v in middles:
        graph.remove_variable(v)
    first.outputs.append(out)
    out.source_op = first
    first.attributes["columns"] = int(sum(p.attributes["columns"] for p in parts))
    first.config = OperationQuantizationConfig(list(first.config.input_quantization_config), [out_config])
    return True


@contextlib.contextmanager
def pointwise_s8() -> Iterator[None]:
    """Each int8 1x1 convolution exported as one PointwiseS8 (KEHOACH 3.14 step 2.5), the residual Add after it with
    it when its rows are 24 or 48 values: esp-dl ran them at 0.13 to 0.45 cycles a multiply-add, 24 channels through
    its unaligned kernel or aligned_pointwise's merged columns, and the Add through PSRAM. The op sums each row as a
    pointwise convolution, rounds as esp-dl's Conv and adds the residual saturating as its Add. ReDimNet2 b0 (KEHOACH
    3.17) on board B: ~80 ms of 1x1 convolutions and ~14 ms of residual Adds.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    global column_merge
    prepare, merge = EspdlExporter.prepare_graph, column_merge

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.operations.values()):
            if op.name in graph.operations:
                pointwise_s8_from(graph, op, info)
        for op in list(graph.operations.values()):
            if op.name in graph.operations:
                pointwise_concat(graph, op, info)
        return graph

    def unless_taken(op, info):
        return None if pointwise_takes(op, info) else merge(op, info)

    EspdlExporter.prepare_graph, column_merge = joining, unless_taken
    try:
        yield
    finally:
        EspdlExporter.prepare_graph, column_merge = prepare, merge


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
    "softmax_quant": softmax_quant,
    "softmax_scale": softmax_scale,
    "layernorm_s8": layernorm_s8,
    "stage_sum": stage_sum,
    "conv_block": conv_block,
    "matmul_s8": matmul_s8,
    "attention_core": attention_core,
    "stem_conv": stem_conv,
    "stride_depthwise": stride_depthwise,
    "weighted_stats": weighted_stats,
    "global_stats": global_stats,
    "pointwise_s8": pointwise_s8,
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
