"""ConvLut (espdl_conv_lut.cpp): a convolution and the table activation alone reading it, as one op (ADR-0031)."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from ..base import exponent_of

CONV_LUT = "ConvLut"


def conv_lut_from(graph, op, info) -> bool:
    """op, a Conv with no activation of its own whose output, no graph output, only an activation with a table on op's
    quant type reads, joined with it as one ConvLut: op's inputs and attributes, the activation's "lut" and output, and
    "conv_exponent" the exponent op's own output had."""
    from esp_ppq.core import OperationQuantizationConfig
    from esp_ppq.IR.quantize import QuantableOperation

    if op.type != "Conv" or not isinstance(op, QuantableOperation):
        return False
    y = op.outputs[0]
    if op.attributes.get("activation", "Linear") != "Linear" or y.name in graph.outputs or len(y.dest_ops) != 1:
        return False
    act = y.dest_ops[0]
    if "lut" not in act.attributes or act.attributes.get("quant_type") != op.attributes.get("quant_type"):
        return False
    out = act.outputs[0]
    conv_exponent = exponent_of(y, info)
    if [v for v in act.inputs if not v.is_parameter] != [y] or conv_exponent is None or exponent_of(out, info) is None:
        return False
    out_config = act.output_quant_config[0]
    graph.remove_operation(act)
    graph.remove_variable(y)
    op.outputs.append(out)
    out.source_op = op
    op.type = CONV_LUT
    op.attributes.update(lut=act.attributes["lut"], conv_exponent=int(conv_exponent))
    op.config = OperationQuantizationConfig(list(op.input_quant_config), [out_config])
    return True


def conv_lut_plain_from(op, info) -> bool:
    """op, an int8 or int16 Conv with no activation of its own, as a ConvLut with no "lut": its inputs and
    attributes, "conv_exponent" its output's exponent."""
    from esp_ppq.IR.quantize import QuantableOperation

    if op.type != "Conv" or not isinstance(op, QuantableOperation):
        return False
    conv_exponent = exponent_of(op.outputs[0], info)
    if op.attributes.get("activation", "Linear") != "Linear" or conv_exponent is None:
        return False
    if op.attributes.get("quant_type") not in ("S8", "S16"):
        return False
    op.type = CONV_LUT
    op.attributes.update(conv_exponent=int(conv_exponent))
    return True


@contextlib.contextmanager
def conv_lut() -> Iterator[None]:
    """Each Conv and the activation with a table that alone reads it exported as one ConvLut (ADR-0031, KEHOACH 3.14
    step 2.5): esp-dl writes the convolution's map to PSRAM and reads it back for the table. The op runs esp-dl's own
    kernels a band of rows at a time and looks each band up while it is in the cache. YOLO26n (KEHOACH 3.18) on board
    B: 66 int8 Swish and 20 int16 tables took 371.5 ms of a 2.72 s picture.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joined(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.topological_sort()):
            conv_lut_from(graph, op, info)
        return graph

    EspdlExporter.prepare_graph = joined
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare


@contextlib.contextmanager
def conv_lut_plain() -> Iterator[None]:
    """Each Conv conv_lut leaves, one no table reads, exported as a ConvLut with no table (ADR-0035): esp-dl's Conv
    reads its weights from flash through the dcache, ConvLut from the run's internal RAM loan in channel tiles
    (ADR-0034). Run after conv_lut. YOLO26n (KEHOACH 3.18) on board B: 36 such Convs took 255 ms of a 2.16 s picture.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joined(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.topological_sort()):
            conv_lut_plain_from(op, info)
        return graph

    EspdlExporter.prepare_graph = joined
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare
