"""The board probe of the ctc net (E11-T12). Run: python -m srpipe.tasks.command.ctc.probe [--run <run> --row <row>].
It writes what ai_engine/test_apps/unit streams on board B: the first stack's layer and the net, seeded random or the
graph of a row of a trained run's ladder (quant.py), with each step's int8 input and output from the whole-sequence
simulation, and the decision of the default commands (E11-T13).
"""

from __future__ import annotations

import argparse
import json
import math
import struct
from pathlib import Path

import numpy as np
import torch
from torch import nn

from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl
from srpipe.core.config import ML_ROOT, data_paths, load_yaml
from srpipe.export import pack_models
from srpipe.tasks.command import ctc
from srpipe.tasks.command import eval as gate
from srpipe.tasks.command.ctc import quant
from srpipe.tasks.command.ctc.model import encoder
from srpipe.tasks.command.ctc.postproc import ctc_score

PROBE_DIR = ML_ROOT.parent / "firmware" / "components" / "ai_engine" / "test_apps" / "unit" / "main" / "probe"
MODELS_FILE, STREAMS_FILE, DECIDE_FILE = "ctc_models.bin", "ctc_streams.bin", "ctc_decide.bin"
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


def probe_net(graph, name: str, step_hops: int, cfg: dict, work: Path, probe_x: np.ndarray) -> tuple[bytes, bytes]:
    """The .espdl of graph streamed step_hops at a time, and its record: every step's int8 input and output over
    probe_x (1, dims, hops)."""
    dims = probe_x.shape[1]
    io = ptq_espdl.io_of(graph)
    x_int8 = ptq_espdl.to_int8(probe_x, io.input_exponent)
    x = x_int8.astype(np.float32) * np.float32(2.0**io.input_exponent)
    y = ptq_espdl.Simulator(graph)(x)
    y_int8 = ptq_espdl.to_int8(y, io.output_exponent)
    if not np.array_equal(y_int8.astype(np.float32) * np.float32(2.0**io.output_exponent), y):
        raise ValueError(f"{name}: the simulated output is not on the int8 grid of its exponent")
    steps = probe_x.shape[2] // step_hops
    frames_per_step = y.shape[2] // steps
    with esp_ppq_patches.applied(cfg["esp_ppq_patches"]):
        espdl = export_espdl.export(
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


def drawn(cfg: dict, dims: int, count: int) -> list[np.ndarray]:
    """count random inputs (1, dims, probe hops) of the probe's seed, as normalised features stand."""
    p = cfg["probe"]
    rng = np.random.default_rng(p["seed"])
    return [rng.normal(p["input_mean"], p["input_std"], (1, dims, p["hops"])).astype(np.float32) for _ in range(count)]


def test_sentence(cfg: dict, net: gate.Ctc) -> np.ndarray:
    """The first sentence of the run's test files, padded to quant.hops and normalised as the net reads it."""
    hops = cfg["quant"]["hops"]
    listing = sorted(
        (data_paths()["processed"] / "command" / net.cfg["split"]["version"] / "test").glob("*.items.jsonl")
    )[0]
    item = json.loads(listing.read_text(encoding="utf-8").splitlines()[0])
    stem = str(listing).removesuffix(".items.jsonl")
    first, n = item["frame_offset"], min(item["n_frames"], hops)
    x = np.concatenate(
        [np.load(stem + s, mmap_mode="r")[first : first + n] for s in (".features.npy", ".pitch.npy")], 1
    )
    return quant.padded(x, hops, net.mean, net.std)


def probe(cfg: dict, out: Path, work: Path, run: Path | None = None, row: str | None = None) -> tuple[Path, Path, Path]:
    """Write out/ctc_models.bin and out/ctc_streams.bin: the first stack's layer a frame a step, the net a chunk a
    step, work keeping each ONNX and .espdl; and out/ctc_decide.bin, the decision of the default commands (E11-T13).
    With run, the net is the graph of row in its ladder, streaming a test sentence."""
    if cfg["probe"]["hops"] % cfg["chunk_hops"] or cfg["quant"]["hops"] % cfg["chunk_hops"]:
        raise ValueError(f"probe and quant hops must be multiples of chunk_hops {cfg['chunk_hops']}")
    scales, p, rungs = cfg["probe"]["norm_scale"], cfg["probe"], ptq_espdl.ladder(quant.LADDER)
    torch.manual_seed(p["seed"])
    one_layer = draw_norm_scales(encoder.layer(cfg, cfg["model"]["stacks"][0]["kernel"]), *scales)
    *layer_calib, layer_x = drawn(cfg, cfg["model"]["width"], p["calib_sequences"] + 1)
    if run is None:
        torch.manual_seed(p["seed"])
        net = draw_norm_scales(encoder.build(cfg), *scales)
        *net_calib, net_x = drawn(cfg, encoder.n_dims(cfg), p["calib_sequences"] + 1)
    else:
        trained = gate.load_ctc(run)
        net, net_x = trained.model, test_sentence(cfg, trained)
    layer_graph = quant.quantized(one_layer, [torch.from_numpy(c) for c in layer_calib], work / LAYER_ENTRY, rungs)
    built = [(LAYER_ENTRY, probe_net(layer_graph, LAYER_ENTRY, 1, cfg, work, layer_x))]
    if run is None:
        net_graph = quant.quantized(net, [torch.from_numpy(c) for c in net_calib], work / NET_ENTRY, rungs)
    else:
        net_graph = export_espdl.load_native(run / "int8" / row / quant.GRAPH_FILE)
    built.append((NET_ENTRY, probe_net(net_graph, NET_ENTRY, cfg["chunk_hops"], cfg, work, net_x)))
    out.mkdir(parents=True, exist_ok=True)
    image = out / MODELS_FILE
    image.write_bytes(pack_models.pack([pack_models.Entry(name, "espdl", espdl) for name, (espdl, _) in built]))
    streams = out / STREAMS_FILE
    streams.write_bytes(STREAMS_HEAD.pack(STREAMS_MAGIC, len(built)) + b"".join(record for _, (_, record) in built))
    for name, (espdl, _) in built:
        print(f"{name}: {len(espdl)} bytes of .espdl")
    decide = out / DECIDE_FILE
    decide.write_bytes(ctc_score.probe_record(cfg))
    return image, streams, decide


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=PROBE_DIR)
    parser.add_argument("--work", type=Path, default=ML_ROOT / "artifacts" / "command_ctc" / "probe")
    parser.add_argument("--run", type=Path, help="stream a row of this trained run's ladder instead of random weights")
    parser.add_argument("--row", help="the row of <run>/int8/ladder.yaml whose graph streams")
    args = parser.parse_args(argv)
    if (args.run is None) != (args.row is None):
        parser.error("--run and --row go together")
    for path in probe(load_yaml(ctc.CONFIG), args.out, args.work, args.run, args.row):
        print(f"{path}: {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
