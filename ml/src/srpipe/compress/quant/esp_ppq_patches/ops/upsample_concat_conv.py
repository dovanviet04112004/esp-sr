"""UpsampleConcatConv (espdl_upsample_concat_conv.cpp): a ConvLut 1x1 over the Concat of a map upsampled twice by
nearest neighbour and another map, as one op that builds the Concat a row at a time in the cache."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from ..base import exponent_of, laid_shape, read_once

UPSAMPLE_CONCAT_CONV = "UpsampleConcatConv"
CONV_LUT = "ConvLut"


def doubled(resize, info) -> bool:
    """resize repeats each row and column of its int8 [1, H, W, C] input twice, as a nearest, asymmetric, floor Resize
    on one exponent does."""
    if resize is None or resize.type != "Resize":
        return False
    a = resize.attributes
    if (a.get("mode"), a.get("coordinate_transformation_mode"), a.get("nearest_mode")) != (
        "nearest",
        "asymmetric",
        "floor",
    ):
        return False
    src, out = laid_shape(resize.inputs[0], info), laid_shape(resize.outputs[0], info)
    if len(src) != 4 or len(out) != 4 or src[0] != 1 or out[0] != 1 or src[3] != out[3]:
        return False
    same = exponent_of(resize.inputs[0], info) == exponent_of(resize.outputs[0], info) is not None
    return same and out[1] == 2 * src[1] and out[2] == 2 * src[2]


def upsample_concat_parts(graph, conv, info) -> tuple | None:
    """(resize, concat, up_first) when conv, an int8 ConvLut 1x1 of one group and stride 1 with a table, reads alone a
    channel Concat of two maps on its exponent, one a doubled Resize that only the Concat reads; None otherwise."""
    if conv.type != CONV_LUT or "lut" not in conv.attributes or conv.attributes.get("quant_type") != "S8":
        return None
    a = conv.attributes
    if list(a.get("kernel_shape", [])) != [1, 1] or a.get("group", 1) != 1 or list(a.get("strides", [1, 1])) != [1, 1]:
        return None
    cat_out = conv.inputs[0]
    concat = cat_out.source_op
    if concat is None or concat.type != "Concat" or len(concat.inputs) != 2 or not read_once(graph, cat_out):
        return None
    shared = exponent_of(cat_out, info)
    if shared is None or any(exponent_of(v, info) != shared for v in concat.inputs):
        return None
    for k, v in enumerate(concat.inputs):
        resize = v.source_op
        other = concat.inputs[1 - k]
        if doubled(resize, info) and read_once(graph, v) and laid_shape(other, info)[:3] == laid_shape(v, info)[:3]:
            return resize, concat, k == 0
    return None


def upsample_concat_conv_from(graph, conv, info) -> bool:
    """conv and the upsampled Concat before it as one UpsampleConcatConv reading the Resize's input, the other map,
    then conv's filter and bias; attributes conv's own and up_first, the Resize and Concat and their maps gone."""
    from esp_ppq.core import OperationQuantizationConfig

    found = upsample_concat_parts(graph, conv, info)
    if found is None:
        return False
    resize, concat, up_first = found
    small = resize.inputs[0]
    other = concat.inputs[1] if up_first else concat.inputs[0]
    configs = [
        resize.input_quant_config[0],
        concat.input_quant_config[1 if up_first else 0],
        *conv.input_quant_config[1:],
    ]
    params = conv.inputs[1:]
    middles = [resize.outputs[0], concat.outputs[0]]
    for op in (concat, resize):
        graph.remove_operation(op)
    for v in middles:
        if v.name in graph.variables:
            graph.remove_variable(v)
    conv.inputs.clear()
    for v in [small, other, *params]:
        conv.inputs.append(v)
        if conv not in v.dest_ops:
            v.dest_ops.append(conv)
    conv.type = UPSAMPLE_CONCAT_CONV
    conv.attributes["up_first"] = int(up_first)
    conv.config = OperationQuantizationConfig(configs, conv.output_quant_config)
    return True


@contextlib.contextmanager
def upsample_concat_conv() -> Iterator[None]:
    """Each ConvLut 1x1 over the Concat of a nearest Resize by 2 and another map on one exponent exported as one
    UpsampleConcatConv (ADR-0037, KEHOACH 3.18): the Resize and the Concat each write their map to PSRAM for the
    convolution to read back. Run after conv_lut. YOLO26n's neck on board B: model.14's Resize and model.15's
    Concat took ~43 ms of a 1.89 s picture.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joined(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.topological_sort()):
            if op.name in graph.operations:
                upsample_concat_conv_from(graph, op, info)
        return graph

    EspdlExporter.prepare_graph = joined
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare
