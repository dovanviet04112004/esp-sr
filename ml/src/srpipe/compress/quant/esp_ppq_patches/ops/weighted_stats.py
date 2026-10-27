"""WeightedStats (espdl_weighted_stats.cpp): the pool's weighted sums of x and x*x over time as one op."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from ..base import CHANNEL_STEP, exponent_of, int8_tensor, laid_shape, read_once

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
