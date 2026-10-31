"""The chip's check of the detect branch (KEHOACH 3.18 step 5), for ai_engine's test app with the detect profile: the
lock's vision image for the test table's models_0, the same image with one .espdl byte flipped for voice, eight val
pictures as RGB888 for models_1, and what the chip must give on each: the CRC32 of the six one-to-one outputs in
esp-dl's channels-last layout, from ESP-PPQ's simulation of the int8 graph on the chip's letterbox under the run's
esp_ppq_patches, and decode's boxes.
Run on the CPU: python -m srpipe.tasks.detect.probe"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import tempfile
import zlib
from pathlib import Path

import numpy as np

from srpipe.compress.quant.ptq_espdl import exponent_of
from srpipe.core.config import ML_ROOT, data_paths, load_yaml
from srpipe.export import pack_models
from srpipe.tasks.detect import data, pack
from srpipe.tasks.detect.postproc import decode

PROBE_DIR = ML_ROOT.parent / "firmware" / "components" / "ai_engine" / "test_apps" / "unit" / "main" / "probe"
RUNS = ML_ROOT / "artifacts" / "detect" / "runs"
SEED = 20261030
PICTURES = 8
WHOLE = 2  # kept at their own size; the rest shrunk to fit models_1
SMALL_SIDE = 256
NAME_BYTES = 32
FIELDS = 6  # x1 y1 x2 y2 class score_permille
HEAD = struct.Struct("<4sIII")  # magic, pictures, outputs, keep
OUTPUT = struct.Struct(f"<{NAME_BYTES}sIiI")  # name, elements, exponent, bytes
PICTURE = struct.Struct("<IIII")  # width, height, offset into models_1, boxes
SLOTS = {"vision": "models_0", "broken": "voice", "pictures": "models_1"}
HEADS = ("one2one_p3", "one2one_p4", "one2one_p5")


def pictures_of(found: data.Package) -> list[np.ndarray]:
    """PICTURES val images drawn by the seed: the first WHOLE at their own size, the rest shrunk by nearest neighbour to
    SMALL_SIDE on the longer side, which the chip's letterbox then grows back."""
    from PIL import Image

    pool = found.images["val"]
    picks = np.random.default_rng(SEED).choice(len(pool), size=PICTURES, replace=False)
    out = []
    for i, k in enumerate(picks):
        rgb = data.rgb_of(pool[k])
        if i >= WHOLE:
            h, w = rgb.shape[:2]
            scale = SMALL_SIDE / max(h, w)
            size = (max(1, round(w * scale)), max(1, round(h * scale)))
            rgb = np.asarray(Image.fromarray(rgb).resize(size, Image.Resampling.NEAREST), dtype=np.uint8)
        out.append(np.ascontiguousarray(rgb))
    return out


def outputs_of(executor, graph, square: np.ndarray) -> list[tuple[str, np.ndarray, int]]:
    """The six outputs on one letterboxed square, in the graph's order: name, integers channels last (grid, grid, c)
    at the producer's bits, and the exponent."""
    import torch

    values = executor.forward(torch.from_numpy(data.input_of(square)))
    out = []
    for (name, var), value in zip(graph.outputs.items(), values, strict=True):
        producer = var.source_op
        config = producer.output_quant_config[producer.outputs.index(var)]
        exponent = exponent_of(config)
        dtype = np.int8 if config.num_of_bits == 8 else np.int16
        q = np.rint(value.detach().double().numpy()[0] / 2.0**exponent).astype(dtype)
        out.append((name, np.ascontiguousarray(q.transpose(1, 2, 0)), exponent))
    return out


def boxes_of(outs: list[tuple[str, np.ndarray, int]], row: dict, width: int, height: int) -> np.ndarray:
    named = {name: (q, e) for name, q, e in outs}
    levels = []
    for level, stride in zip(HEADS, decode.STRIDES, strict=True):
        (box, box_e), (cls, cls_e) = named[f"{level}_box"], named[f"{level}_cls"]
        levels.append(decode.Level(box, cls, box_e, cls_e, stride))
    chip = row["chip"]
    frame = decode.frame_of(width, height, row["size"])
    return decode.decode(levels, len(row["names"]), chip["score_permille"], frame, chip["top_k"], pack.TOP_K_MAX)


def block_output(graph, block: int) -> str:
    """The variable the last op of /model.<block>/ writes, in the graph's order."""
    last = [op for op in graph.topological_sort() if op.name.startswith(f"/model.{block}/")]
    if not last:
        raise ValueError(f"no op of /model.{block}/ in the graph")
    return last[-1].outputs[0].name


def reexported(graph, taps: list[int], cfg: dict, row: dict, folder: Path) -> bytes:
    """The vision image of graph exported afresh under the patches in force, each tap block's output added as a graph
    output: a diagnostic image that finds the first output the chip computes otherwise, or times export patches."""
    from srpipe.compress.quant import export_espdl

    for block in taps:
        name = block_output(graph, block)
        graph.outputs[name] = graph.variables[name]
    export_espdl.export(graph, folder / "probe.espdl", int16_lut_step=cfg["quant"]["lut_step"])
    espdl = (folder / "probe.espdl").read_bytes()
    chip = row["chip"]
    return pack.image(espdl, f"{row['run'][:28]}+x", row["size"], row["names"], chip["score_permille"], chip["top_k"])


def row_of(run: Path) -> dict:
    """The fields of a lock row the probe reads, for a run not deployed: its config's S, letterbox fill and chip
    fields, model.pt's class names."""
    cfg = load_yaml(run / "config.resolved.yaml")
    meta = json.loads((run / "model.onnx.json").read_text(encoding="utf-8"))
    return {"run": run.name, "size": cfg["size"], "pad": cfg["pad"], "names": meta["names"], "chip": cfg["chip"]}


def build(
    out: Path, taps: list[int], patches: list[str], unlocked: Path | None = None, drop: list[str] | None = None
) -> dict[str, Path]:
    """Write detect_vision.bin, detect_broken.bin, detect_pictures.bin and detect_expect.bin under out, for the lock's
    detect row or for the run unlocked; with taps, the vision image and the expectations carry those blocks' outputs
    too; with patches, export patches beyond the run's, which keep every integer, the image is exported under them;
    drop leaves those of the run's export patches out of the image, ESP-PPQ's simulation keeping them all."""
    from esp_ppq.executor import TorchExecutor

    from srpipe.compress.quant import esp_ppq_patches, export_espdl

    if unlocked is None:
        row = json.loads(pack_models.LOCK.read_text(encoding="utf-8"))["models"][pack.BRANCH]
        run = RUNS / row["run"]
        (entry,) = row["files"]
        if hashlib.sha256((run / "int8" / "model.espdl").read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"{run}: int8/model.espdl is not the .espdl the lock holds, so graph.native is not either")
        vision = pack.locked()
    else:
        run, row = unlocked, row_of(unlocked)
        chip = row["chip"]
        espdl = (run / "int8" / "model.espdl").read_bytes()
        vision = pack.image(espdl, row["run"], row["size"], row["names"], chip["score_permille"], chip["top_k"])
    broken = bytearray(vision)
    broken[pack.HEADER_BYTES + (len(vision) - pack.HEADER_BYTES) // 2] ^= 0xFF
    cfg = load_yaml(run / "config.resolved.yaml")
    found = data.package(data_paths()["interim"] / cfg["package"])
    graph = export_espdl.load_native(run / "int8" / "graph.native")
    keep = row["chip"]["top_k"]
    blob, records, tables, shapes = bytearray(), [], [], None
    extra = [p for p in patches if p not in cfg["esp_ppq_patches"]]
    kept = [p for p in cfg["esp_ppq_patches"] if p not in (drop or [])]
    if taps or extra or kept != cfg["esp_ppq_patches"]:
        with tempfile.TemporaryDirectory() as folder, esp_ppq_patches.applied(kept + extra):
            vision = reexported(graph, taps, cfg, row, Path(folder))
    with esp_ppq_patches.applied(cfg["esp_ppq_patches"]):
        executor = TorchExecutor(graph=graph, device="cpu")
        for rgb in pictures_of(found):
            square, _ = data.letterbox(rgb, row["size"], row["pad"])
            outs = outputs_of(executor, graph, square)
            shapes = shapes or [(name, q.size, e, q.dtype.itemsize) for name, q, e in outs]
            boxes = boxes_of(outs, row, rgb.shape[1], rgb.shape[0])
            table = np.zeros((keep, FIELDS), np.int16)
            table[: len(boxes)] = boxes
            records.append(
                PICTURE.pack(rgb.shape[1], rgb.shape[0], len(blob), len(boxes))
                + struct.pack(f"<{len(outs)}I", *(zlib.crc32(q.tobytes()) for _, q, _ in outs))
            )
            tables.append(table.tobytes())
            blob += rgb.tobytes()
    if len(blob) > pack_models.partition_bytes(SLOTS["pictures"], pack_models.UNIT_PARTITIONS):
        raise ValueError(f"{len(blob)} bytes of pictures overflow {SLOTS['pictures']}")
    expect = HEAD.pack(b"SRDE", PICTURES, len(shapes), keep)
    expect += b"".join(OUTPUT.pack(n.encode("ascii"), size, e, b) for n, size, e, b in shapes)
    expect += b"".join(records) + b"".join(tables)
    files = {
        "vision": out / "detect_vision.bin",
        "broken": out / "detect_broken.bin",
        "pictures": out / "detect_pictures.bin",
        "expect": out / "detect_expect.bin",
    }
    out.mkdir(parents=True, exist_ok=True)
    for key, payload in (("vision", vision), ("broken", bytes(broken)), ("pictures", blob), ("expect", expect)):
        files[key].write_bytes(bytes(payload))
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=PROBE_DIR)
    parser.add_argument("--taps", type=int, nargs="*", default=[], help="blocks whose outputs the chip reports too")
    parser.add_argument("--patches", nargs="*", default=[], help="export patches beyond the run's for the image")
    parser.add_argument("--run", type=Path, default=None, help="a run to probe in place of the lock's detect row")
    parser.add_argument("--drop", nargs="*", default=[], help="export patches of the run the image leaves out")
    args = parser.parse_args(argv)
    for key, path in build(args.out, args.taps, args.patches, args.run, args.drop).items():
        print(f"{path}: {path.stat().st_size} bytes{f' for {SLOTS[key]}' if key in SLOTS else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
