"""MatMulS8 (espdl_matmul_s8.cpp): each projection by constant weights with its bias as one op."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import numpy as np

from ..base import CHANNEL_STEP, exponent_of, int8_tensor, laid_shape
from .conv_block import dense_filters

MATMUL_S8 = "MatMulS8"


def bias_after(graph, out, info) -> tuple | None:
    """(add, bias) when out, read by one Add alone, gets an int8 bias of its last axis added on its own exponent, the
    sum's too; None otherwise."""
    if out.name in graph.outputs or len(out.dest_ops) != 1:
        return None
    (add,) = out.dest_ops
    if add.type != "Add" or len(add.inputs) != 2:
        return None
    biases = [v for v in add.inputs if v.is_parameter and v.value is not None]
    if len(biases) != 1 or biases[0].value.numel() != laid_shape(out, info)[-1]:
        return None
    e = exponent_of(out, info)
    if e is None or exponent_of(biases[0], info) != e or exponent_of(add.outputs[0], info) != e:
        return None
    return add, biases[0]


def matmul_s8_from(graph, op, info) -> bool:
    """op, an int8 MatMul of a map's rows [..., K] by constant int8 weights [K, N], K and N multiples of 8 and its mac
    shift 1 or more, as one MatMulS8 taking also the bias Add alone reading it: weights [Np / 16][K][16], bias
    [Np], Np being N padded with zeros to a multiple of 16."""
    import torch
    from esp_ppq.core import OperationQuantizationConfig
    from esp_ppq.IR.quantize import QuantableOperation

    if op.type != "MatMul" or not isinstance(op, QuantableOperation) or len(op.inputs) != 2:
        return False
    (x, w), out = op.inputs, op.outputs[0]
    if x.is_parameter or not w.is_parameter or w.value is None or w.value.dim() != 2:
        return False
    k, n = w.value.shape
    if laid_shape(x, info)[-1] != k or k % (CHANNEL_STEP // 2) or n % (CHANNEL_STEP // 2):
        return False
    if not all(int8_tensor(c) for c in [*op.input_quant_config, *op.output_quant_config]):
        return False
    activation, exponents = op.attributes.get("activation", "Linear"), [exponent_of(v, info) for v in (out, x, w)]
    if activation not in ("Linear", "Relu") or None in exponents or exponents[0] - exponents[1] - exponents[2] < 1:
        return False
    padded = -(-n // CHANNEL_STEP) * CHANNEL_STEP
    weights = np.pad(dense_filters(w.value.cpu().numpy(), n), [(0, padded - n), (0, 0)])
    w.value = torch.from_numpy(
        np.ascontiguousarray(weights.reshape(padded // CHANNEL_STEP, CHANNEL_STEP, k).transpose(0, 2, 1))
    )
    configs, out_config = list(op.input_quant_config), op.output_quant_config[0]
    found = bias_after(graph, out, info)
    if found is not None:
        add, bias = found
        configs.append(add.input_quant_config[add.inputs.index(bias)])
        out_config, total = add.output_quant_config[0], add.outputs[0]
        add.inputs.remove(bias)
        bias.dest_ops.remove(add)
        graph.remove_operation(add)
        graph.remove_variable(out)
        op.outputs.append(total)
        total.source_op = op
        bias.value = torch.from_numpy(np.pad(bias.value.cpu().numpy().reshape(-1), (0, padded - n)))
        op.inputs.append(bias)
        bias.dest_ops.append(op)
    op.type = MATMUL_S8
    for name in [a for a in op.attributes if a != "quant_type"]:
        del op.attributes[name]
    op.attributes.update(
        shift=int(exponents[0] - exponents[1] - exponents[2]), relu=int(activation == "Relu"), columns=int(n)
    )
    op.config = OperationQuantizationConfig(configs, [out_config])
    return True


@contextlib.contextmanager
def matmul_s8() -> Iterator[None]:
    """Each int8 MatMul of a map's rows by constant weights exported as one MatMulS8 (KEHOACH 3.14 step 2.5), with the
    bias Add after it: esp-dl runs a 24-column product's first 16 columns through its unaligned kernel and the rest
    through C, then the Add through PSRAM. The op sums each row as a pointwise convolution, rounds as esp-dl's
    MatMul, then adds the bias saturating. ReDimNet2 b0 (KEHOACH 3.17) on board B: its TCMs' 36 projections took
    ~0.7 ms each, 2.25 cycles a multiply-add.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.operations.values()):
            if op.name in graph.operations:
                matmul_s8_from(graph, op, info)
        return graph

    EspdlExporter.prepare_graph = joining
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare
