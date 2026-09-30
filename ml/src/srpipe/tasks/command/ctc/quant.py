"""Quantise and export the ctc net (KEHOACH 3.12, 3.14, ADR-0013). Run: python -m srpipe.tasks.command.ctc.quant probe

probe builds one encoder layer and the whole net of configs/models/command_ctc.yaml with seeded random weights,
quantises each with ESP-PPQ, exports the layer streamed a frame at a time and the net a chunk at a time, and writes
what ai_engine/test_apps/unit streams on board B (E11-T12): one model image and, a record a net, the int8 input and
the int8 output of every step from the whole-sequence simulation.
"""

from __future__ import annotations

import argparse
import math
import struct
from pathlib import Path

import numpy as np
import torch
from torch import nn

from srpipe.compress.quant import esp_ppq_patches, ptq_espdl
from srpipe.core.config import ML_ROOT, load_yaml
from srpipe.export import pack_models
from srpipe.tasks.command import ctc
from srpipe.tasks.command.ctc.model import encoder

PROBE_DIR = ML_ROOT.parent / "firmware" / "components" / "ai_engine" / "test_apps" / "unit" / "main" / "probe"
MODELS_FILE, STREAMS_FILE = "ctc_models.bin", "ctc_streams.bin"
LADDER = "command_ctc"
LAYER_ENTRY, NET_ENTRY = "ctc_lay", "ctc_net"
# Count; then a record a net: entry name, hops a step, input dims, outputs a step, steps, input and output exponents,
# then each step's int8 input, then each step's int8 output, in esp-dl's layout of each, padded to four bytes.
STREAMS_HEAD = struct.Struct("<4sI")
STREAMS_MAGIC = b"SRCT"
RECORD = struct.Struct("<8sIIIIii")
# ESP-PPQ's helper.save heads an .espdl with "EDL2", the encryption flag, the length and four pad bytes.
ESPDL_HEAD_BYTES = 16


def stored_test(espdl: Path) -> tuple[tuple[tuple[int, ...], bytes], tuple[tuple[int, ...], bytes]]:
    """The test input and output ESP-PPQ stored in an .espdl for model->test(), as dims and int8 bytes, both in the
    layout esp-dl holds the tensors in."""
    from esp_ppq.parser.espdl.FlatBuffers.Dl.Model import Model

    graph = Model.GetRootAs(espdl.read_bytes()[ESPDL_HEAD_BYTES:], 0).Graph()

    def dims_and_bytes(tensor) -> tuple[tuple[int, ...], bytes]:
        dims = tuple(int(d) for d in tensor.DimsAsNumpy())
        raw = b"".join(tensor.RawData(j).BytesAsNumpy().tobytes() for j in range(tensor.RawDataLength()))
        return dims, raw[: math.prod(dims)]

    return dims_and_bytes(graph.TestInputsValue(0)), dims_and_bytes(graph.TestOutputsValue(0))


def step_perm(channels: int, per_step: int, dims: tuple[int, ...]) -> tuple[int, ...]:
    """The axes that take a step from ONNX's (1, channels, time) to esp-dl's dims of it: kept, or time first."""
    if channels == per_step and channels > 1:
        raise ValueError(f"a step of {per_step} x {channels} reads the same in both layouts")
    for perm in ((0, 1, 2), (0, 2, 1)):
        if tuple((1, channels, per_step)[a] for a in perm) == dims:
            return perm
    raise ValueError(f"esp-dl holds a (1, {channels}, {per_step}) step as {dims}")


def steps_in_layout(x: np.ndarray, per_step: int, perm: tuple[int, ...]) -> bytes:
    """x (1, channels, time) as the int8 of one step after another, each with its axes in perm's order."""
    steps = (x[..., s : s + per_step] for s in range(0, x.shape[-1], per_step))
    return b"".join(np.ascontiguousarray(step.transpose(perm)).tobytes() for step in steps)


