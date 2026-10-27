"""StemConv (espdl_stem_conv.cpp): the stem's broadcast copies and their depthwise as one op."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from ..base import CHANNEL_STEP, WORD_BYTES, exponent_of, laid_shape, read_once
from .conv_block import block_conv, block_shifts, block_values

STEM_CONV = "StemConv"


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
