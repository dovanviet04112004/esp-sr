"""Step 1 of the path to the chip (KEHOACH 3.14): a torch network traced to ONNX as ESP-PPQ reads it, held to torch
by onnxruntime before anything is quantised."""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import torch
from torch import nn

OPSET = 18  # espdl_quantize_torch's
# What the TorchScript exporter says of itself, of the Slice its Pad reverses its list by, which onnxsim folds, of
# every RNN whatever its batch, and of shapes its tracer cannot prove, which checked holds to torch on real inputs.
LEGACY_EXPORT_NOTES = (
    (DeprecationWarning, "You are using the legacy TorchScript-based ONNX export"),
    (DeprecationWarning, "The feature will be removed"),
    (UserWarning, "Constant folding - Only steps=1 can be constant folded"),
    (UserWarning, "Exporting a model to ONNX with a batch_size other than 1"),
    (torch.jit.TracerWarning, "Converting a tensor to a Python boolean might cause the trace to be incorrect"),
)


def export(
    model: nn.Module,
    shapes: list[list[int]],
    out: Path,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
) -> Path:
    """model traced on zeros of shapes into out with constants folded, as espdl_quantize_torch traces it; inputs and
    outputs name the graph's tensors, else torch names them."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        # ESP-PPQ's patches read the TorchScript exporter's graph, as espdl_quantize_torch exports (KEHOACH 3.14).
        for category, said in LEGACY_EXPORT_NOTES:
            warnings.filterwarnings("ignore", message=f"{said}.*", category=category)
        torch.onnx.export(
            model.eval(),
            tuple(torch.zeros(shape) for shape in shapes),
            str(out),
            input_names=inputs,
            output_names=outputs,
            opset_version=OPSET,
            do_constant_folding=True,
            dynamo=False,
        )
    return out


def gap(model: nn.Module, onnx_file: Path, xs: tuple[np.ndarray, ...]) -> float:
    """Largest difference between onnxruntime on onnx_file and model on xs over every output, as a share of the
    largest magnitude torch gives that output."""
    import onnxruntime

    xs = tuple(np.ascontiguousarray(x, dtype=np.float32) for x in xs)
    session = onnxruntime.InferenceSession(str(onnx_file), providers=["CPUExecutionProvider"])
    ran = session.run(None, {i.name: x for i, x in zip(session.get_inputs(), xs, strict=True)})
    with torch.no_grad():
        want = model.eval()(*(torch.from_numpy(x) for x in xs))
    want = tuple(want) if isinstance(want, (tuple, list)) else (want,)
    tiny = np.finfo(np.float32).tiny
    return max(
        float(np.abs(r - w.numpy()).max() / max(float(np.abs(w.numpy()).max()), tiny))
        for r, w in zip(ran, want, strict=True)
    )


def checked(
    model: nn.Module,
    xs: tuple[np.ndarray, ...],
    out: Path,
    rtol: float,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
) -> Path:
    """export on the shapes of xs, refused when onnxruntime and torch differ on xs by more than rtol of an output."""
    path = export(model, [list(x.shape) for x in xs], out, inputs, outputs)
    found = gap(model, path, xs)
    if found > rtol:
        raise ValueError(f"{out}: onnxruntime differs from torch by {found:.3g} of an output, over {rtol}")
    return path