def probe_net(model: nn.Module, name: str, dims: int, step_hops: int, cfg: dict, work: Path) -> tuple[bytes, bytes]:
    """The .espdl of model streamed step_hops at a time, and its record: every step's int8 input and output."""
    p = cfg["probe"]
    rng = np.random.default_rng(p["seed"])
    shape = (1, dims, p["hops"])

    def draw() -> np.ndarray:
        return rng.normal(p["input_mean"], p["input_std"], shape).astype(np.float32)

    calib = [torch.from_numpy(draw()) for _ in range(p["calib_sequences"])]
    graph = ptq_espdl.quantize(model.eval(), calib, work / name, ptq_espdl.ladder(LADDER))
    norms = sum(isinstance(m, encoder.ScalarRmsNorm) for m in model.modules())
    fused = sum(op.type == "RMSNormalization" for op in graph.operations.values())
    if fused != norms:
        raise ValueError(f"{name}: ESP-PPQ fused {fused} of {norms} norms into esp-dl's RMSNormalization")
    io = ptq_espdl.io_of(graph)
    x_int8 = ptq_espdl.to_int8(draw(), io.input_exponent)
    x = x_int8.astype(np.float32) * np.float32(2.0**io.input_exponent)
    y = ptq_espdl.Simulator(graph)(x)
    y_int8 = ptq_espdl.to_int8(y, io.output_exponent)
    if not np.array_equal(y_int8.astype(np.float32) * np.float32(2.0**io.output_exponent), y):
        raise ValueError(f"{name}: the simulated output is not on the int8 grid of its exponent")
    steps = shape[2] // step_hops
    frames_per_step = y.shape[2] // steps
    with esp_ppq_patches.applied(cfg["esp_ppq_patches"]):
        espdl = ptq_espdl.export(
            graph,
            work / name / f"{name}.espdl",
            streaming_input_shape=[1, dims, step_hops],
            test_input=x[..., :step_hops],
        )
    (in_dims, in_test), (out_dims, out_test) = stored_test(espdl)
    x_steps = steps_in_layout(x_int8, step_hops, step_perm(dims, step_hops, in_dims))
    y_steps = steps_in_layout(y_int8, frames_per_step, step_perm(y.shape[1], frames_per_step, out_dims))
    # ESP-PPQ ran the first step alone for model->test(): equal bytes prove the layout and the int8 graph's causality.
    if x_steps[: len(in_test)] != in_test or y_steps[: len(out_test)] != out_test:
        raise ValueError(f"{name}: the first step differs from the test values ESP-PPQ stored for model->test()")
    outputs = frames_per_step * y.shape[1]
    head = RECORD.pack(name.encode("ascii"), step_hops, dims, outputs, steps, io.input_exponent, io.output_exponent)
    body = x_steps + y_steps
    return espdl.read_bytes(), head + body + b"\0" * (-len(body) % 4)


def draw_norm_scales(model: nn.Module, low: float, high: float) -> nn.Module:
    """Norm scales drawn as training leaves them: a scale of exactly one drops out of the ONNX graph, and ESP-PPQ then
    leaves the norm an int8 chain instead of fusing it."""
    for norm in (m for m in model.modules() if isinstance(m, encoder.ScalarRmsNorm)):
        nn.init.uniform_(norm.scale, low, high)
    return model


def probe(cfg: dict, out: Path, work: Path) -> tuple[Path, Path]:
    """Write out/ctc_models.bin and out/ctc_streams.bin: the first stack's layer a frame a step, the net a chunk a
    step; work keeps each ONNX and .espdl."""
    if cfg["probe"]["hops"] % cfg["chunk_hops"]:
        raise ValueError(f"probe hops {cfg['probe']['hops']} are not a multiple of chunk_hops {cfg['chunk_hops']}")
    scales = cfg["probe"]["norm_scale"]
    torch.manual_seed(cfg["probe"]["seed"])
    first = cfg["model"]["stacks"][0]
    one_layer = draw_norm_scales(encoder.layer(cfg, first["kernel"]), *scales)
    torch.manual_seed(cfg["probe"]["seed"])
    net = draw_norm_scales(encoder.build(cfg), *scales)
    built = [
        (LAYER_ENTRY, probe_net(one_layer, LAYER_ENTRY, cfg["model"]["width"], 1, cfg, work)),
        (NET_ENTRY, probe_net(net, NET_ENTRY, encoder.n_dims(cfg), cfg["chunk_hops"], cfg, work)),
    ]
    out.mkdir(parents=True, exist_ok=True)
    image = out / MODELS_FILE
    image.write_bytes(pack_models.pack([pack_models.Entry(name, "espdl", espdl) for name, (espdl, _) in built]))
    streams = out / STREAMS_FILE
    streams.write_bytes(STREAMS_HEAD.pack(STREAMS_MAGIC, len(built)) + b"".join(record for _, (_, record) in built))
    for name, (espdl, _) in built:
        print(f"{name}: {len(espdl)} bytes of .espdl")
    return image, streams


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("probe", help="write the streaming probe of E11-T12 for ai_engine/test_apps/unit")
    run.add_argument("--out", type=Path, default=PROBE_DIR)
    run.add_argument("--work", type=Path, default=ML_ROOT / "artifacts" / "command_ctc" / "probe")
    args = parser.parse_args(argv)
    for path in probe(load_yaml(ctc.CONFIG), args.out, args.work):
        print(f"{path}: {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
