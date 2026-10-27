"""SoftmaxQuant (espdl_softmax_quant.cpp): each Softmax with its quantiser, attention's scaled scores as a table."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import numpy as np

from ..base import CHANNEL_STEP, exponent_of, int8_tensor, laid_shape

SOFTMAX_QUANT = "SoftmaxQuant"


def softmax_quant_from(graph, softmax) -> bool:
    """softmax and the QuantizeLinear alone reading its float32 output replaced by one SoftmaxQuant writing that
    node's int8 output, when softmax reads one int8 grid and the node quantises onto one int8 grid with no offset."""
    from esp_ppq.core import DataType
    from esp_ppq.IR.quantize import QuantableOperation

    if softmax.type != "Softmax" or not isinstance(softmax, QuantableOperation) or len(softmax.outputs) != 1:
        return False
    probs, given = softmax.outputs[0], softmax.input_quant_config[0]
    if probs.name in graph.outputs or len(probs.dest_ops) != 1 or given.num_of_bits != 8 or given.scale.numel() != 1:
        return False
    (quant,) = probs.dest_ops
    if quant.type != "QuantizeLinear" or quant.inputs[0] is not probs or quant.outputs[0].dtype != DataType.INT8:
        return False
    scale, *offset = (v.value for v in quant.inputs[1:])
    if scale is None or scale.numel() != 1 or any(o is not None and bool(o.any()) for o in offset):
        return False
    out = quant.outputs[0]
    graph.remove_operation(quant)
    graph.remove_variable(probs)
    softmax.outputs.append(out)
    out.source_op = softmax
    softmax.type = SOFTMAX_QUANT
    return True


def scaled_by_constant(graph, var, info) -> tuple | None:
    """(mul, map, constant, shift) when var, read by one op alone, is an int8 Mul of a map by one int8 constant, its
    rows at least CHANNEL_STEP long so esp-dl's S3 vector kernel runs it, the shift onto var's exponent to the right;
    None otherwise."""
    from esp_ppq.IR.quantize import QuantableOperation

    mul = var.source_op
    if mul is None or mul.type != "Mul" or not isinstance(mul, QuantableOperation) or len(mul.inputs) != 2:
        return None
    if var.name in graph.outputs or len(var.dest_ops) != 1 or laid_shape(var, info)[-1] < CHANNEL_STEP:
        return None
    constants = [v for v in mul.inputs if v.is_parameter and v.value is not None and v.value.numel() == 1]
    maps = [v for v in mul.inputs if not v.is_parameter]
    if len(constants) != 1 or len(maps) != 1 or not all(int8_tensor(c) for c in mul.input_quant_config):
        return None
    exponents = [exponent_of(v, info) for v in (var, maps[0], constants[0])]
    if None in exponents or exponents[0] - exponents[1] - exponents[2] < 0:
        return None
    return mul, maps[0], int(constants[0].value.item()), exponents[0] - exponents[1] - exponents[2]


def products_by_byte(constant: int, shift: int) -> np.ndarray:
    """int8 x * constant by the byte of x, as tie728_s8_mul_w1_16_w2_1 gives it: the QACC product rounded half up
    by shift as tie728_s8_vector_round_result does, saturated."""
    x = np.arange(256, dtype=np.int64).astype(np.uint8).view(np.int8).astype(np.int64)
    product = x * constant
    if shift:
        product = ((product >> (shift - 1)) + 1) >> 1
    return np.clip(product, -128, 127).astype(np.int8)


def softmax_scale_from(graph, softmax, info) -> bool:
    """A SoftmaxQuant reading the int8 product of a map by one constant reads the map instead, the products by the
    map's byte as a second input on the product's exponent; the Mul and its product go."""
    import torch
    from esp_ppq.core import DataType

    if softmax.type != SOFTMAX_QUANT or len(softmax.inputs) != 1:
        return False
    found = scaled_by_constant(graph, softmax.inputs[0], info)
    if found is None:
        return False
    mul, source, constant, shift = found
    product, exponent = mul.outputs[0], exponent_of(softmax.inputs[0], info)
    configs = [mul.input_quant_config[mul.inputs.index(source)], softmax.input_quant_config[0]]
    product.dest_ops.clear()
    graph.remove_operation(mul)
    graph.remove_variable(product)
    table = graph.create_variable(value=torch.from_numpy(products_by_byte(constant, shift)), is_parameter=True)
    table.dtype = DataType.INT8
    info.add_var_exponents(table.name, exponent)
    softmax.inputs[:] = [source, table]
    source.dest_ops.append(softmax)
    table.dest_ops.append(softmax)
    softmax.config.input_quantization_config[:] = configs
    return True


@contextlib.contextmanager
def softmax_scale() -> Iterator[None]:
    """Each SoftmaxQuant reading an int8 map times one constant reads the map, the products by its byte as a table
    (KEHOACH 3.14 step 2.5): esp-dl's Mul wrote every scaled score through PSRAM for the op to read back. The op looks
    each byte up in the table before its exp table. Run after softmax_quant. ReDimNet2 b0 (KEHOACH 3.17) on board B:
    each attention at 148 frames scaled its scores in 3.1 ms.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.operations.values()):
            softmax_scale_from(graph, op, info)
        return graph

    EspdlExporter.prepare_graph = joining
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare


@contextlib.contextmanager
def softmax_quant() -> Iterator[None]:
    """Each int8 Softmax and the QuantizeLinear alone reading it exported as one SoftmaxQuant (KEHOACH 3.14 step 2.5):
    esp-dl's Softmax writes float32 a column apart through PSRAM for the quantiser to read back. The op takes the same
    table, sums and rounding and writes int8. Run after lean_quantizers, which leaves one quantiser per grid.
    ReDimNet2 b0 (KEHOACH 3.17) on board B: each attention at 148 frames spent 24 ms in the pair.
    """
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        for op in list(graph.operations.values()):
            softmax_quant_from(graph, op)
        return graph

    EspdlExporter.prepare_graph = joining
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare
