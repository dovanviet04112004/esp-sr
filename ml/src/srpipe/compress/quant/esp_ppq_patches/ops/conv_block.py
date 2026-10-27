"""ConvBlock (espdl_conv_block.cpp): each residual conv block with the 1x1 convolutions on either side as one op;
its helpers lay convolution weights and shifts as esp-dl stores them."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import numpy as np

from ..base import CHANNEL_STEP, behind_reshapes, exponent_of, int8_tensor, laid_shape

CONV_BLOCK = "ConvBlock"
# (kernel, depthwise, bias, activation) of a ReDimNet2 conv block's convolutions from its input on.
BLOCK_STEPS = (
    (3, True, False, "Linear"),
    (1, False, True, "Relu"),
    (1, True, True, "Linear"),
    (3, True, False, "Linear"),
    (1, False, True, "Linear"),
)


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
