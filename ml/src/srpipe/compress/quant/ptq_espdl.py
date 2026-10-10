"""Step 2 of the path to the chip (KEHOACH 3.14): ESP-PPQ quantises a network's ONNX on rungs 1 and 2, with the int16
layers rung 3 names, under the chip's power-of-two scales, and simulates the int8 graph as the chip computes it.

The simulation runs whole sequences. A causal network streamed from empty caches must give the same int8 at every
hop, and that equality is what the board is held to (E11-T10).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from torch import nn

from srpipe.compress.quant import esp_ppq_patches, onnx_export
from srpipe.core.config import CONFIGS, deep_merge, load_yaml

TARGET = "esp32s3"
BITS = 8
WIDE_BITS = 16
INT8_MIN, INT8_MAX = -128, 127
LADDER = CONFIGS / "models" / "quant.yaml"


@dataclass(frozen=True)
class Io:
    """The graph's one input and one output with their power-of-two exponents: value = int8 * 2^exponent."""

    input_name: str
    input_exponent: int
    output_name: str
    output_exponent: int


@dataclass(frozen=True)
class Port:
    """One input or output of a graph with the power-of-two exponent its int8 carry: value = int8 * 2^exponent."""

    name: str
    exponent: int


def ladder(branch: str) -> dict:
    """The branch's rungs of configs/models/quant.yaml over the default ones (KEHOACH 3.14)."""
    cfg = load_yaml(LADDER)
    return deep_merge(cfg["default"], cfg["branches"][branch])


def timing_rungs(branch: str) -> dict:
    """The branch's rungs with quant.yaml's timing over them: a graph quick to quantise, whose time on the chip is the
    rungs' own since it depends on the graph's structure alone (KEHOACH 3.14)."""
    return ladder(branch) | load_yaml(LADDER)["timing"]


def setting_of(rungs: dict):
    """The ESP-PPQ setting for esp-dl that rungs describe: equalization, bias correction, calibration, int16 layers."""
    from esp_ppq.api.espdl_interface import get_target_platform
    from esp_ppq.api.setting import QuantizationSettingFactory

    setting = QuantizationSettingFactory.espdl_setting(BITS)
    equalization = rungs["equalization"]
    setting.equalization = equalization is not None
    if equalization is not None:
        setting.equalization_setting.iterations = equalization["iterations"]
        setting.equalization_setting.value_threshold = equalization["value_threshold"]
        setting.equalization_setting.opt_level = equalization["opt_level"]
    setting.bias_correct = rungs["bias_correction"]
    setting.quantize_activation_setting.calib_algorithm = rungs["calibration"]
    for op in rungs["int16_ops"]:
        setting.dispatching_table.append(op, get_target_platform(TARGET, WIDE_BITS))
    return setting


def quantize(model: nn.Module, calib: list[torch.Tensor], work: Path, rungs: dict):
    """The quantised ESP-PPQ graph of model under rungs, calibrated on batches shaped like calib[0]; work keeps the
    ONNX, held to torch on calib[0]."""
    from esp_ppq.api import espdl_quantize_onnx

    first = calib[0].numpy()
    onnx = onnx_export.checked(model, (first,), work / "graph.onnx", rungs["onnx_rtol"])
    return espdl_quantize_onnx(
        onnx_import_file=str(onnx),
        espdl_export_file=str(work / "graph.espdl"),
        calib_dataloader=calib,
        calib_steps=len(calib),
        input_shape=[list(first.shape)],
        target=TARGET,
        num_of_bits=BITS,
        collate_fn=lambda batch: batch.to("cpu"),
        setting=setting_of(rungs),
        device="cpu",
        error_report=False,
        skip_export=True,
        verbose=0,
    )


def quantize_named(
    model: nn.Module,
    calib: list[tuple[torch.Tensor, ...]],
    work: Path,
    rungs: dict,
    inputs: list[str],
    outputs: list[str],
):
    """quantize for a network of several inputs and outputs, exported to ONNX under the names given, so the chip
    finds each tensor by name; calibrated on tuples shaped like calib[0]."""
    from esp_ppq.api import espdl_quantize_onnx

    first = tuple(t.numpy() for t in calib[0])
    onnx = onnx_export.checked(model, first, work / "graph.onnx", rungs["onnx_rtol"], inputs, outputs)
    return espdl_quantize_onnx(
        onnx_import_file=str(onnx),
        espdl_export_file=str(work / "graph.espdl"),
        calib_dataloader=calib,
        calib_steps=len(calib),
        input_shape=[list(t.shape) for t in calib[0]],
        target=TARGET,
        num_of_bits=BITS,
        collate_fn=lambda batch: [t.to("cpu") for t in batch],
        setting=setting_of(rungs),
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


def ends_of(graph) -> tuple[str, object, str, object]:
    """The name and quantisation config of a single-input, single-output graph's input, then of its output."""
    ((in_name, var_in),) = graph.inputs.items()
    ((out_name, var_out),) = graph.outputs.items()
    consumer, producer = var_in.dest_ops[0], var_out.source_op
    in_config = consumer.input_quant_config[consumer.inputs.index(var_in)]
    return in_name, in_config, out_name, producer.output_quant_config[producer.outputs.index(var_out)]


def io_of(graph) -> Io:
    """Input and output of a single-input, single-output graph, with the exponents their int8 carry."""
    in_name, in_config, out_name, out_config = ends_of(graph)
    return Io(in_name, exponent_of(in_config), out_name, exponent_of(out_config))


def io_bits(graph) -> tuple[int, int]:
    """Bits of a single-input, single-output graph's input and output: 16 where a layer at either end went int16."""
    _, in_config, _, out_config = ends_of(graph)
    return in_config.num_of_bits, out_config.num_of_bits


def ports_of(graph) -> tuple[list[Port], list[Port]]:
    """Every input at the exponent its first reader takes and every output at its producer's, in the graph's order."""

    def read(var) -> int:
        reader = var.dest_ops[0]
        return exponent_of(reader.input_quant_config[reader.inputs.index(var)])

    def written(var) -> int:
        producer = var.source_op
        return exponent_of(producer.output_quant_config[producer.outputs.index(var)])

    inputs = [Port(name, read(var)) for name, var in graph.inputs.items()]
    return inputs, [Port(name, written(var)) for name, var in graph.outputs.items()]


def to_int8(x: np.ndarray, exponent: int) -> np.ndarray:
    return np.clip(np.rint(x / 2.0**exponent), INT8_MIN, INT8_MAX).astype(np.int8)


class Simulator:
    """The quantised graph run by ESP-PPQ's executor on whole sequences, under the ESP-PPQ fixes its branch names: the
    numbers the chip computes."""

    def __init__(self, graph, patches: Sequence[str] = ()) -> None:
        from esp_ppq.executor.torch import TorchExecutor

        self.executor, self.patches = TorchExecutor(graph=graph, device="cpu"), list(patches)

    def __call__(self, x: np.ndarray) -> np.ndarray:
        (out,) = self.run(x)
        return out

    def run(self, *xs: np.ndarray) -> list[np.ndarray]:
        """Every output of a graph of several inputs, given in the graph's order."""
        tensors = [torch.from_numpy(np.ascontiguousarray(x, dtype=np.float32)) for x in xs]
        with esp_ppq_patches.applied(self.patches):
            outs = self.executor.forward(inputs=tensors)
        return [out.detach().cpu().numpy() for out in outs]


def same_integers(
    nets: Sequence[nn.Module], calib: list[torch.Tensor], inputs: list[np.ndarray], rungs: dict, patches: Sequence[str]
) -> tuple[int, float]:
    """How many of the simulated outputs of nets, each quantised under rungs and patches on calib, differ from the
    first net's on inputs, and by how much at most: (0, 0.0) when a rewrite between them keeps every integer, the
    check step 2.4 of KEHOACH 3.14's procedure asks of a rewrite of the network."""
    import tempfile

    outs = []
    for net in nets:
        with esp_ppq_patches.applied(patches), tempfile.TemporaryDirectory() as work:
            graph = quantize(net, calib, Path(work), rungs)
        sim = Simulator(graph, patches)
        outs.append(np.stack([sim(x).ravel() for x in inputs]))
    first, rest = outs[0], outs[1:]
    off = sum(int((first != other).sum()) for other in rest)
    return off, max((float(np.abs(first - other).max()) for other in rest), default=0.0)
