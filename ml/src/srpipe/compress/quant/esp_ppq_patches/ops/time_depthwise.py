"""TimeDepthwise (espdl_time_depthwise.cpp): depthwise convolutions over time of an odd kernel, padded to keep T."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import numpy as np

from ..base import CHANNEL_STEP, int8_tensor, laid_shape
from .conv_block import block_shifts, depthwise_taps

TIME_DEPTHWISE = "TimeDepthwise"


def time_depthwise_from(op, info) -> bool:
    """op, an int8 depthwise convolution with bias over time on a [1, T, C] map, of an odd kernel K from 3 at stride 1,
    padded K // 2 frames at each end, Linear, C a multiple of 8 and its shift 1 or more, renamed TimeDepthwise: taps
    [K][Cp] and bias [Cp], Cp being C padded with zeros to a multiple of 16."""
    import torch
    from esp_ppq.IR.quantize import QuantableOperation

    if op.type != "Conv" or not isinstance(op, QuantableOperation) or len(op.inputs) != 3:
        return False
    attrs, (x, w, b) = op.attributes, op.inputs
    k = attrs.get("kernel_shape", [0])[0]
    laid = laid_shape(x, info)
    if (
        len(attrs.get("kernel_shape", [])) != 1
        or k < 3
        or k % 2 == 0
        or attrs.get("strides") != [1]
        or attrs.get("pads") != [k // 2, k // 2]
        or attrs.get("dilations", [1]) != [1]
        or attrs.get("activation", "Linear") != "Linear"
        or len(laid) != 3
        or laid[0] != 1
        or attrs.get("group", 1) != laid[-1]
        or laid[-1] % (CHANNEL_STEP // 2)
        or laid_shape(op.outputs[0], info) != laid
        or not all(int8_tensor(c) for c in [*op.input_quant_config[:2], *op.output_quant_config])
    ):
        return False
    shifts = block_shifts(x, [op], 0, info)
    if shifts is None or shifts[0] < 1:
        return False
    c = laid[-1]
    cp = -(-c // CHANNEL_STEP) * CHANNEL_STEP
    taps = np.pad(depthwise_taps(w.value.cpu().numpy(), c, k), [(0, 0), (0, cp - c)])
    w.value = torch.from_numpy(np.ascontiguousarray(taps)).to(w.value.dtype)
    b.value = torch.from_numpy(np.pad(b.value.cpu().numpy().reshape(-1), (0, cp - c))).to(b.value.dtype)
    op.type = TIME_DEPTHWISE
    for name in [a for a in op.attributes if a != "quant_type"]:
        del op.attributes[name]
    op.attributes.update(shift=int(shifts[0]))
    return True


@contextlib.contextmanager
def time_depthwise() -> Iterator[None]:
    """Each int8 Linear depthwise convolution over time of an odd kernel exported as TimeDepthwise (KEHOACH 3.14 step
    2.5): esp-dl runs b3's 72 channels through its unaligned kernel at 0.45-0.6 cycles a multiply-add. The op sums
    each output vector's K taps in QACC over rows padded to 16-byte vectors and zero rows at both ends. ReDimNet2 b3
    (KEHOACH 3.17) on board B: the TCMs' 24 convolutions of 7 to 59 frames, 12.1 ms a window.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def renaming(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in graph.operations.values():
            time_depthwise_from(op, info)
        return graph

    EspdlExporter.prepare_graph = renaming
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare
