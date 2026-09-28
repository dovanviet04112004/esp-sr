"""ESP-PPQ for esp-dl (KEHOACH 3.14): quantise a torch network to int8 under the chip's power-of-two scales, simulate
it as the chip computes, and export .espdl, streamed one hop at a time when asked.

The simulation runs whole sequences. A causal network streamed from empty caches must give the same int8 at every
hop, and that equality is what the board is held to (E11-T10).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from torch import nn

TARGET = "esp32s3"
BITS = 8
INT8_MIN, INT8_MAX = -128, 127


@dataclass(frozen=True)
class Io:
    """The graph's one input and one output with their power-of-two exponents: value = int8 * 2^exponent."""

    input_name: str
    input_exponent: int
    output_name: str
    output_exponent: int


def quantize(model: nn.Module, calib: list[torch.Tensor], work: Path):
    """The quantised ESP-PPQ graph of model, calibrated on batches of one shaped like calib[0]; work keeps the ONNX."""
    from esp_ppq.api import espdl_quantize_torch

    work.mkdir(parents=True, exist_ok=True)
    return espdl_quantize_torch(
        model=model.eval(),
        espdl_export_file=str(work / "graph.espdl"),
        calib_dataloader=calib,
        calib_steps=len(calib),
        input_shape=list(calib[0].shape),
        target=TARGET,
        num_of_bits=BITS,
        collate_fn=lambda batch: batch.to("cpu"),
        device="cpu",
        error_report=False,
        skip_export=True,
        verbose=0,
    )


def exponent_of(config) -> int:
    """log2 of a tensor quantisation scale, which esp-dl requires to be a power of two."""
    scale = float(config.scale)
    exponent = round(math.log2(scale))
    if 2.0**exponent != scale:
        raise ValueError(f"scale {scale} is not a power of two")
    return exponent


def io_of(graph) -> Io:
    """Input and output of a single-input, single-output graph, with the exponents their int8 carry."""
    ((in_name, var_in),) = graph.inputs.items()
    ((out_name, var_out),) = graph.outputs.items()
    consumer, producer = var_in.dest_ops[0], var_out.source_op
    in_config = consumer.input_quant_config[consumer.inputs.index(var_in)]
    out_config = producer.output_quant_config[producer.outputs.index(var_out)]
    return Io(in_name, exponent_of(in_config), out_name, exponent_of(out_config))


def to_int8(x: np.ndarray, exponent: int) -> np.ndarray:
    return np.clip(np.rint(x / 2.0**exponent), INT8_MIN, INT8_MAX).astype(np.int8)


class Simulator:
    """The quantised graph run by ESP-PPQ's executor on whole sequences: the numbers the chip computes."""

    def __init__(self, graph) -> None:
        from esp_ppq.executor.torch import TorchExecutor

        self.executor = TorchExecutor(graph=graph, device="cpu")

    def __call__(self, x: np.ndarray) -> np.ndarray:
        (out,) = self.executor.forward(inputs=torch.from_numpy(np.ascontiguousarray(x, dtype=np.float32)))
        return out.detach().cpu().numpy()


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
    graph, out: Path, streaming_input_shape: list[int] | None = None, test_input: np.ndarray | None = None
) -> Path:
    """Write out as .espdl, with StreamingCache ahead of every causal convolution when streaming_input_shape gives
    the one-hop input; test_input, of that shape, is stored for model->test(). The exporter works on its own copy,
    so graph stays the whole-sequence one the Simulator runs."""
    import esp_ppq.lib as ppq_lib
    from esp_ppq.api.espdl_interface import generate_test_value, get_target_platform

    values = None
    if test_input is not None:
        values = generate_test_value(graph, "cpu", [torch.from_numpy(np.ascontiguousarray(test_input))])
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
