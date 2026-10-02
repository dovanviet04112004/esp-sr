"""Step 4 of the path to the chip (KEHOACH 3.14): a quantised graph written as .espdl, streamed a step at a time when
asked, and as ESP-PPQ's native graph, which a later step reads back without quantising again."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch

from srpipe.compress.quant.ptq_espdl import BITS, TARGET


def cache_each_causal_conv(graph):
    """Register a StreamingCache between each causal convolution and its input, and drop the convolution's pads.

    ESP-PPQ's auto_streaming caches a variable for all its consumers, so the residual Add that reads a block's input
    would get a window of hops instead of the current one; a cache tied to the convolution leaves the Add alone.
    """
    from esp_ppq.parser.espdl.espdl_streaming import StreamingTable, get_conv_cache_window_size, set_conv_new_padding

    table = StreamingTable()
    for op in list(graph.operations.values()):
        if op.type != "Conv":
            continue
        kernel = op.attributes["kernel_shape"][0]
        effective = op.attributes.get("dilations", [1])[0] * (kernel - 1) + 1
        window = get_conv_cache_window_size(op)
        if effective > 1 and window != effective:
            raise ValueError(f"{op.name} pads ahead in time, so it cannot stream: pads {op.attributes.get('pads')}")
        if window > 1:
            source = op.inputs[0].name
            if source in table:
                raise ValueError(f"{source} feeds two convolutions; one cache per variable is all ESP-PPQ keeps")
            table.add(source, op.name, window)
            set_conv_new_padding(op)
    return graph


def export(
    graph,
    out: Path,
    streaming_input_shape: list[int] | None = None,
    test_input: np.ndarray | tuple[np.ndarray, ...] | None = None,
) -> Path:
    """Write out as .espdl, with StreamingCache ahead of every causal convolution when streaming_input_shape gives
    the one-hop input; test_input, of that shape, or a tuple of one array an input, is stored for model->test(). The
    exporter works on its own copy, so graph stays the whole-sequence one the Simulator runs."""
    import esp_ppq.lib as ppq_lib
    from esp_ppq.api.espdl_interface import generate_test_value, get_target_platform

    values = None
    if test_input is not None:
        arrays = test_input if isinstance(test_input, tuple) else (test_input,)
        values = generate_test_value(graph, "cpu", [torch.from_numpy(np.ascontiguousarray(a)) for a in arrays])
    out.parent.mkdir(parents=True, exist_ok=True)
    ppq_lib.Exporter(platform=get_target_platform(TARGET, BITS)).export(
        file_path=str(out),
        graph=graph,
        values_for_test=values,
        export_config=True,
        streaming_input_shape=streaming_input_shape,
        streaming_custom_passes=[cache_each_causal_conv] if streaming_input_shape is not None else None,
    )
    return out


def save_native(graph, out: Path) -> Path:
    """graph as ESP-PPQ's native file: every operation, parameter and quantisation config as the graph holds them."""
    from esp_ppq.parser import NativeExporter

    out.parent.mkdir(parents=True, exist_ok=True)
    NativeExporter().export(file_path=str(out), graph=graph)
    return out


def load_native(path: Path):
    """The graph save_native wrote."""
    from esp_ppq.api import load_native_graph

    return load_native_graph(str(path))
