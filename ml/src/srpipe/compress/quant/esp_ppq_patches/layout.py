"""Export patches that keep esp-dl's own ops: fewer Transposes and quantisers, merged columns, cache-sized slices,
channel scales, dropped identities, broadcast copies, positive axes, channels padded to the vector step."""

from __future__ import annotations

import contextlib
import math
from collections.abc import Iterator

import numpy as np

from .base import CHANNEL_STEP, DATA_CACHE_BYTES, ELEMENTWISE_OPS, REDUCE_OPS, laid_shape, linked


def chain_as_one(shape: list[int], first: list[int], target: list[int], second: list[int]):
    """(q, out) such that transposing an array of shape by q and reshaping it to out lays its values as transposing it
    by first, reshaping to target and transposing by second does; q the identity when the chain moves nothing, None
    when no single transpose does it."""
    import itertools

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


def turn_axes_positive(graph) -> int:
    """Each Unsqueeze's negative axes as the output axes they name, ONNX's reading, on its own copy of axes another op
    also reads; how many."""
    done = 0
    for op in [op for op in graph.topological_sort() if op.type == "Unsqueeze"]:
        axes = op.inputs[1]
        rank = len(op.inputs[0].shape) + axes.value.numel()
        named = axes.value.where(axes.value >= 0, axes.value + rank)
        if named.equal(axes.value):
            continue
        if len(axes.dest_ops) > 1:
            axes.dest_ops.remove(op)
            own = graph.create_variable(value=named, is_parameter=True)
            own.dtype, own.dest_ops[:] = axes.dtype, [op]
            op.inputs[1] = own
        else:
            axes.value = named
        done += 1
    return done


@contextlib.contextmanager
def unsqueeze_axes_positive() -> Iterator[None]:
    """esp-dl's Unsqueeze turns a negative axis positive inside its own parameter tensor while the model plans its
    memory, and a graph left in flash keeps that tensor in flash, read only. command_ctc (ADR-0028) on board B: its
    stacks' axis -1 panicked the build, "Dbus write to cache rejected". After the export's passes this writes each such
    axis as the output axis it names, the same integers.
    """
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def positive(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        turn_axes_positive(graph)
        return graph

    EspdlExporter.prepare_graph = positive
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare


def padded_size(channels: int) -> int | None:
    """The multiple of CHANNEL_STEP channels pads up to when that adds at most a third of them; None when channels
    is one already or would grow more."""
    up = -(-channels // CHANNEL_STEP) * CHANNEL_STEP
    return up if up != channels and 3 * up <= 4 * channels else None


def keeps_channels(op, info) -> bool:
    """op passes every channel of its tensor inputs to its output as it finds it, zero staying zero: a Relu, an Add
    of two tensors, a RequantizeLinear on one grid about zero, or a Slice along other axes."""
    tensors = [v for v in op.inputs if not v.is_parameter]
    if op.type == "Relu":
        return len(tensors) == 1
    if op.type == "Add":
        return len(tensors) == 2 == len(op.inputs)
    if op.type == "RequantizeLinear":
        zero = op.inputs[2:3]
        return len(tensors) == 1 and op.inputs[1].value.numel() == 1 and all(not v.value.any() for v in zero)
    if op.type == "Slice" and len(op.inputs) > 3 and op.inputs[3].is_parameter:
        rank = len(laid_shape(op.inputs[0], info))
        return len(tensors) == 1 and all(int(a) % rank != rank - 1 for a in op.inputs[3].value.tolist())
    return False


def writes_zero_for_zero(op) -> bool:
    """op is an ungrouped convolution whose zero weights and bias give zero, its activation none or a Relu."""
    return (
        op.type == "Conv"
        and op.attributes.get("group", 1) == 1
        and op.attributes.get("activation", "Linear") in ("Linear", "Relu")
    )


def channel_region(graph, start, info) -> tuple[set[str], set[str], set[str]] | None:
    """(variables, writers, readers) around start, a convolution's output: every variable joined to it through ops
    that keep_channels, the convolutions writing one of them and the ungrouped ones reading one as their input; None
    when another op writes or reads one, one is a graph output, or one is not laid channels last."""
    region, writers, readers, todo = set(), set(), set(), [start]
    while todo:
        var = todo.pop()
        if var.name in region:
            continue
        perm = info.get_var_permute(var.name)
        if var.name in graph.outputs or var.source_op is None or not perm or perm[-1] != 1:
            return None
        region.add(var.name)
        source = var.source_op
        if writes_zero_for_zero(source):
            writers.add(source.name)
        elif keeps_channels(source, info):
            todo += [v for v in source.inputs if not v.is_parameter]
        else:
            return None
        for op in var.dest_ops:
            if op.type == "Conv" and op.inputs[0] is var and op.attributes.get("group", 1) == 1:
                readers.add(op.name)
            elif keeps_channels(op, info):
                todo += [v for v in op.inputs if not v.is_parameter] + list(op.outputs)
            else:
                return None
    return region, writers, readers


def zero_padded(value, axis: int, size: int):
    """value with zeros appended along axis up to size."""
    import torch

    shape = list(value.shape)
    shape[axis] = size - shape[axis]
    return torch.cat([value, torch.zeros(shape, dtype=value.dtype)], dim=axis)


def pad_parameter(op, index: int, axis: int, size: int) -> None:
    """op's parameter input index padded with zeros along axis up to size, its per-channel grid along with it when
    it pads the outputs."""
    param, config = op.inputs[index], op.input_quant_config[index]
    if len(param.dest_ops) != 1:
        raise ValueError(f"{op.name}: {param.name} is shared, so padding it would change another op")
    param.value = zero_padded(param.value, axis, size)
    if axis == 0 and config.scale is not None and config.scale.dim() > 0:
        extra = size - config.scale.numel()
        config.scale = edge_repeated(config.scale, extra)
        config.offset = edge_repeated(config.offset, extra)


def edge_repeated(values, extra: int):
    """values with its last entry repeated extra times."""
    import torch

    return torch.cat([values, values[-1:].repeat(extra)])


def pad_channels(graph) -> int:
    """Each channel_region whose channels padded_size pads, padded: its writers' weights and bias and its readers'
    weights with zero channels, its variables wider by as many. The zero channels stay zero through every op of the
    region, so the other channels keep their integers; how many regions."""
    from esp_ppq.IR.quantize import QuantableOperation
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo

    info, seen, done = ExporterPatternInfo(), set(), 0
    for op in list(graph.topological_sort()):
        if op.type != "Conv" or not isinstance(op, QuantableOperation) or op.outputs[0].name in seen:
            continue
        found = channel_region(graph, op.outputs[0], info)
        if found is None:
            continue
        region, writers, readers = found
        seen |= region
        channels = laid_shape(op.outputs[0], info)[-1]
        size = padded_size(channels)
        if size is None:
            continue
        for name in writers:
            writer = graph.operations[name]
            for index in range(1, len(writer.inputs)):
                pad_parameter(writer, index, 0, size)
        for name in readers:
            pad_parameter(graph.operations[name], 1, 1, size)
        for name in region:
            var = graph.variables[name]
            var.shape = [size if i == 1 else d for i, d in enumerate(var.shape)]
        done += 1
    return done


@contextlib.contextmanager
def aligned_channels() -> Iterator[None]:
    """esp-dl's S3 convolution kernels take their vector path only when both channel counts are multiples of 16.
    After the export's layout pass this pads each run of tensors of 12 or 24 channels, joined by Relu, Add,
    RequantizeLinear and Slice between ungrouped convolutions, to the next multiple with zero channels (pad_channels):
    the same integers in the real channels, a third more multiply-adds at the vector rate. SwiftF0 (KEHOACH 3.11) on
    board B: its 12-channel convolutions ran at 2.3 to 3.6 cycles a multiply-add, 96% of 6.74 s a window.
    """
    from esp_ppq.parser import espdl_exporter

    reset = espdl_exporter.reset_graph_layout

    def padded(graph):
        reset(graph)
        pad_channels(graph)

    espdl_exporter.reset_graph_layout = padded
    try:
        yield
    finally:
        espdl_exporter.reset_graph_layout = reset
