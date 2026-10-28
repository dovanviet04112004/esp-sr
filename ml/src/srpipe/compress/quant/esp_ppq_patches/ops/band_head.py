"""BandHead (espdl_band_head.cpp): the head of SwiftF0's network after its residual blocks, the Slice of the lowest
bands, the 3x3 convolution and, from it, the 5x1 spread with its ReduceMax and the 1x1 pitch convolution, as one op
of two outputs."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import numpy as np

from ..base import CHANNEL_STEP, exponent_of, int8_tensor, laid_shape, linked
from .band_block import conv_shift, real_inputs
from .conv_block import dense_filters

BAND_HEAD = "BandHead"
KERNEL_INPUTS = 12  # rows a tap of band_s8_esp32s3.S's weights
HEAD_PADS = [1, 1, 0, 1]  # a band below, none above, a frame either side
SPREAD_PADS = [2, 0, 2, 0]


def int8_conv(op, kernel: list[int], pads: list[int], activation: str, outputs: int | None = None) -> bool:
    """op is an int8 convolution with bias of one group at a stride of 1, undilated, of that kernel, padding and
    activation, giving outputs channels when given."""
    from esp_ppq.IR.quantize import QuantableOperation

    if op is None or op.type != "Conv" or not isinstance(op, QuantableOperation) or len(op.inputs) != 3:
        return False
    attrs = op.attributes
    if attrs.get("kernel_shape") != kernel or attrs.get("pads", [0] * 4) != pads or attrs.get("group", 1) != 1:
        return False
    if attrs.get("strides", [1, 1]) != [1, 1] or attrs.get("dilations", [1, 1]) != [1, 1]:
        return False
    if attrs.get("activation", "Linear") != activation:
        return False
    if outputs is not None and op.inputs[1].value.shape[-1] != outputs:
        return False
    return all(int8_tensor(c) for c in [*op.input_quant_config[:2], *op.output_quant_config])


def band_slice(op, info) -> int | None:
    """The bands op keeps when it is a Slice of the lowest bands of a [1, B, T, 16] map, the laid axis 1; None
    otherwise."""
    if op.type != "Slice" or len(op.inputs) < 4 or not all(v.is_parameter for v in op.inputs[1:]):
        return None
    laid = laid_shape(op.inputs[0], info)
    starts, ends, axes = (v.value.tolist() for v in op.inputs[1:4])
    steps = op.inputs[4].value.tolist() if len(op.inputs) > 4 else [1]
    if len(laid) != 4 or laid[0] != 1 or laid[-1] != CHANNEL_STEP or axes != [1] or starts != [0] or steps != [1]:
        return None
    return int(ends[0]) if 0 < ends[0] < laid[1] else None


def band_head_parts(graph, op, info) -> tuple | None:
    """(slice, head, spread, reduce, pitch, bands, shifts) when op takes the lowest bands of a [1, B, T, 16] map for
    head alone: a 3x3 convolution with ReLU padded HEAD_PADS, read by spread alone, a 5x1 Linear one padded
    SPREAD_PADS to as many channels whose output a ReduceMax over the bands alone reads on its exponent, and by pitch
    alone, a 1x1 Linear one to a single channel; each convolution with bias and a shift of 1 or more. None
    otherwise."""
    bands = band_slice(op, info)
    sliced = op.outputs[0]
    if bands is None or sliced.name in graph.outputs or len(sliced.dest_ops) != 1:
        return None
    (head,) = sliced.dest_ops
    h = head.outputs[0]
    if not int8_conv(head, [3, 3], HEAD_PADS, "Relu") or h.name in graph.outputs or len(h.dest_ops) != 2:
        return None
    by_kernel = {tuple(c.attributes.get("kernel_shape", [])): c for c in h.dest_ops}
    spread, pitch = by_kernel.get((5, 1)), by_kernel.get((1, 1))
    channels = laid_shape(h, info)[-1]
    if not int8_conv(spread, [5, 1], SPREAD_PADS, "Linear") or not int8_conv(pitch, [1, 1], [0] * 4, "Linear"):
        return None
    s = spread.outputs[0]
    if laid_shape(s, info)[-1] != channels or laid_shape(pitch.outputs[0], info)[-1] != 1:
        return None
    if s.name in graph.outputs or len(s.dest_ops) != 1 or s.dest_ops[0].type != "ReduceMax":
        return None
    reduce = s.dest_ops[0]
    axes = reduce.inputs[1].value.tolist() if len(reduce.inputs) > 1 else reduce.attributes.get("axes")
    if axes != [1] or reduce.attributes.get("keepdims", 1) != 0:
        return None
    if exponent_of(reduce.outputs[0], info) != exponent_of(s, info):
        return None
    shifts = [conv_shift(c, info) for c in (head, spread, pitch)]
    if None in shifts or min(shifts) < 1 or channels > CHANNEL_STEP:
        return None
    return op, head, spread, reduce, pitch, bands, shifts


def laid_band_weights(op, kernel: tuple[int, int]) -> np.ndarray:
    """op's weights as band_s8_esp32s3.S reads them, [time tap][band tap][12][16], its inputs and outputs padded
    with zeros, from ESP-PPQ's S3 layout of them."""
    kb, kt = kernel
    outputs = op.inputs[1].value.shape[-1]
    dense = dense_filters(op.inputs[1].value.cpu().numpy(), outputs).reshape(outputs, kb, kt, -1)
    taps = dense.transpose(2, 1, 3, 0)
    if real_inputs([taps]) > KERNEL_INPUTS:
        raise ValueError(f"{op.name} weighs more than {KERNEL_INPUTS} inputs")
    taps = taps[:, :, :KERNEL_INPUTS]
    padding = [(0, 0), (0, 0), (0, KERNEL_INPUTS - taps.shape[2]), (0, CHANNEL_STEP - outputs)]
    return np.ascontiguousarray(np.pad(taps, padding))


