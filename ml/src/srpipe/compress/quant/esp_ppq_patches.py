"""Patches to ESP-PPQ 1.3.11's esp-dl export, applied around an export by the branch whose config names them.

Only configs/models/command_ctc.yaml names any; every other branch exports exactly as ESP-PPQ does. Each patch states
the bug, what board B showed without it and the test that pins it (tests/test_esp_ppq_patches.py); when the ESP-PPQ
pin moves, re-check each one upstream.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Sequence

# esp-dl runs convolutions channels last, so time, the first spatial axis of every conv streamed here, is axis 1.
CONV_TIME_AXIS = 1


def requantise_graph_input_readers(op, graph) -> None:
    """Requantise each graph input op reads at another scale than the input's first reader, whose scale esp-dl gives
    the input."""
    import torch
    from esp_ppq.IR.quantize import QuantableOperation
    from esp_ppq.parser.espdl.espdl_graph_utils import insert_requantize_node
    from esp_ppq.parser.espdl.espdl_typedef import QUANT_OP_SET, ExporterPatternInfo
    from esp_ppq.parser.espdl.export_patterns import EspdlQuantHelper

    def exportable(config, var) -> bool:
        return EspdlQuantHelper.TQC_Exportable_Check(TQC=config, bounded_var=var)

    if not isinstance(op, QuantableOperation) or op.type in QUANT_OP_SET:
        return
    for config, var in list(zip(op.input_quant_config, op.inputs, strict=True)):
        if var.name not in graph.inputs or var.source_op is not None or not exportable(config, var):
            continue
        readers = [o for o in graph.topological_sort() if isinstance(o, QuantableOperation) and var in o.inputs]
        pairs = (pair for o in readers for pair in zip(o.input_quant_config, o.inputs, strict=True))
        upstream = next(c for c, v in pairs if v is var and exportable(c, v))
        if upstream.num_of_bits == config.num_of_bits and torch.equal(upstream.scale, config.scale):
            continue
        created = insert_requantize_node(graph=graph, var=var, upstream_config=upstream, config=config, op=op)
        info = ExporterPatternInfo()
        if perm := info.get_var_permute(var.name):
            info.add_var_permute(created.outputs[0].name, perm)


@contextlib.contextmanager
def requantise_graph_inputs() -> Iterator[None]:
    """InsertRequantNodePattern requantises only variables an op produces, so a graph input two readers take at two
    scales reaches esp-dl at the first reader's, and an Add or Sub aligned to its output adds integers of two scales.
    Board B, ctc_lay (the layer reads its input in a Conv, an Add and the bypass Sub): model->test() off by 8 steps.
    """
    from esp_ppq.parser import espdl_exporter

    base = espdl_exporter.InsertRequantNodePattern

    class Pattern(base):
        def export(self, op, graph, **kwargs):
            op = super().export(op, graph, **kwargs)
            requantise_graph_input_readers(op, graph)
            return op

    espdl_exporter.InsertRequantNodePattern = Pattern
    try:
        yield
    finally:
        espdl_exporter.InsertRequantNodePattern = base


@contextlib.contextmanager
def conv_caches_along_time() -> Iterator[None]:
    """A cache registered without a frame axis takes the graph input's, which is the channel axis of a convolution's
    tensor once the input reaches the convolutions through a layout-agnostic op; this gives such caches time.
    Board B, ctc_net (a Slice feeds the front's 2D convolutions): esp-dl asserts in Reshape while building.
    """
    from esp_ppq.parser.espdl.espdl_streaming import StreamingTable

    add = StreamingTable.add

    def along_time(self, var_name, op_name, window_size, frame_axis=None):
        return add(self, var_name, op_name, window_size, CONV_TIME_AXIS if frame_axis is None else frame_axis)

    StreamingTable.add = along_time
    try:
        yield
    finally:
        StreamingTable.add = add


PATCHES = {"requantise_graph_inputs": requantise_graph_inputs, "conv_caches_along_time": conv_caches_along_time}


@contextlib.contextmanager
def applied(names: Sequence[str]) -> Iterator[None]:
    """The named patches, in force for the length of the block."""
    unknown = sorted(set(names) - set(PATCHES))
    if unknown:
        raise ValueError(f"unknown ESP-PPQ patches {unknown}; known: {sorted(PATCHES)}")
    with contextlib.ExitStack() as stack:
        for name in names:
            stack.enter_context(PATCHES[name]())
        yield
