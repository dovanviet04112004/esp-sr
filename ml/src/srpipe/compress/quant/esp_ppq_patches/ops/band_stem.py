"""BandStem (espdl_band_stem.cpp): SwiftF0's first convolution, from the graph's input of a few channels to its
16-channel trunk, as one op on BandBlock's kernel."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import numpy as np

from ..base import CHANNEL_STEP, laid_shape
from .band_block import conv_shift, real_inputs
from .band_head import int8_conv
from .conv_block import dense_filters

BAND_STEM = "BandStem"
LANES_MAX = 12  # the inputs a pixel band_s8_esp32s3.S's kernels take


def band_stem_lanes(graph, op, info) -> int | None:
    """The input channels of op when it is SwiftF0's stem: a 3x3 int8 convolution with bias and ReLU padded to keep
    the map's size, of a shift of 1 or more, reading a graph input [1, B, T, C], C at most LANES_MAX, into a
    [1, B, T, 16] map; None otherwise."""
    if not int8_conv(op, [3, 3], [1, 1, 1, 1], "Relu") or op.inputs[0].name not in graph.inputs:
        return None
    x, y = laid_shape(op.inputs[0], info), laid_shape(op.outputs[0], info)
    if len(x) != 4 or x[0] != 1 or x[-1] > LANES_MAX or y != [*x[:-1], CHANNEL_STEP]:
        return None
    shift = conv_shift(op, info)
    return int(x[-1]) if shift is not None and shift >= 1 else None


def band_stem_from(graph, op, info) -> bool:
    """op, when band_stem_lanes takes it, as one BandStem reading the graph input, then its weights laid
    [time tap][band tap][C][16] and its bias; attributes the mac shift and the lanes C."""
    import torch

    lanes = band_stem_lanes(graph, op, info)
    if lanes is None:
        return False
    w = op.inputs[1]
    dense = dense_filters(w.value.cpu().numpy(), CHANNEL_STEP).reshape(CHANNEL_STEP, 3, 3, -1)
    taps = dense.transpose(2, 1, 3, 0)
    if real_inputs([taps]) > lanes:
        return False
    w.value = torch.from_numpy(np.ascontiguousarray(taps[:, :, :lanes])).to(w.value.dtype)
    shift = conv_shift(op, info)
    op.type = BAND_STEM
    quant_type = op.attributes["quant_type"]
    op.attributes.clear()
    op.attributes.update(shift=shift, lanes=lanes, quant_type=quant_type)
    return True


@contextlib.contextmanager
def band_stem() -> Iterator[None]:
    """SwiftF0's stem exported as one BandStem (KEHOACH 3.14 step 2.5): esp-dl runs its three input channels on the
    unaligned kernel. The op lays each input column out 16 bytes a band in internal RAM and sums it on BandBlock's
    kernel with three lanes a tap, each integer as esp-dl gives it. Run after aligned_channels, which pads its
    output to 16 channels. SwiftF0 (KEHOACH 3.11) on board B: the stem took 3.5 ms of a 32.1 ms 16-frame window.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        info = ExporterPatternInfo()
        for op in list(graph.topological_sort()):
            band_stem_from(graph, op, info)
        return graph

    EspdlExporter.prepare_graph = joining
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare
