"""Step 1 of the path to the chip (KEHOACH 3.14): a torch network traced to ONNX as ESP-PPQ reads it, held to torch
by onnxruntime before anything is quantised."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from torch import nn

OPSET = 18  # espdl_quantize_torch's


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