def band_head_from(graph, op, info) -> bool:
    """The head band_head_parts finds from op replaced by one BandHead reading the map op slices, then the 3x3 and
    the 5x1 convolutions' weights laid by laid_band_weights and their bias padded to 16, then the 1x1 convolution's
    weights and bias; it gives the pitch convolution's output and the ReduceMax's. The Slice, the convolutions and
    the ReduceMax go, with the maps between them."""
    import torch
    from esp_ppq.core import OperationQuantizationConfig
    from esp_ppq.IR.quantize import QuantableOperation

    found = band_head_parts(graph, op, info)
    if found is None:
        return False
    slicer, head, spread, reduce, pitch, bands, shifts = found
    x, logits, peaks = slicer.inputs[0], pitch.outputs[0], reduce.outputs[0]
    params, configs = [], [slicer.input_quant_config[0]]
    for conv, kernel in ((head, (3, 3)), (spread, (5, 1)), (pitch, None)):
        w, b = conv.inputs[1], conv.inputs[2]
        if kernel is not None:
            w.value = torch.from_numpy(laid_band_weights(conv, kernel)).to(w.value.dtype)
            b.value = torch.nn.functional.pad(b.value.flatten(), (0, CHANNEL_STEP - b.value.numel()))
        else:
            w.value = torch.from_numpy(np.ascontiguousarray(dense_filters(w.value.cpu().numpy(), 1))).to(w.value.dtype)
        for v in (w, b):
            v.dest_ops.remove(conv)
            params.append(v)
        configs += conv.input_quant_config[1:3]
        del conv.inputs[1:]
    out_configs = [pitch.output_quant_config[0], reduce.output_quant_config[0]]
    for var, maker in ((logits, pitch), (peaks, reduce)):
        maker.outputs.remove(var)
        var.source_op = None
    gone = [slicer, head, spread, reduce, pitch]
    middles = [v for gone_op in gone for v in gone_op.outputs]
    for gone_op in gone:
        graph.remove_operation(gone_op)
    for v in middles:
        graph.remove_variable(v)
    attributes = {"shifts": shifts, "bands": bands, "quant_type": head.attributes["quant_type"]}
    block = graph.create_operation(op_type=BAND_HEAD, attributes=attributes)
    config = OperationQuantizationConfig(configs, out_configs)
    linked(graph, QuantableOperation(block, config, head.platform), [x, *params], [logits, peaks])
    return True


@contextlib.contextmanager
def band_head() -> Iterator[None]:
    """SwiftF0's head exported as one BandHead (KEHOACH 3.14 step 2.5): esp-dl runs its three narrow convolutions
    on the unaligned kernel and writes every map through PSRAM. The op runs the head a frame column at a time, its
    columns in internal RAM, each integer as esp-dl's ops give it. Run after band_block and aligned_channels, whose
    16-channel map it reads. SwiftF0 (KEHOACH 3.11) on board B: the head took 12.3 ms of a 41.3 ms 16-frame window.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.topological_sort()):
            if op.name in graph.operations:
                band_head_from(graph, op, info)
        return graph

    EspdlExporter.prepare_graph = joining
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare
