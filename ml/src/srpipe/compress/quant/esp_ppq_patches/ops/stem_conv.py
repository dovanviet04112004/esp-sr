"""StemConv (espdl_stem_conv.cpp): the stem's broadcast copies and their depthwise as one op."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import numpy as np

from ..base import CHANNEL_STEP, WORD_BYTES, exponent_of, laid_shape, read_once
from .conv_block import block_conv, block_shifts, block_values
from .layernorm_s8 import LAYERNORM_S8

STEM_CONV = "StemConv"


def stem_conv_from(graph, conv, info) -> bool:
    """conv, an int8 depthwise 3x3 convolution with bias keeping a [1, T, F, C] map's size, C a multiple of 4, of an
    Add alone reading a map [1, T, F, 1] and int8 zeros [1, 1, 1, C] on the map's exponent, as one StemConv reading
    the map: taps [V * 9][16] (V = ceil(C / 16), each 16 channels' nine taps in turn) and bias [V * 16], C padded
    with zeros; its shift 1 or more. The Add and its copies go."""
    import torch
    from esp_ppq.core import OperationQuantizationConfig

    if not block_conv(conv, 3, True, True, "Linear", info):
        return False
    copies, c = conv.inputs[0], laid_shape(conv.inputs[0], info)[-1]
    add = copies.source_op
    if c % WORD_BYTES or add is None or add.type != "Add" or not read_once(graph, copies):
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
    taps, bias = values
    vectors = taps.shape[1] // CHANNEL_STEP
    taps = taps.reshape(9, vectors, CHANNEL_STEP).transpose(1, 0, 2).reshape(vectors * 9, CHANNEL_STEP)
    for v, value in zip((w, b), (np.ascontiguousarray(taps), bias), strict=True):
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


def layernorm_into(graph, stem, info) -> bool:
    """stem, a StemConv whose output a LayerNormS8 over its channels alone reads, joined with it: the norm's gamma and
    beta become its inputs after the bias, its epsilon and the convolution's own exponent its attributes."""
    from esp_ppq.core import OperationQuantizationConfig

    y = stem.outputs[0]
    if y.name in graph.outputs or len(y.dest_ops) != 1:
        return False
    norm = y.dest_ops[0]
    if norm.type != LAYERNORM_S8 or norm.inputs[0] is not y or len(norm.inputs) < 2 or len(norm.outputs) != 1:
        return False
    e_y = exponent_of(y, info)
    if e_y is None:
        return False
    out, params = norm.outputs[0], list(norm.inputs[1:])
    configs = [*stem.input_quant_config, *norm.input_quant_config[1:]]
    out_config, epsilon = norm.output_quant_config[0], float(norm.attributes.get("epsilon", 1e-5))
    for v in params:
        norm.inputs.remove(v)
        v.dest_ops.remove(norm)
    graph.remove_operation(norm)
    graph.remove_variable(y)
    for v in params:
        stem.inputs.append(v)
        v.dest_ops.append(stem)
    stem.outputs.append(out)
    out.source_op = stem
    stem.attributes.update(normed=1, epsilon=epsilon, conv_exponent=int(e_y))
    stem.config = OperationQuantizationConfig(configs, [out_config])
    return True


@contextlib.contextmanager
def stem_conv() -> Iterator[None]:
    """ReDimNet2's stem, the log-mel broadcast into C channels by an Add of zeros (copies_by_broadcast) and their
    depthwise 3x3, exported as one StemConv (KEHOACH 3.14 step 2.5): the Add wrote C copies through PSRAM and esp-dl
    ran the depthwise apart. The op spreads each byte across a vector and runs ConvBlock's depthwise kernel with the
    bias, one pass per 16 channels, then the LayerNormS8 reading it a row at a time. On board B: b0 (12 channels) 7.5
    ms for the Add, 12.0 ms for the Conv; b3 (24) 5.8 and 16.9 ms.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.operations.values()):
            if op.name in graph.operations and stem_conv_from(graph, op, info):
                layernorm_into(graph, op, info)
        return graph

    EspdlExporter.prepare_graph = joining
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare
