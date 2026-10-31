"""C3k2Block (espdl_c3k2_block.cpp): YOLO's C3k2 of one 3x3 bottleneck, its two 1x1 ConvLuts, Slices, Add and
Concat, as one op that keeps every middle map in a few rows."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from ..base import exponent_of, laid_shape, read_once

C3K2_BLOCK = "C3k2Block"
CONV_LUT = "ConvLut"


def conv_lut_of(var, kernel: int, info) -> object | None:
    """The int8 ConvLut with a table making var, of one group, stride 1 and a kernel x kernel window padded to keep
    the map's size; None otherwise."""
    op = var.source_op
    if op is None or op.type != CONV_LUT or "lut" not in op.attributes or op.attributes.get("quant_type") != "S8":
        return None
    a = op.attributes
    if a.get("group", 1) != 1 or list(a.get("strides", [1, 1])) != [1, 1] or len(op.inputs) != 3:
        return None
    if list(a.get("kernel_shape", [])) != [kernel, kernel] or list(a.get("dilations", [1, 1])) != [1, 1]:
        return None
    pad = kernel // 2
    return op if list(a.get("pads", [0, 0, 0, 0])) == [pad] * 4 else None


def slice_start(op, info) -> int | None:
    """The channel op, a Slice of one axis and step 1, starts at; None when it slices otherwise."""
    if op is None or op.type != "Slice" or len(op.inputs) < 4:
        return None
    starts, axes = op.inputs[1].value, op.inputs[3].value
    if starts is None or axes is None or starts.numel() != 1 or axes.numel() != 1:
        return None
    if len(op.inputs) > 4 and op.inputs[4].value is not None and int(op.inputs[4].value.flatten()[0]) != 1:
        return None
    return int(starts.flatten()[0])


def c3k2_block_parts(graph, cat, info) -> tuple | None:
    """(cv1, m1, m2, cv2, gone) when cat joins Slice(y, 0:c), Slice(y, c:2c) and Add(Slice(y, c:2c), m2(m1(...)))
    of y = cv1(x), each a ConvLut with a table, m1 and m2 3x3, cv1 and cv2 1x1, cv2 alone reading cat, every map of the
    block read only inside it, and y, both Slices, the Add and cat on one exponent; None otherwise."""
    if cat.type != "Concat" or len(cat.inputs) != 3 or not read_once(graph, cat.outputs[0]):
        return None
    (cv2,) = cat.outputs[0].dest_ops
    if conv_lut_of(cv2.outputs[0], 1, info) is not cv2:
        return None
    s0, s1, z = cat.inputs
    first, second, add = s0.source_op, s1.source_op, z.source_op
    if first is None or second is None or first.inputs[0] is not second.inputs[0]:
        return None
    y = first.inputs[0]
    cv1 = conv_lut_of(y, 1, info)
    c = laid_shape(s0, info)[-1]
    if cv1 is None or len(y.dest_ops) != 2 or slice_start(first, info) != 0 or slice_start(second, info) != c:
        return None
    if laid_shape(s1, info)[-1] != c or laid_shape(y, info)[-1] != 2 * c or not read_once(graph, s0):
        return None
    if add is None or add.type != "Add" or len(add.inputs) != 2 or s1 not in add.inputs or not read_once(graph, z):
        return None
    u = add.inputs[1] if add.inputs[0] is s1 else add.inputs[0]
    m2 = conv_lut_of(u, 3, info)
    if m2 is None or not read_once(graph, u) or not read_once(graph, m2.inputs[0]):
        return None
    m1 = conv_lut_of(m2.inputs[0], 3, info)
    if (
        m1 is None
        or m1.inputs[0] is not s1
        or sorted(o.name for o in s1.dest_ops) != sorted(o.name for o in (m1, add, cat))
    ):
        return None
    shared = {exponent_of(v, info) for v in (y, s0, s1, u, z, cat.outputs[0])}
    if len(shared) != 1 or None in shared:
        return None
    return cv1, m1, m2, cv2, [first, second, m1, m2, add, cat, cv1]


def c3k2_block_from(graph, cat, info) -> bool:
    """The C3k2 c3k2_block_parts finds at cat replaced by one C3k2Block reading x, then the filter and bias of cv1,
    m1, m2 and cv2 in turn; attributes each one's table and convolution exponent, the block's middle maps gone."""
    from esp_ppq.core import OperationQuantizationConfig

    found = c3k2_block_parts(graph, cat, info)
    if found is None:
        return False
    cv1, m1, m2, cv2, gone = found
    x = cv1.inputs[0]
    params, configs = [], [cv1.input_quant_config[0]]
    attributes = {}
    for k, op in enumerate((cv1, m1, m2, cv2), start=1):
        attributes[f"lut{k}"] = op.attributes["lut"]
        attributes[f"exponent{k}"] = int(op.attributes["conv_exponent"])
        # remove_operation drops an op's parameters with it: the block keeps them.
        for v, config in zip(op.inputs[1:], op.input_quant_config[1:], strict=True):
            v.dest_ops.remove(op)
            params.append(v)
            configs.append(config)
        del op.inputs[1:]
    cv2.inputs[0].dest_ops.remove(cv2)
    cv2.inputs.clear()
    middles = [v for op in gone for v in op.outputs]
    for op in gone:
        graph.remove_operation(op)
    for v in middles:
        if v.name in graph.variables:
            graph.remove_variable(v)
    for v in [x, *params]:
        cv2.inputs.append(v)
        v.dest_ops.append(cv2)
    quant_type = cv2.attributes["quant_type"]
    cv2.type = C3K2_BLOCK
    cv2.attributes.clear()
    cv2.attributes.update(attributes, quant_type=quant_type)
    cv2.config = OperationQuantizationConfig(configs, cv2.output_quant_config)
    return True


@contextlib.contextmanager
def c3k2_block() -> Iterator[None]:
    """Each C3k2 of one 3x3 bottleneck whose Add and Concat keep one exponent exported as one C3k2Block (ADR-0036,
    KEHOACH 3.18): its middle maps pass through PSRAM eight times. The op runs the block a row at a time on esp-dl's
    kernels, every middle row in the cache. Run after conv_lut. YOLO26n's model.2 and model.4 on board B: ~315 ms of a
    1.99 s picture.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph
    build_luts = EspdlExporter.build_lut_proto

    def joined(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.topological_sort()):
            if op.name in graph.operations:
                c3k2_block_from(graph, op, info)
        return graph

    def with_block_luts(self, graph, info):
        return [*build_luts(self, graph, info), *block_luts(graph, info)]

    EspdlExporter.prepare_graph = joined
    EspdlExporter.build_lut_proto = with_block_luts
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare
        EspdlExporter.build_lut_proto = build_luts


def block_luts(graph, info) -> list:
    """The tables C3k2Blocks name in lut1 to lut4 that no op's "lut" exports, built as ESP-PPQ's build_lut_proto
    builds them: ESP-PPQ writes only the tables a "lut" attribute names."""
    from esp_ppq.core import DataType, convert_any_to_numpy
    from esp_ppq.parser.espdl import helper

    done = {op.attributes["lut"] for op in graph.operations.values() if op.attributes.get("lut")}
    protos = []
    for op in graph.topological_sort():
        if op.type != C3K2_BLOCK:
            continue
        for k in range(1, 5):
            name = op.attributes[f"lut{k}"]
            if name in done:
                continue
            lut = info.get_lut(name)
            protos.append(
                helper.make_tensor(
                    name=name,
                    data_type=DataType.convert_from_torch(lut.dtype).value,
                    dims=lut.shape,
                    vals=convert_any_to_numpy(lut).flatten(),
                    raw=True,
                    exponents=info.get_var_exponents(name),
                )
            )
            done.add(name)
    return protos
