"""Host side of espsr_compare (KEHOACH 3.16): ESP-SR's model image, the job of each item as it goes over TCP, and the
results and outputs that come back, written as <variant>.wav beside <item>/board.json.

Every constant of the link comes from main/job_format.h, the grid from gen_grid.h, the model partition from
partitions.csv, the variants from ml/configs/afe/compare.yaml and board B's balance from the file
configs/scenes/device.yaml names, so the board and this file cannot disagree. Standard library and PyYAML only.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import struct
import subprocess
import sys
import wave
import zlib
from dataclasses import dataclass
from pathlib import Path

import yaml

APP = Path(__file__).resolve().parent
REPO = APP.parents[2]
FORMAT = APP / "main" / "job_format.h"
GRID = REPO / "firmware" / "components" / "common" / "include" / "gen_grid.h"
AFE = REPO / "firmware" / "components" / "dsp_afe" / "include" / "gen_afe.h"
PARTITIONS = APP / "partitions.csv"
COMPARE = REPO / "ml" / "configs" / "afe" / "compare.yaml"
DEVICE = REPO / "ml" / "configs" / "scenes" / "device.yaml"
ESP_SR_MODELS = APP / "managed_components" / "espressif__esp-sr" / "model"
BOARD = "board"
INPUT = "input.wav"
COSTS = "board.json"
MODEL_PARTITION = "model"


def define(header: Path, name: str) -> float:
    """The number a #define or enumerator of header gives name."""
    number = r"\(?(-?(?:0x[0-9a-fA-F]+|\d+(?:\.\d+)?))[uf]?\)?"
    match = re.search(rf"^\s*(?:#define\s+{name}\s+|{name}\s*=\s*){number}", header.read_text(), flags=re.MULTILINE)
    if match is None:
        raise KeyError(f"{name} is not in {header.name}")
    return float(int(match.group(1), 16)) if match.group(1).startswith("0x") else float(match.group(1))


def int_define(header: Path, name: str) -> int:
    return int(define(header, name))


JOB_MAGIC = int_define(FORMAT, "ESPSR_JOB_MAGIC")
RESULT_MAGIC = int_define(FORMAT, "ESPSR_RESULT_MAGIC")
VERSION = int_define(FORMAT, "ESPSR_JOB_VERSION")
NAME_BYTES = int_define(FORMAT, "ESPSR_JOB_NAME_BYTES")
MODEL_BYTES = int_define(FORMAT, "ESPSR_JOB_MODEL_BYTES")
VARIANTS_MAX = int_define(FORMAT, "ESPSR_JOB_VARIANTS_MAX")
NO_SOURCE = int_define(FORMAT, "ESPSR_JOB_NO_SOURCE")
AFE_CHANNEL = int_define(FORMAT, "ESPSR_JOB_AFE_CHANNEL")
ACK = bytes([int_define(FORMAT, "ESPSR_JOB_ACK")])
REFUSED = int_define(FORMAT, "ESPSR_JOB_REFUSED")
KINDS = {k: int_define(FORMAT, f"ESPSR_JOB_KIND_{k.upper()}") for k in ("dsp_afe", "bss", "webrtc", "nsnet")}
N_BINS = int_define(GRID, "GEN_GRID_N_BINS")
FS = int_define(GRID, "GEN_GRID_SAMPLE_RATE_HZ")
NS_FLOOR_DB = define(AFE, "GEN_AFE_NS_FLOOR_DB")
SPATIAL = {"none": 0, "gsc": 1}
VARIANT = struct.Struct(f"<{NAME_BYTES}s{MODEL_BYTES}sIiIIfii")
JOB_HEAD = struct.Struct(f"<IIIIII{NAME_BYTES}s{2 * N_BINS}f")
RESULT = struct.Struct("<IIIiIIIIIIi")
RESULT_FIELDS = ("magic", "job", "index", "status", "crc32", "samples", "us_mean", "us_peak", "internal_bytes",
                 "psram_bytes", "channel")  # fmt: skip


@dataclass(frozen=True)
class Variant:
    """One board variant of compare.yaml as the job carries it; source names the variant it reads, None the input."""

    name: str
    kind: str
    source: str | None = None
    spatial: str = "none"
    ns_on: bool = False
    ns_floor_db: float = NS_FLOOR_DB
    level: int = 0
    model: str = ""
    channel: int = AFE_CHANNEL


def board_variants(cfg: dict) -> list[Variant]:
    """The variants of compare.yaml made on the board, in file order, each source ahead of what reads it."""
    out: list[Variant] = []
    for name, spec in cfg["variants"].items():
        if spec.get("by") != BOARD:
            continue
        if "spatial" in spec:
            floor = spec.get("ns_floor_db", NS_FLOOR_DB)
            v = Variant(name, "dsp_afe", spatial=spec["spatial"], ns_on=spec["ns"] == "omlsa", ns_floor_db=floor)
        elif spec.get("espsr") == "bss":
            v = Variant(name, "bss", channel=spec.get("channel", AFE_CHANNEL))
        elif spec["ns"] == "espsr_webrtc":
            v = Variant(name, "webrtc", source=spec["after"], level=spec["level"])
        else:
            v = Variant(name, "nsnet", source=spec["after"], model=spec["model"])
        if v.source is not None and v.source not in {o.name for o in out}:
            raise ValueError(f"{name} reads {v.source}, which no board variant ahead of it makes")
        out.append(v)
    if len(out) > VARIANTS_MAX:
        raise ValueError(f"{len(out)} board variants, a job carries {VARIANTS_MAX}")
    return out


def read_input(path: Path) -> tuple[bytes, int, int]:
    """Interleaved int16 frames of a wav, its sample count per channel and its channels."""
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != FS or w.getsampwidth() != 2:
            rate, bits = w.getframerate(), 8 * w.getsampwidth()
            raise ValueError(f"{path}: {rate} Hz, {bits} bit; the board takes {FS} Hz int16")
        return w.readframes(w.getnframes()), w.getnframes(), w.getnchannels()


def balance() -> list[float]:
    """Board B's calib/bal as configs/scenes/device.yaml names it: re, im per bin."""
    path = REPO / yaml.safe_load(DEVICE.read_text())["microphone"]["balance"]
    lines = [line for line in path.read_text().splitlines() if line and not line.startswith("#")]
    rows = list(csv.DictReader(lines))
    if len(rows) != N_BINS:
        raise ValueError(f"{path}: {len(rows)} bins, the grid has {N_BINS}")
    return [float(x) for r in rows for x in (r["re"], r["im"])]


def job_bytes(job: int, item: str, samples: int, channels: int, gains: list[float], variants: list[Variant]) -> bytes:
    """The job header of one item; its input follows it on the link."""
    index = {v.name: k for k, v in enumerate(variants)}
    records = [
        VARIANT.pack(
            v.name.encode(),
            v.model.encode(),
            KINDS[v.kind],
            NO_SOURCE if v.source is None else index[v.source],
            SPATIAL[v.spatial],
            int(v.ns_on),
            v.ns_floor_db,
            v.level,
            v.channel,
        )
        for v in variants
    ]
    records += [bytes(VARIANT.size)] * (VARIANTS_MAX - len(variants))
    head = JOB_HEAD.pack(JOB_MAGIC, VERSION, job, samples, channels, len(variants), item.encode(), *gains)
    return head + b"".join(records)


def end_bytes() -> bytes:
    """A job of no variants, which ends the board's session."""
    head = JOB_HEAD.pack(JOB_MAGIC, VERSION, 0, 0, 0, 0, b"", *([0.0] * 2 * N_BINS))
    return head + bytes(VARIANT.size * VARIANTS_MAX)


def parse_result(raw: bytes, job: int, index: int) -> dict:
    """One result record; refuse one of another job or out of order."""
    r = dict(zip(RESULT_FIELDS, RESULT.unpack(raw), strict=True))
    if r["magic"] != RESULT_MAGIC or r["job"] != job or r["index"] != index:
        raise ValueError(f"result of job {r['job']} variant {r['index']}, magic {r['magic']:#x}; want {job}/{index}")
    return r


def write_output(folder: Path, name: str, data: bytes, crc32: int) -> Path:
    """<name>.wav, mono int16, once its bytes match the CRC32 the board took of them."""
    if zlib.crc32(data) != crc32:
        raise ValueError(f"{name}: received with CRC32 {zlib.crc32(data):08x}, the board sent {crc32:08x}")
    path = folder / f"{name}.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(data)
    return path


def record_costs(folder: Path, found: dict[str, dict]) -> None:
    """Merge the board's figures of each variant into <item>/board.json."""
    path = folder / COSTS
    known = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps(known | found, indent=2, sort_keys=True) + "\n")


def partition(name: str) -> tuple[int, int]:
    """Offset and size of a partition of partitions.csv."""
    rows = [r for r in csv.reader(PARTITIONS.read_text().splitlines()) if r and not r[0].lstrip().startswith("#")]
    for row in rows:
        if row[0].strip() == name:
            return int(row[3].strip(), 0), int(row[4].strip(), 0)
    raise KeyError(f"{name} is not in {PARTITIONS.name}")


def model_image(out: Path) -> Path:
    """ESP-SR's nsnet1, nsnet2 and nsnet3 packed by its own pack_model.py into one image for the model partition."""
    # pack_model.py joins its output to the model folder, which leaves an absolute path as it is.
    out = out.resolve()
    staging = out / "nsnet"
    shutil.copytree(ESP_SR_MODELS / "nsnet_model", staging, dirs_exist_ok=True)
    image = out / "srmodels.bin"
    pack = [sys.executable, str(ESP_SR_MODELS / "pack_model.py"), "-m", str(staging), "-o", str(image)]
    subprocess.run(pack, check=True)
    if image.stat().st_size > partition(MODEL_PARTITION)[1]:
        raise ValueError(f"{image}: {image.stat().st_size} bytes, over the model partition")
    return image
