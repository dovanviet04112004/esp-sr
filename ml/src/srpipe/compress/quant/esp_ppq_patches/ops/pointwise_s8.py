"""PointwiseS8 (espdl_pointwise_s8.cpp): other 1x1 convolutions with their residual Adds, stride convolutions read
as patches and Concats of slices, each as one op."""

from __future__ import annotations

import contextlib
import math
from collections.abc import Iterator

import numpy as np

from .. import layout
from ..base import CHANNEL_STEP, DATA_CACHE_BYTES, config_of, exponent_of, int8_tensor, laid_shape
from .conv_block import dense_filters

POINTWISE_S8 = "PointwiseS8"
# Input channels PointwiseS8 holds a row of in registers; others it reads 16 a vector.
HELD_INPUTS = (24, 48)


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


def odd_rows(laid_x: list[int]) -> bool:
    """Whether laid_x is a 1-d map [1, T, K] of K a multiple of 8; when K is no multiple of 16, PointwiseS8 copies its
    rows to rows padded to 16 before its kernel reads them."""
    return len(laid_x) == 3 and laid_x[0] == 1 and laid_x[-1] % (CHANNEL_STEP // 2) == 0


def pointwise_s8_from(graph, op, info) -> bool:
    """op, an int8 1x1 Conv of K inputs to N outputs over a map's rows, K 24, 48 or a multiple of 16, or of 8 over a
    1-d map [1, T, K], N a multiple of 8, its mac shift 1 or more, as one PointwiseS8: weights [Np / 16][K][16], bias
    [Np] when it has one, Np being N padded with zeros to a multiple of 16. With K held in registers, the residual Add
    after it joins too. A 2-d one whose kernel [kh, kw] is its stride reads non-overlapping patches, K = kh * kw * C a
    multiple of 16, as "patch"."""
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
        # Weights within half the data cache stay there; past all of it the op runs them in slices that do.
        if len(laid_x) != 4 or laid_x[0] != 1 or k % CHANNEL_STEP or DATA_CACHE_BYTES // 2 < k * n <= DATA_CACHE_BYTES:
            return False
        (kh, kw), (t, f) = patch, laid_x[1:3]
        if t % kh or f % kw or laid_y != [1, t // kh, f // kw, n]:
            return False
    elif laid_x[:-1] != laid_y[:-1] or not (k in HELD_INPUTS or k % CHANNEL_STEP == 0 or odd_rows(laid_x)):
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
    joined = join_residual(graph, op, y, configs, info) if math.prod(patch) == 1 else None
    if joined is not None:
        residual_shift, out_config = joined
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


def join_residual(graph, op, y, configs: list, info) -> tuple | None:
    """The residual Add alone reading y, op's output, joined into op: the map it adds, or that map before a
    RequantizeLinear only the Add reads, appended to op's inputs and its config to configs, the Add's output becoming
    op's; (the residual's shift, the Add's output config), None when no such Add follows."""
    found = residual_add(graph, y, info)
    if found is None:
        return None
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
    return residual_shift, out_config


def concat_residual(graph, op, info) -> bool:
    """The residual Add after op, a PointwiseS8 pointwise_concat joined from slices, joined into it too."""
    from esp_ppq.core import OperationQuantizationConfig

    if op.type != POINTWISE_S8 or op.attributes.get("residual_shift") != -1 or op.attributes.get("patch") != [1, 1]:
        return False
    configs = list(op.config.input_quantization_config)
    joined = join_residual(graph, op, op.outputs[0], configs, info)
    if joined is None:
        return False
    op.attributes["residual_shift"] = int(joined[0])
    op.config = OperationQuantizationConfig(configs, [joined[1]])
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
    it: esp-dl ran them at 0.13 to 0.45 cycles a multiply-add, 24 channels through
    its unaligned kernel or aligned_pointwise's merged columns, and the Add through PSRAM. The op sums each row as a
    pointwise convolution, rounds as esp-dl's Conv and adds the residual saturating as its Add. ReDimNet2 b0 (KEHOACH
    3.17) on board B: ~80 ms of 1x1 convolutions and ~14 ms of residual Adds.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare, merge = EspdlExporter.prepare_graph, layout.column_merge

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.operations.values()):
            if op.name in graph.operations:
                pointwise_s8_from(graph, op, info)
        for op in list(graph.operations.values()):
            if op.name in graph.operations and op.type == "Concat":
                out = op.outputs[0]
                if pointwise_concat(graph, op, info):
                    concat_residual(graph, out.source_op, info)
        return graph

    def unless_taken(op, info):
        return None if pointwise_takes(op, info) else merge(op, info)

    EspdlExporter.prepare_graph, layout.column_merge = joining, unless_taken
    try:
        yield
    finally:
        EspdlExporter.prepare_graph, layout.column_merge = prepare, merge
