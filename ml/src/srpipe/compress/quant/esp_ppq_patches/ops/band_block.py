"""BandBlock (espdl_band_block.cpp): each SwiftF0 residual block, two convolutions along the bands, the residual Add
and its ReLU, as one op."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import numpy as np

from ..base import CHANNEL_STEP, exponent_of, int8_tensor, laid_shape
from .conv_block import dense_filters, relu_of_sum
from .pointwise_s8 import residual_add

BAND_BLOCK = "BandBlock"
TIME_KERNELS = (1, 3)


def band_conv(op, activation: str, info) -> tuple[int, int, int] | None:
    """(band kernel, time kernel, dilation) of op, an int8 convolution with bias of one group on a [1, B, T, 16] map:
    an odd band kernel, a time kernel of TIME_KERNELS, dilated along the bands alone, at a stride of 1 padded to keep
    the map's size, then activation; None when op is no such convolution."""
    from esp_ppq.IR.quantize import QuantableOperation

    if op is None or op.type != "Conv" or not isinstance(op, QuantableOperation) or len(op.inputs) != 3:
        return None
    attrs, laid = op.attributes, laid_shape(op.inputs[0], info)
    kb, kt = attrs.get("kernel_shape", [0, 0])
    d, dt = attrs.get("dilations", [1, 1])
    keep = [d * (kb - 1) // 2, (kt - 1) // 2] * 2
    if len(laid) != 4 or laid[0] != 1 or laid[-1] != CHANNEL_STEP or laid != laid_shape(op.outputs[0], info):
        return None
    if kb % 2 == 0 or kt not in TIME_KERNELS or dt != 1 or attrs.get("strides", [1, 1]) != [1, 1]:
        return None
    if attrs.get("pads", [0] * 4) != keep or attrs.get("group", 1) != 1:
        return None
    if attrs.get("activation", "Linear") != activation:
        return None
    if not all(int8_tensor(c) for c in [*op.input_quant_config[:2], *op.output_quant_config]):
        return None
    return int(kb), int(kt), int(d)


def conv_shift(op, info) -> int | None:
    """op's mac shift to the right, when its bias sits on the exponent of its products."""
    e_x, e_w, e_y = (exponent_of(v, info) for v in (op.inputs[0], op.inputs[1], op.outputs[0]))
    if None in (e_x, e_w, e_y) or exponent_of(op.inputs[2], info) != e_x + e_w:
        return None
    return int(e_y - e_x - e_w)


def band_block_parts(graph, relu, info) -> tuple | None:
    """(x, first, second, requantiser, kernel, shifts) when relu ends a SwiftF0 residual block on x: first, a
    band_conv with ReLU reading x, second one of its shape without, reading first's output alone, their sum with x,
    or with x through a RequantizeLinear read by the Add alone, on the sum's exponent, then relu; kernel the band and
    time kernels and the dilation, shifts each convolution's 1 or more to the right, then the residual's 0 or more.
    None otherwise."""
    if not relu_of_sum(graph, relu, info):
        return None
    add = relu.inputs[0].source_op
    for s in add.inputs:
        second = s.source_op
        kernel = band_conv(second, "Linear", info)
        h = second.inputs[0] if kernel is not None else None
        if h is None or h.name in graph.outputs or h.dest_ops != [second]:
            continue
        first = h.source_op
        found = residual_add(graph, s, info)
        if band_conv(first, "Relu", info) != kernel or found is None or found[2] is not first.inputs[0]:
            continue
        shifts = [conv_shift(first, info), conv_shift(second, info)]
        if None in shifts or min(shifts) < 1:
            return None
        return first.inputs[0], first, second, found[3], kernel, [*shifts, found[4]]
    return None


def laid_taps(op, kernel: tuple[int, int, int]) -> np.ndarray:
    """op's weights as BandBlock's kernel reads them, [time tap][band tap][input][16 outputs], from ESP-PPQ's S3
    layout of them."""
    kb, kt, _ = kernel
    dense = dense_filters(op.inputs[1].value.cpu().numpy(), CHANNEL_STEP).reshape(CHANNEL_STEP, kb, kt, -1)
    return np.ascontiguousarray(dense.transpose(2, 1, 3, 0))


def real_inputs(taps: list[np.ndarray]) -> int:
    """The inputs up to the last one any of taps weighs, 1 at least."""
    weighed = np.flatnonzero(np.any(np.stack(taps) != 0, axis=(0, 1, 2, 4)))
    return int(weighed[-1]) + 1 if weighed.size else 1


def band_block_from(graph, relu, info) -> bool:
    """relu and the block band_block_parts finds before it replaced by one BandBlock reading x, then each
    convolution's weights, laid by laid_taps over the inputs any weighs, and bias in turn; the block's other ops and
    middle maps go."""
    import torch
    from esp_ppq.core import OperationQuantizationConfig

    found = band_block_parts(graph, relu, info)
    if found is None:
        return False
    x, first, second, requantiser, kernel, shifts = found
    taps = [laid_taps(op, kernel) for op in (first, second)]
    inputs = real_inputs(taps)
    params, configs = [], [first.input_quant_config[0]]
    for op, laid in zip((first, second), taps, strict=True):
        w, b = op.inputs[1], op.inputs[2]
        w.value = torch.from_numpy(np.ascontiguousarray(laid[:, :, :inputs])).to(w.value.dtype)
        for v in (w, b):
            v.dest_ops.remove(op)
            params.append(v)
        configs += op.input_quant_config[1:3]
        del op.inputs[1:]
    add = relu.inputs[0].source_op
    relu.inputs[0].dest_ops.remove(relu)
    relu.inputs.clear()
    gone = [add, first, second, *([requantiser] if requantiser is not None else [])]
    middles = [v for op in gone for v in op.outputs]
    for op in gone:
        graph.remove_operation(op)
    for v in middles:
        graph.remove_variable(v)
    for v in [x, *params]:
        relu.inputs.append(v)
        v.dest_ops.append(relu)
    relu.type = BAND_BLOCK
    relu.attributes.pop("lut", None)
    relu.attributes.update(kernel=list(kernel[:2]), dilation=kernel[2], shifts=shifts, in_channels=inputs)
    relu.config = OperationQuantizationConfig(configs, relu.output_quant_config)
    return True


@contextlib.contextmanager
def band_block() -> Iterator[None]:
    """Each int8 SwiftF0 residual block exported as one BandBlock (KEHOACH 3.14 step 2.5): esp-dl runs its two
    convolutions, Add, ReLU and RequantizeLinear one map at a time through PSRAM, and has no Add with ReLU. The op
    runs the block a frame column at a time, the middle column in internal RAM, each integer as esp-dl's ops give it.
    Run after aligned_channels, whose 16-channel maps it reads. SwiftF0 (KEHOACH 3.11) on board B: its six blocks
    took ~1 176 ms of a 1 465 ms window.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.topological_sort()):
            if op.name in graph.operations:
                band_block_from(graph, op, info)
        return graph

    EspdlExporter.prepare_graph = joining
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare
