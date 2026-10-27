"""AttentionCore (espdl_attention.cpp): each attention's heads, scores, softmax and weighing as one op."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from ..base import exponent_of, int8_tensor, laid_shape, read_once
from .softmax_quant import SOFTMAX_QUANT

ATTENTION_CORE = "AttentionCore"

KEYS_PERM, ROWS_PERM, MERGE_PERM = [0, 2, 1, 3], [0, 2, 3, 1], [0, 3, 1, 2]


def head_split(graph, var, perm: list[int], info) -> tuple | None:
    """(source, ops, heads) when var is Reshape(Transpose(Reshape(source), perm)) of an int8 [1, T, H * D] source cut
    into heads [1, T, H, D], each variable after source read by the next op alone, on source's exponent."""
    last = var.source_op
    if last is None or last.type != "Reshape" or not read_once(graph, var):
        return None
    moved = last.inputs[0]
    turn = moved.source_op
    if turn is None or turn.type != "Transpose" or turn.attributes.get("perm") != perm or not read_once(graph, moved):
        return None
    cut = turn.inputs[0]
    first = cut.source_op
    if first is None or first.type != "Reshape" or not read_once(graph, cut):
        return None
    source = first.inputs[0]
    shape, heads = laid_shape(source, info), laid_shape(cut, info)
    if len(shape) != 3 or shape[0] != 1 or len(heads) != 4 or heads[:2] != shape[:2] or heads[2] * heads[3] != shape[2]:
        return None
    if exponent_of(source, info) is None or exponent_of(var, info) != exponent_of(source, info):
        return None
    return source, [first, turn, last], heads[2]


def head_merge(graph, var, info) -> tuple | None:
    """(out, ops) when var, the heads [H, D, T], goes through Reshape, Transpose MERGE_PERM and Reshape into out
    [1, T, H * D] on var's exponent, each variable on the way read by the next op alone; None otherwise."""
    ops, at = [], var
    for kind in ("Reshape", "Transpose", "Reshape"):
        if not read_once(graph, at):
            return None
        (op,) = at.dest_ops
        if op.type != kind or (kind == "Transpose" and op.attributes.get("perm") != MERGE_PERM):
            return None
        ops.append(op)
        at = op.outputs[0]
    shape = laid_shape(at, info)
    if len(shape) != 3 or shape[0] != 1 or exponent_of(at, info) != exponent_of(var, info):
        return None
    return at, ops


def int8_product(op) -> bool:
    """op is an int8 MatMul of two maps on one grid each."""
    from esp_ppq.IR.quantize import QuantableOperation

    return (
        op is not None
        and op.type == "MatMul"
        and isinstance(op, QuantableOperation)
        and op.attributes.get("activation", "Linear") == "Linear"
        and not any(v.is_parameter for v in op.inputs)
        and all(int8_tensor(c) for c in [*op.input_quant_config, *op.output_quant_config])
    )


def attention_core_from(graph, softmax, info) -> bool:
    """softmax, a SoftmaxQuant over axis 1 of the scores of keys [H, T, D] by queries [H, D, T], each cut into heads
    from its projection by head_split, read by one product of values [H, D, T] whose heads head_merge lays back,
    replaced by one AttentionCore reading the three projections and writing the merged heads; each product's shift
    1 or more. The ops and middle maps go."""
    from esp_ppq.core import OperationQuantizationConfig

    if softmax.type != SOFTMAX_QUANT or softmax.attributes.get("axis") != 1 or not read_once(graph, softmax.outputs[0]):
        return False
    scores, probs = softmax.inputs[0], softmax.outputs[0]
    product, (weigh,) = scores.source_op, probs.dest_ops
    if (
        not int8_product(product)
        or not int8_product(weigh)
        or not read_once(graph, scores)
        or weigh.inputs[1] is not probs
    ):
        return False
    found = [
        head_split(graph, product.inputs[0], KEYS_PERM, info),
        head_split(graph, product.inputs[1], ROWS_PERM, info),
        head_split(graph, weigh.inputs[0], ROWS_PERM, info),
    ]
    merged = head_merge(graph, weigh.outputs[0], info)
    if None in found or merged is None or len({f[2] for f in found}) != 1:
        return False
    sources = [f[0] for f in found]
    if len({tuple(laid_shape(v, info)) for v in sources}) != 1 or laid_shape(merged[0], info) != laid_shape(
        sources[0], info
    ):
        return False
    e = {name: exponent_of(v, info) for name, v in (("k", sources[0]), ("q", sources[1]), ("v", sources[2]))}
    e.update(s=exponent_of(scores, info), p=exponent_of(probs, info), o=exponent_of(weigh.outputs[0], info))
    if None in e.values() or e["s"] - e["k"] - e["q"] < 1 or e["o"] - e["v"] - e["p"] < 1:
        return False
    out, extra = merged[0], softmax.inputs[1:]
    configs = [*product.input_quant_config, weigh.input_quant_config[0], *softmax.input_quant_config[1:]]
    out_config = merged[1][-1].output_quant_config[0]
    gone = [product, weigh, *(op for f in found for op in f[1]), *merged[1]]
    middles = [v for op in gone for v in op.outputs if v is not out] + [probs]
    for v in softmax.inputs:
        v.dest_ops.remove(softmax)
    softmax.inputs.clear()
    probs.source_op = None
    softmax.outputs.clear()
    for op in gone:
        graph.remove_operation(op)
    for v in middles:
        graph.remove_variable(v)
    for v in [*sources, *extra]:
        softmax.inputs.append(v)
        v.dest_ops.append(softmax)
    softmax.outputs.append(out)
    out.source_op = softmax
    softmax.type = ATTENTION_CORE
    del softmax.attributes["axis"]
    softmax.attributes.update(
        heads=int(found[0][2]),
        score_shift=int(e["s"] - e["k"] - e["q"]),
        value_shift=int(e["o"] - e["v"] - e["p"]),
        score_exponent=int(e["s"]),
        prob_exponent=int(e["p"]),
    )
    softmax.config = OperationQuantizationConfig(configs, [out_config])
    return True


@contextlib.contextmanager
def attention_core() -> Iterator[None]:
    """Each attention esp-dl runs as head splits, a score MatMul, a softmax over axis 1, a weighing MatMul and a head
    merge exported as one AttentionCore (KEHOACH 3.14 step 2.5): every step wrote a whole map through PSRAM. The op
    lays each head once and runs both products as pointwise rows, 16 query frames at a time, each block's softmax as
    SoftmaxQuant does. Run after softmax_quant and softmax_scale. ReDimNet2 b0 (KEHOACH 3.17) on board B: each
    attention at 148 frames took ~17 ms.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.operations.values()):
            if op.name in graph.operations:
                attention_core_from(graph, op, info)
        return graph

    EspdlExporter.prepare_graph = joining
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare
