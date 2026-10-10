"""The board probe of the speaker branch (E11-T25): a row of <run>/int8/ladder.yaml as ai_engine/test_apps/unit runs it
on board B, the row's graph as .espdl in a model image, and the window ptq kept with its simulated int8 embedding.
Run: python -m srpipe.tasks.speaker.probe --run <run> --row <row> [--out <dir>]"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

import numpy as np

from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl
from srpipe.core.config import ML_ROOT, load_yaml
from srpipe.export import pack_models
from srpipe.tasks.speaker import quant

PROBE_DIR = ML_ROOT.parent / "firmware" / "components" / "ai_engine" / "test_apps" / "unit" / "main" / "probe"
MODELS_FILE, WINDOWS_FILE = "speaker_models.bin", "speaker_windows.bin"
ENTRY = "speaker"
# Timed runs; then entry name, mels, frames, embedding dims, input and output exponents, then the int8 features
# (mels x frames) and the int8 embedding, padded to four bytes.
WINDOWS_HEAD = struct.Struct("<4sI")
WINDOWS_MAGIC = b"SRSV"
RECORD = struct.Struct("<16sIIIii")


def probe(cfg: dict, run: Path, row: str, out: Path) -> tuple[Path, Path]:
    """Write out/speaker_models.bin and out/speaker_windows.bin of the run's row under cfg, the branch's config; the
    .espdl stays beside its graph."""
    patches = cfg["esp_ppq_patches"]
    folder = run / quant.INT8_DIR
    graph = export_espdl.load_native(folder / row / quant.GRAPH_FILE)
    io = ptq_espdl.io_of(graph)
    window = np.load(folder / quant.WINDOW_FILE)
    if window.shape[:2] != (1, 1):
        raise ValueError(f"{window.shape}: the board copies one channel as it lies, esp-dl laying channels last")
    x_int8 = ptq_espdl.to_int8(window, io.input_exponent)
    x = x_int8.astype(np.float32) * np.float32(2.0**io.input_exponent)
    y = ptq_espdl.Simulator(graph, patches)(x)
    y_int8 = ptq_espdl.to_int8(y, io.output_exponent)
    if not np.array_equal(y_int8.astype(np.float32) * np.float32(2.0**io.output_exponent), y):
        raise ValueError(f"{row}: the simulated embedding is not on the int8 grid of its exponent")
    with esp_ppq_patches.applied(patches):
        espdl = export_espdl.export(graph, folder / row / f"{ENTRY}.espdl", test_input=x).read_bytes()
    out.mkdir(parents=True, exist_ok=True)
    image = out / MODELS_FILE
    image.write_bytes(pack_models.pack([pack_models.Entry(ENTRY, "espdl", espdl)]))
    head = WINDOWS_HEAD.pack(WINDOWS_MAGIC, cfg["probe"]["runs"])
    head += RECORD.pack(ENTRY.encode("ascii"), *x.shape[2:], y.size, io.input_exponent, io.output_exponent)
    body = x_int8.tobytes() + y_int8.tobytes()
    windows = out / WINDOWS_FILE
    windows.write_bytes(head + body + b"\0" * (-len(body) % 4))
    print(f"{ENTRY}: {len(espdl)} bytes of .espdl")
    return image, windows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, required=True, help="a run of python -m srpipe.tasks.speaker.quant import")
    parser.add_argument("--row", required=True, help="the row of <run>/int8/ladder.yaml whose graph the board runs")
    parser.add_argument("--out", type=Path, default=PROBE_DIR)
    args = parser.parse_args(argv)
    for path in probe(load_yaml(quant.CONFIG), args.run, args.row, args.out):
        print(f"{path}: {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
