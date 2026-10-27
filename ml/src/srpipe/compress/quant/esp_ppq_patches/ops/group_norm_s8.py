"""GroupNormS8 (espdl_group_norm_s8.cpp): each GroupNorm chain of agg_gnorm as one op on its [T, C] map."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from ..base import config_of, exponent_of, int8_tensor, laid_shape, read_once
from .layernorm_s8 import LAYERNORM_S8

GROUP_NORM_S8 = "GroupNormS8"
CHANNELS_SWAPPED = [0, 2, 1]


def only_reader(graph, var, kind: str):
    """The op of type kind reading var, when it alone reads var and var is no graph output; None otherwise."""
    if var.name in graph.outputs or len(var.dest_ops) != 1 or var.dest_ops[0].type != kind:
        return None
    return var.dest_ops[0]


def channel_constant(op, channels: int):
    """op's one parameter input when it holds a value per channel and op reads one map besides it; None otherwise."""
    params = [v for v in op.inputs if v.is_parameter]
    if len(params) != 1 or len(op.inputs) != 2 or params[0].value is None or params[0].value.numel() != channels:
        return None
    return params[0]


def one_value(var) -> float | None:
    """The one value var holds, as the simulation reads the parameter: the export has already stored it as integers on
    its grid, which times the grid's step gives back; None when it holds more than one."""
    import torch

    config = config_of(var)
    if config is None or config.scale.numel() != 1 or var.value is None:
        return None
    stored = var.value.flatten()
    return float(stored[0]) * float(config.scale) if bool(torch.all(stored == stored[0])) else None


def group_norm_s8_from(graph, norm, info) -> bool:
    """norm, the LayerNorm of a GroupNorm1d chain (Transpose to channels first, Reshape to [1, G, n], the norm with
    one gamma and one beta value, Reshape to [1, C, T], a Mul and an Add of int8 constants per channel on the output's
    exponent, Transpose back), with the chain as one GroupNormS8 on the [1, T, C] map the first Transpose reads."""
    from esp_ppq.core import OperationQuantizationConfig
    from esp_ppq.IR.quantize import QuantableOperation

    if norm.type not in ("LayerNormalization", LAYERNORM_S8) or not isinstance(norm, QuantableOperation):
        return False
    to_groups = norm.inputs[0].source_op
    if to_groups is None or to_groups.type != "Reshape" or not read_once(graph, norm.inputs[0]):
        return False
    first = to_groups.inputs[0].source_op
    if first is None or first.type != "Transpose" or list(first.attributes.get("perm", [])) != CHANNELS_SWAPPED:
        return False
    if not read_once(graph, to_groups.inputs[0]):
        return False
    back = only_reader(graph, norm.outputs[0], "Reshape")
    mul = back and only_reader(graph, back.outputs[0], "Mul")
    add = mul and only_reader(graph, mul.outputs[0], "Add")
    last = add and only_reader(graph, add.outputs[0], "Transpose")
    if not last or list(last.attributes.get("perm", [])) != CHANNELS_SWAPPED:
        return False
    x, y = first.inputs[0], last.outputs[0]
    shape, grouped = laid_shape(x, info), laid_shape(norm.inputs[0], info)
    if len(shape) != 3 or shape[0] != 1 or laid_shape(y, info) != shape or len(grouped) != 3:
        return False
    frames, channels, groups = shape[1], shape[2], grouped[1]
    if channels % groups or grouped[2] != channels // groups * frames:
        return False
    w, b = channel_constant(mul, channels), channel_constant(add, channels)
    gamma = one_value(norm.inputs[1]) if len(norm.inputs) > 1 else None
    beta = one_value(norm.inputs[2]) if len(norm.inputs) > 2 else 0.0
    if w is None or b is None or gamma is None or beta is None:
        return False
    ops = (first, to_groups, norm, back, mul, add, last)
    if not all(isinstance(op, QuantableOperation) for op in (norm, mul, add)):
        return False
    if not all(int8_tensor(c) for op in (norm, mul, add) for c in [*op.input_quant_config, *op.output_quant_config]):
        return False
    e = {k: exponent_of(v, info) for k, v in (("x", x), ("n", norm.outputs[0]), ("w", w), ("m", mul.outputs[0]))}
    e |= {"b": exponent_of(b, info), "a": exponent_of(add.outputs[0], info), "y": exponent_of(y, info)}
    if None in e.values() or not e["m"] == e["b"] == e["a"] == e["y"] or e["m"] - e["n"] - e["w"] < 1:
        return False
    configs = [
        first.input_quant_config[0],
        mul.input_quant_config[mul.inputs.index(w)],
        add.input_quant_config[add.inputs.index(b)],
    ]
    out_config = last.output_quant_config[0]
    epsilon = float(norm.attributes.get("epsilon", 1e-5))
    middles = [first.outputs[0], to_groups.outputs[0], norm.outputs[0], back.outputs[0], mul.outputs[0]]
    middles.append(add.outputs[0])
    for op, v in ((first, x), (mul, w), (add, b)):
        op.inputs.remove(v)
        v.dest_ops.remove(op)
    last.outputs.remove(y)
    y.source_op = None
    for v in list(norm.inputs[1:]):
        graph.remove_variable(v)
    for op in ops:
        if op is not norm:
            graph.remove_operation(op)
    for v in middles:
        if v.name in graph.variables:
            graph.remove_variable(v)
    norm.inputs.clear()
    norm.outputs.clear()
    for v in (x, w, b):
        norm.inputs.append(v)
        v.dest_ops.append(norm)
    norm.outputs.append(y)
    y.source_op = norm
    norm.type = GROUP_NORM_S8
    for name in [a for a in norm.attributes if a != "quant_type"]:
        del norm.attributes[name]
    norm.attributes.update(
        groups=int(groups),
        epsilon=epsilon,
        gamma=gamma,
        beta=beta,
        norm_exponent=int(e["n"]),
        mul_shift=int(e["m"] - e["n"] - e["w"]),
    )
    norm.config = OperationQuantizationConfig(configs, [out_config])
    return True


@contextlib.contextmanager
def group_norm_s8() -> Iterator[None]:
    """Each GroupNorm chain of ReDimNet2's agg_gnorm exported as one GroupNormS8 (KEHOACH 3.14 step 2.5): esp-dl moved
    each [T, C] map to group-major order and back through two Transposes around the LayerNorm, then ran the Mul and
    the Add through PSRAM. The op takes each group's LayerNorm as layernorm_as_espdl does, in its axis order, then the
    Mul's rounding and the saturating Add per value. ReDimNet2 b3 (KEHOACH 3.17) on board B: 7 chains, ~3.3 s.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.operations.values()):
            if op.name in graph.operations:
                group_norm_s8_from(graph, op, info)
        return graph

    EspdlExporter.prepare_graph = joining
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare
