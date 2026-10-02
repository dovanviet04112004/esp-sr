"""Quantise and export the wake TCN (KEHOACH 3.11, 3.14).

probe builds the network of configs/models/wake.yaml with random weights, quantises it with ESP-PPQ, exports it
streamed one hop at a time, and writes what ai_engine/test_apps/unit streams on board B (E11-T10): the model slot
image and the int8 input and output of every hop from the whole-sequence simulation.
Run: python -m srpipe.tasks.wake.quant probe [--out <dir>]
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

import numpy as np
import torch

from srpipe.compress.quant import export_espdl, ptq_espdl
from srpipe.core.config import ML_ROOT, load_device, load_yaml
from srpipe.export import pack_models
from srpipe.tasks.wake import CONFIG
from srpipe.tasks.wake.model.tcn import Tcn

PROBE_DIR = ML_ROOT.parent / "firmware" / "components" / "ai_engine" / "test_apps" / "unit" / "main" / "probe"
PROBE_ENTRY = "probe"
# hops, bands, outputs, input exponent, output exponent; then int8 input (hops x bands), int8 output (hops x outputs).
VECTORS_HEAD = struct.Struct("<4sIIIii")
VECTORS_MAGIC = b"SRST"


def n_bands(cfg: dict) -> int:
    return load_device(cfg["features"])["features"]["n_bands"]


def build(cfg: dict) -> Tcn:
    m = cfg["model"]
    return Tcn(n_bands(cfg), m["channels"], m["kernel"], m["dilations"])


def probe(cfg: dict, out: Path, work: Path) -> tuple[Path, Path]:
    """Write out/models.bin and out/stream.bin from a seeded random network; work keeps the ONNX and the .espdl."""
    p = cfg["probe"]
    torch.manual_seed(p["seed"])
    model = build(cfg).eval()
    rng = np.random.default_rng(p["seed"])
    shape = (1, n_bands(cfg), p["hops"])

    def draw() -> np.ndarray:
        return rng.normal(p["input_mean"], p["input_std"], shape).astype(np.float32)

    calib = [torch.from_numpy(draw()) for _ in range(p["calib_sequences"])]
    graph = ptq_espdl.quantize(model, calib, work, ptq_espdl.ladder("wake"))
    io = ptq_espdl.io_of(graph)
    x_int8 = ptq_espdl.to_int8(draw(), io.input_exponent)
    x = x_int8.astype(np.float32) * np.float32(2.0**io.input_exponent)
    y = ptq_espdl.Simulator(graph)(x)
    y_int8 = ptq_espdl.to_int8(y, io.output_exponent)
    if not np.array_equal(y_int8.astype(np.float32) * np.float32(2.0**io.output_exponent), y):
        raise ValueError("the simulated output is not on the int8 grid of its exponent")
    espdl = export_espdl.export(
        graph, work / "probe.espdl", streaming_input_shape=[*shape[:2], 1], test_input=x[..., :1]
    )
    out.mkdir(parents=True, exist_ok=True)
    image = out / "models.bin"
    image.write_bytes(pack_models.pack([pack_models.Entry(PROBE_ENTRY, "espdl", espdl.read_bytes())]))
    vectors = out / "stream.bin"
    head = VECTORS_HEAD.pack(VECTORS_MAGIC, shape[2], shape[1], y.shape[1], io.input_exponent, io.output_exponent)
    vectors.write_bytes(head + x_int8[0].T.tobytes() + y_int8[0].T.tobytes())
    return image, vectors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("probe", help="write the streaming probe of E11-T10 for ai_engine/test_apps/unit")
    run.add_argument("--out", type=Path, default=PROBE_DIR)
    run.add_argument("--work", type=Path, default=ML_ROOT / "artifacts" / "wake" / "probe")
    args = parser.parse_args(argv)
    cfg = load_yaml(CONFIG)
    for path in probe(cfg, args.out, args.work):
        print(f"{path}: {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
