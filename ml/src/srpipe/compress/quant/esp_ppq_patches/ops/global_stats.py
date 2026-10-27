"""GlobalStats (espdl_global_stats.cpp): the pool's mean over time and mean centred square as one op."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from ..base import CHANNEL_STEP, exponent_of, int8_tensor, laid_shape, read_once
from .weighted_stats import MAX_SUMMED_FRAMES, int8_map_product

GLOBAL_STATS = "GlobalStats"


def time_mean(op, info) -> bool:
    """op is an int8 ReduceMean over T of a [1, T, C] map into [1, 1, C], C a multiple of 16, T up to
    MAX_SUMMED_FRAMES."""
    from esp_ppq.IR.quantize import QuantableOperation

    if op is None or op.type != "ReduceMean" or not isinstance(op, QuantableOperation):
        return False
    axes = op.inputs[1] if len(op.inputs) > 1 else None
    laid = laid_shape(op.inputs[0], info)
    return (
        op.attributes.get("keepdims", 1) == 1
        and axes is not None
        and axes.value is not None
        and axes.value.reshape(-1).tolist() == [1]
        and len(laid) == 3
        and laid[0] == 1
        and laid[1] <= MAX_SUMMED_FRAMES
        and laid[2] % CHANNEL_STEP == 0
        and laid_shape(op.outputs[0], info) == [1, 1, laid[2]]
        and all(int8_tensor(c) for c in [op.input_quant_config[0], *op.output_quant_config])
    )


def global_stats_from(graph, op, info) -> bool:
    """op, a time_mean of the squares of x less its time_mean (requantised, or not, for the Sub alone), each square
    and difference read by the next op alone, replaced by one GlobalStats reading x and writing the mean and op's
    output; the Sub on one exponent throughout, the requantisation's shift 0 or more, the square's 1 or more."""
    from esp_ppq.core import OperationQuantizationConfig
    from esp_ppq.IR.quantize import QuantableOperation

    if not time_mean(op, info):
        return False
    square = op.inputs[0]
    squaring = square.source_op
    if not int8_map_product(squaring) or squaring.inputs[0] is not squaring.inputs[1] or not read_once(graph, square):
        return False
    centred = squaring.inputs[0]
    sub = centred.source_op
    if (
        sub is None
        or sub.type != "Sub"
        or not isinstance(sub, QuantableOperation)
        or set(centred.dest_ops) != {squaring}
    ):
        return False
    x, centre = sub.inputs
    requantiser = (
        centre.source_op if centre.source_op is not None and centre.source_op.type == "RequantizeLinear" else None
    )
    mean = requantiser.inputs[0] if requantiser is not None else centre
    if requantiser is not None and not read_once(graph, centre):
        return False
    if not time_mean(mean.source_op, info) or mean.source_op.inputs[0] is not x or centred.name in graph.outputs:
        return False
    e = {k: exponent_of(v, info) for k, v in (("x", x), ("c", centre), ("d", centred), ("m", mean), ("s", square))}
    if None in e.values() or not e["x"] == e["c"] == e["d"] or e["c"] < e["m"] or e["s"] - 2 * e["d"] < 1:
        return False
    averaging, total = mean.source_op, op.outputs[0]
    shifts = [int(e["c"] - e["m"]), int(e["s"] - 2 * e["d"])]
    configs, out_configs = (
        [averaging.input_quant_config[0]],
        [averaging.output_quant_config[0], op.output_quant_config[0]],
    )
    gone = [averaging, sub, squaring, *([requantiser] if requantiser is not None else [])]
    middles = [centred, square, *([centre] if requantiser is not None else [])]
    for v in (mean, total):
        v.source_op.outputs.remove(v)
        v.source_op = None
    for unit in gone:
        graph.remove_operation(unit)
    for v in middles:
        graph.remove_variable(v)
    for v in list(op.inputs):
        graph.remove_variable(v)
    op.inputs.append(x)
    x.dest_ops.append(op)
    op.outputs.extend([mean, total])
    for v in (mean, total):
        v.source_op = op
    op.type = GLOBAL_STATS
    for name in [a for a in op.attributes if a != "quant_type"]:
        del op.attributes[name]
    op.attributes.update(shifts=shifts, square_exponent=int(e["s"]))
    op.config = OperationQuantizationConfig(configs, out_configs)
    return True


@contextlib.contextmanager
def global_stats() -> Iterator[None]:
    """An attentive pool's mean of x over time and the mean of the squares of x less it exported as one GlobalStats
    (KEHOACH 3.14 step 2.5): esp-dl wrote the difference and its square through PSRAM, a [T, C] map each, between two
    ReduceMeans. The op sums x a row at a time, takes the mean, its requantisation, the difference and the square as
    esp-dl does each, and the means by ReduceMean's float steps. ReDimNet2 b0 (KEHOACH 3.17) on board B: ~14.5 ms.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.operations.values()):
            if op.name in graph.operations:
                global_stats_from(graph, op, info)
        return graph

    EspdlExporter.prepare_graph = joining
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare
