"""Rung 4 of the path to the chip (KEHOACH 3.14): a quantised ESP-PPQ graph trained on through its own fake
quantisation. The graph's ONNX holds the batch it was traced with, so training runs on a graph built for a batch, and
carry moves what it learnt onto the graph of one that the chip runs."""

from __future__ import annotations

import numpy as np
import torch


def float_parameters(graph) -> dict:
    """The graph's float parameters by variable name, as ESP-PPQ's TrainableGraph finds them."""
    from esp_ppq.core import DataType

    return {
        name: var
        for name, var in graph.variables.items()
        if var.is_parameter and DataType.to_torch(var.dtype) == torch.float
    }


class Trainable:
    """A quantised graph whose float parameters learn through ESP-PPQ's executor: the rounding to int8 passes the
    gradient straight through, and every exponent stays as calibrated."""

    def __init__(self, graph, device: str) -> None:
        from esp_ppq.executor.torch import TorchExecutor

        found = float_parameters(graph)
        powers = [name for name, var in found.items() if any(op.type == "Pow" for op in var.dest_ops)]
        if powers:
            raise ValueError(f"{powers} raise to a power: a fixed exponent would learn, fuse its norm first")
        self.graph, self.executor = graph, TorchExecutor(graph=graph, device=device)
        self.parameters = [var.value for var in found.values()]
        for tensor in self.parameters:
            tensor.requires_grad = True

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        (out,) = self.executor.forward_with_gradient(inputs=x)
        return out

    def infer(self, x: torch.Tensor) -> torch.Tensor:
        with torch.no_grad():
            (out,) = self.executor.forward(inputs=x)
        return out

    def freeze(self) -> None:
        """Take the gradient off every parameter, as the graph was before."""
        for tensor in self.parameters:
            tensor.requires_grad = False
            tensor.grad = None


def batch_of(graph) -> int:
    """The batch the graph's input was traced with."""
    (var,) = graph.inputs.values()
    return int(var.shape[0])


def carry(trained, into, x: np.ndarray) -> None:
    """Move trained's float parameters and the scales of its revisable quantisation configs onto into, a graph of the
    same operations built for another batch; refused unless both then give the same output on x, a batch of one."""
    from esp_ppq.executor.torch import TorchExecutor
    from esp_ppq.IR import QuantableOperation

    learnt = float_parameters(trained)
    for name, var in float_parameters(into).items():
        var.value = learnt[name].value.detach().to("cpu").clone()
    for name, op in into.operations.items():
        if not isinstance(op, QuantableOperation):
            continue
        source = trained.operations[name].config
        mine = op.config.input_quantization_config + op.config.output_quantization_config
        theirs = source.input_quantization_config + source.output_quantization_config
        for config, given in zip(mine, theirs, strict=True):
            if config.is_revisable() and given.scale is not None:
                config.scale = given.scale.detach().to("cpu").clone()
                config.offset = given.offset.detach().to("cpu").clone()
    rows = torch.from_numpy(np.repeat(np.ascontiguousarray(x, dtype=np.float32), batch_of(trained), axis=0))
    (want,) = TorchExecutor(graph=trained, device="cpu").forward(inputs=rows)
    (got,) = TorchExecutor(graph=into, device="cpu").forward(inputs=rows[:1].contiguous())
    if not torch.equal(want[:1], got):
        raise ValueError(f"the carried graph differs from the trained one by {float((want[:1] - got).abs().max())}")
