"""StrideDepthwise (espdl_stride_depthwise.cpp): depthwise convolutions over 2 or 4 frames at that stride."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import numpy as np

from ..base import CHANNEL_STEP, int8_tensor, laid_shape
from .conv_block import block_shifts, depthwise_taps

STRIDE_DEPTHWISE = "StrideDepthwise"

STRIDES = (2, 4)


def stride_depthwise_from(op, info) -> bool:
    """op, an int8 depthwise convolution with bias of k frames at a stride of k, k in STRIDES, over a [1, T, F, C] map,
    C a multiple of 8 and its shift 1 or more, renamed StrideDepthwise: taps [Cp / 16][k][16] and bias [Cp], Cp
    being C padded with zeros to a multiple of 16."""
    import torch
    from esp_ppq.IR.quantize import QuantableOperation

    if op.type != "Conv" or not isinstance(op, QuantableOperation) or len(op.inputs) != 3:
        return False
    attrs, (x, w, b) = op.attributes, op.inputs
    k = attrs.get("kernel_shape", [0])[0]
    laid, out = laid_shape(x, info), laid_shape(op.outputs[0], info)
    if (
        k not in STRIDES
        or attrs.get("kernel_shape") != [k, 1]
        or attrs.get("strides") != [k, 1]
        or any(attrs.get("pads", []))
        or attrs.get("dilations", [1, 1]) != [1, 1]
        or attrs.get("activation", "Linear") != "Linear"
        or len(laid) != 4
        or laid[0] != 1
        or attrs.get("group", 1) != laid[-1]
        or laid[-1] % (CHANNEL_STEP // 2)
        or out != [1, laid[1] // k, laid[2], laid[3]]
        or not all(int8_tensor(c) for c in [*op.input_quant_config[:2], *op.output_quant_config])
    ):
        return False
    shifts = block_shifts(x, [op], 0, info)
    if shifts is None or shifts[0] < 1:
        return False
    c = laid[-1]
    cp = -(-c // CHANNEL_STEP) * CHANNEL_STEP
    taps = np.pad(depthwise_taps(w.value.cpu().numpy(), c, k), [(0, 0), (0, cp - c)])
    laid_taps = taps.reshape(k, cp // CHANNEL_STEP, CHANNEL_STEP).transpose(1, 0, 2)
    w.value = torch.from_numpy(np.ascontiguousarray(laid_taps)).to(w.value.dtype)
    b.value = torch.from_numpy(np.pad(b.value.cpu().numpy().reshape(-1), (0, cp - c))).to(b.value.dtype)
    op.type = STRIDE_DEPTHWISE
    for name in [a for a in op.attributes if a != "quant_type"]:
        del op.attributes[name]
    op.attributes.update(shift=int(shifts[0]), stride=int(k))
    return True


@contextlib.contextmanager
def stride_depthwise() -> Iterator[None]:
    """Each int8 depthwise convolution of 2 or 4 frames at that stride exported as StrideDepthwise (KEHOACH 3.14 step
    2.5): esp-dl runs a depthwise kernel not 3x3 a pixel a call, and 24 channels unaligned. The op sums each output
    vector's k taps in QACC over rows padded to 16-byte vectors. ReDimNet2 b0 (KEHOACH 3.17) on board B: stage 2's and
    stage 4's took 6.6 and 2.3 ms at 12.4 and 4.3 cycles a multiply-add.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def renaming(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in graph.operations.values():
            stride_depthwise_from(op, info)
        return graph

    EspdlExporter.prepare_graph = renaming
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare
