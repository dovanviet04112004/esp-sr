"""StageSum (espdl_stage_sum.cpp): each weighted sum of stage outputs as one op."""

from __future__ import annotations

import contextlib
import math
from collections.abc import Iterator

from ..base import CHANNEL_STEP, int8_tensor, laid_shape

STAGE_SUM = "StageSum"


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
