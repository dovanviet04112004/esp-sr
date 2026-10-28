"""BandTail (espdl_band_tail.cpp): the tail of SwiftF0's confidence branch as one op; and band_stream, the export
patch that makes SwiftF0's band ops stream (KEHOACH 3.11 step 3.2)."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import numpy as np

from ..base import exponent_of, int8_tensor, linked
from .band_block import conv_shift
from .conv_block import dense_filters

BAND_TAIL = "BandTail"
HIDDEN_TAPS = 3
# Frames an op's output trails its input once it streams; BandBlock adds its time kernel less one.
OWN_DELAY = {"BandStem": 1, "BandChain": 0, "BandHead": 1, BAND_TAIL: 1}
PER_FRAME = ("ReduceMean", "Unsqueeze", "Squeeze", "Reshape", "Transpose", "Gather")


def int8_conv1d(op, taps: int, pads: list[int], activation: str) -> bool:
    """op is a 1-D int8 convolution with bias of one group at a stride of 1, undilated, of taps taps, that padding
    and activation."""
    from esp_ppq.IR.quantize import QuantableOperation

    if op.type != "Conv" or not isinstance(op, QuantableOperation) or len(op.inputs) != 3:
        return False
    attrs = op.attributes
    if attrs.get("kernel_shape") != [taps] or attrs.get("pads", [0, 0]) != pads or attrs.get("group", 1) != 1:
        return False
    if attrs.get("strides", [1]) != [1] or attrs.get("dilations", [1]) != [1]:
        return False
    if attrs.get("activation", "Linear") != activation:
        return False
    return all(int8_tensor(c) for c in [*op.input_quant_config[:2], *op.output_quant_config])


def transposed(var, perm: list[int]):
    """The op reading var alone when it is a Transpose of that perm, else None."""
    if len(var.dest_ops) != 1 or var.dest_ops[0].type != "Transpose":
        return None
    op = var.dest_ops[0]
    return op if op.attributes.get("perm") == perm else None


def band_tail_parts(graph, concat, info) -> tuple | None:
    """(transposes, hidden, confidence, gather, peaks, level, shifts) when concat joins, on one exponent along axis
    1, the peaks [1, T, C] of a BandHead through a Transpose with a level [1, 1, T], and a Transpose of the join feeds
    hidden alone, a 1-D int8 convolution of HIDDEN_TAPS taps padded one frame each side with ReLU, whose output
    confidence alone reads, a 1x1 Linear one to one channel, then a Transpose and a Gather of axis 1 giving a graph
    output; shifts each convolution's 1 or more. None otherwise."""
    if concat.type != "Concat" or concat.attributes.get("axis") != 1 or len(concat.inputs) != 2:
        return None
    first, level = concat.inputs
    t_peaks = first.source_op
    if t_peaks is None or t_peaks.type != "Transpose" or t_peaks.attributes.get("perm") != [0, 2, 1]:
        return None
    peaks = t_peaks.inputs[0]
    if peaks.source_op is None or peaks.source_op.type != "BandHead" or len(first.dest_ops) != 1:
        return None
    exponents = {exponent_of(v, info) for v in (peaks, first, level, concat.outputs[0])}
    if len(exponents) != 1 or None in exponents:
        return None
    t_join = transposed(concat.outputs[0], [0, 2, 1])
    if t_join is None or len(t_join.outputs[0].dest_ops) != 1:
        return None
    hidden = t_join.outputs[0].dest_ops[0]
    if not int8_conv1d(hidden, HIDDEN_TAPS, [1, 1], "Relu") or len(hidden.outputs[0].dest_ops) != 1:
        return None
    confidence = hidden.outputs[0].dest_ops[0]
    if not int8_conv1d(confidence, 1, [0, 0], "Linear") or confidence.inputs[1].value.numel() != len(
        hidden.inputs[2].value.flatten()
    ):
        return None
    t_out = transposed(confidence.outputs[0], [0, 2, 1])
    if t_out is None or len(t_out.outputs[0].dest_ops) != 1:
        return None
    gather = t_out.outputs[0].dest_ops[0]
    if gather.type != "Gather" or gather.attributes.get("axis") != 1 or gather.outputs[0].name not in graph.outputs:
        return None
    for v in (concat, t_join, hidden, confidence, t_out):
        if v.outputs[0].name in graph.outputs:
            return None
    shifts = [conv_shift(c, info) for c in (hidden, confidence)]
    if None in shifts or min(shifts) < 1:
        return None
    return [t_peaks, concat, t_join, t_out], hidden, confidence, gather, peaks, level, shifts


def tail_weights(hidden, confidence) -> tuple[np.ndarray, np.ndarray]:
    """hidden's weights as [tap][input][output] and confidence's as [input], from ESP-PPQ's S3 layout of them."""
    w = hidden.inputs[1].value.cpu().numpy()
    outputs = w.shape[-1]
    dense = dense_filters(w, outputs).reshape(outputs, HIDDEN_TAPS, -1)
    return np.ascontiguousarray(dense.transpose(1, 2, 0)), dense_filters(confidence.inputs[1].value.cpu().numpy(), 1)[0]


def band_tail_from(graph, concat, info) -> bool:
    """The tail band_tail_parts finds from concat replaced by one BandTail reading the peaks and the level, then
    the hidden convolution's weights laid by tail_weights and its bias, then the confidence convolution's; it gives
    the Gather's graph output. The Transposes, the Concat, both convolutions and the Gather go, with the maps between
    them."""
    import torch
    from esp_ppq.core import OperationQuantizationConfig
    from esp_ppq.IR.quantize import QuantableOperation

    found = band_tail_parts(graph, concat, info)
    if found is None:
        return False
    transposes, hidden, confidence, gather, peaks, level, shifts = found
    out = gather.outputs[0]
    configs = [transposes[0].input_quant_config[0], concat.input_quant_config[1]]
    params = []
    for conv, laid in zip((hidden, confidence), tail_weights(hidden, confidence), strict=True):
        w, b = conv.inputs[1], conv.inputs[2]
        w.value = torch.from_numpy(laid).to(w.value.dtype)
        for v in (w, b):
            v.dest_ops.remove(conv)
            params.append(v)
        configs += conv.input_quant_config[1:3]
        del conv.inputs[1:]
    out_configs = [gather.output_quant_config[0]]
    gather.outputs.remove(out)
    out.source_op = None
    gone = [*transposes, hidden, confidence, gather]
    middles = [v for op in gone for v in op.outputs]
    for op in gone:
        graph.remove_operation(op)
    for v in middles:
        graph.remove_variable(v)
    attributes = {"shifts": shifts, "quant_type": hidden.attributes["quant_type"]}
    tail = graph.create_operation(op_type=BAND_TAIL, attributes=attributes)
    config = OperationQuantizationConfig(configs, out_configs)
    linked(graph, QuantableOperation(tail, config, hidden.platform), [peaks, level, *params], [out])
    return True


def own_delay(op) -> int | None:
    """Frames op's output trails its input once it streams; None for an op that cannot stream."""
    if op.type == "BandBlock":
        return int(op.attributes["kernel"][1]) - 1
    if op.type in OWN_DELAY:
        return OWN_DELAY[op.type]
    return 0 if op.type in PER_FRAME else None


def stream_delays(graph) -> dict[str, int]:
    """Frames each variable trails the graph input once the band ops stream, BandTail's from its peaks. Refused when
    an op cannot stream or joins unlike delays. Each op of PER_FRAME is taken to read one frame per output frame, as
    in SwiftF0's graph; the streaming probe checks every frame against the window's simulation."""
    delays = {name: 0 for name in graph.inputs}
    for op in graph.topological_sort():
        own = own_delay(op)
        if own is None:
            raise ValueError(f"{op.name}: a {op.type} cannot stream")
        ins = [delays[v.name] for v in op.inputs if not v.is_parameter]
        if op.type != BAND_TAIL and len(set(ins)) != 1:
            raise ValueError(f"{op.name}: joins inputs {ins} frames behind the graph input")
        for v in op.outputs:
            delays[v.name] = ins[0] + own
    return delays


def band_stream_from(graph, info) -> None:
    """The tail of SwiftF0's confidence branch as BandTail, then on every op that streams its delay, and on BandTail
    the lag of its peaks behind its level."""
    for op in list(graph.topological_sort()):
        if op.name in graph.operations:
            band_tail_from(graph, op, info)
    delays = stream_delays(graph)
    for op in graph.operations.values():
        if op.type in OWN_DELAY or op.type == "BandBlock":
            op.attributes["delay"] = delays[op.outputs[0].name]
        if op.type == BAND_TAIL:
            op.attributes["lag"] = delays[op.inputs[0].name] - delays[op.inputs[1].name]


@contextlib.contextmanager
def band_stream() -> Iterator[None]:
    """SwiftF0's band ops exported to stream (KEHOACH 3.11 step 3.2): each takes S new frames a call, keeps the
    columns its time taps reach back to, and gives the frames whose later ones it holds. esp-dl's 1-D convolution of
    the confidence branch keeps nothing between calls, so BandTail takes the tail over. Name it last, after every
    band patch, for the streaming export alone: the window export and its cuts stay as they are.
    """
    from esp_ppq.parser.espdl.espdl_typedef import ExporterPatternInfo
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def streaming(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        band_stream_from(graph, ExporterPatternInfo())
        return graph

    EspdlExporter.prepare_graph = streaming
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare
