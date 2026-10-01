"""Quantise and export the kws DS-CNN (KEHOACH 3.12, 3.14).

probe builds every size of configs/models/command_kws.yaml with seeded random weights, quantises each with ESP-PPQ on
random windows and writes what ai_engine/test_apps/unit runs on board B: one model image with an entry a size, and
per size one int8 window with the simulated int8 logits, so the board checks each size and times one run of it.
Run: python -m srpipe.tasks.command.kws.quant probe [--out <dir>]
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

import numpy as np
import torch

from srpipe.compress.quant import ptq_espdl
from srpipe.core.config import ML_ROOT, load_yaml
from srpipe.export import pack_models
from srpipe.tasks import command
from srpipe.tasks.command import kws
from srpipe.tasks.command.kws.model import dscnn

PROBE_DIR = ML_ROOT.parent / "firmware" / "components" / "ai_engine" / "test_apps" / "unit" / "main" / "probe"
MODELS_FILE, WINDOWS_FILE = "kws_models.bin", "kws_windows.bin"
LADDER = "command_kws"
# Count and timed runs; then a record a size: entry name, hops, dims, classes, input and output exponents, then the
# int8 window (hops x dims) and the int8 logits, padded to four bytes.
WINDOWS_HEAD = struct.Struct("<4sII")
WINDOWS_MAGIC = b"SRKW"
RECORD = struct.Struct("<8sIIIii")


def entry_of(size: str) -> str:
    return f"kws_{size.lower()}"


def probe_size(cfg: dict, size: str, n_classes: int, work: Path) -> tuple[bytes, bytes]:
    """The .espdl of one size with seeded random weights, and its record: one window and the simulated logits."""
    p, window = cfg["probe"], (cfg["window_hops"], kws.n_dims(cfg))
    torch.manual_seed(p["seed"])
    model = dscnn.build(cfg, window, n_classes, size).eval()
    rng = np.random.default_rng(p["seed"])

    def draw() -> np.ndarray:
        return rng.normal(p["input_mean"], p["input_std"], (1, 1, *window)).astype(np.float32)

    calib = [torch.from_numpy(draw()) for _ in range(p["calib_windows"])]
    graph = ptq_espdl.quantize(model, calib, work / size, ptq_espdl.ladder(LADDER))
    io = ptq_espdl.io_of(graph)
    x_int8 = ptq_espdl.to_int8(draw(), io.input_exponent)
    x = x_int8.astype(np.float32) * np.float32(2.0**io.input_exponent)
    y = ptq_espdl.Simulator(graph)(x)
    y_int8 = ptq_espdl.to_int8(y, io.output_exponent)
    if not np.array_equal(y_int8.astype(np.float32) * np.float32(2.0**io.output_exponent), y):
        raise ValueError(f"{size}: the simulated logits are not on the int8 grid of their exponent")
    espdl = ptq_espdl.export(graph, work / size / f"{entry_of(size)}.espdl", test_input=x)
    head = RECORD.pack(entry_of(size).encode("ascii"), *window, n_classes, io.input_exponent, io.output_exponent)
    body = x_int8.tobytes() + y_int8.tobytes()
    return espdl.read_bytes(), head + body + b"\0" * (-len(body) % 4)


def probe(cfg: dict, command_cfg: dict, out: Path, work: Path) -> tuple[Path, Path]:
    """Write out/kws_models.bin and out/kws_windows.bin over every size; work keeps each ONNX and .espdl."""
    n_classes = len(kws.classes(command_cfg, cfg["commands"]))
    sizes = list(cfg["model"]["sizes"])
    built = [probe_size(cfg, size, n_classes, work) for size in sizes]
    out.mkdir(parents=True, exist_ok=True)
    image = out / MODELS_FILE
    entries = [pack_models.Entry(entry_of(s), "espdl", espdl) for s, (espdl, _) in zip(sizes, built, strict=True)]
    image.write_bytes(pack_models.pack(entries))
    windows = out / WINDOWS_FILE
    head = WINDOWS_HEAD.pack(WINDOWS_MAGIC, len(sizes), cfg["probe"]["runs"])
    windows.write_bytes(head + b"".join(record for _, record in built))
    for size, (espdl, _) in zip(sizes, built, strict=True):
        print(f"{entry_of(size)}: {len(espdl)} bytes of .espdl")
    return image, windows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("probe", help="write the size probe of E11-T17 for ai_engine/test_apps/unit")
    run.add_argument("--out", type=Path, default=PROBE_DIR)
    run.add_argument("--work", type=Path, default=ML_ROOT / "artifacts" / "command_kws" / "probe")
    args = parser.parse_args(argv)
    for path in probe(load_yaml(kws.CONFIG), load_yaml(command.CONFIG), args.out, args.work):
        print(f"{path}: {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
