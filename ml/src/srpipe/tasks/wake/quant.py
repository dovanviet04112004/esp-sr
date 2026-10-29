"""Quantise and export the wake TCN (KEHOACH 3.11, 3.14): probe streams a seeded random network a few hops a call
for ai_engine/test_apps/unit (E11-T10), every hop's int8 input and output, and as an image's wake for the branch;
ptq rows each calibration of a trained run, scored as the chip scores; deploy exports a row into firmware/models.
Run: python -m srpipe.tasks.wake.quant probe [--step <hops a call>] | ptq <run> | deploy <run> --row <row>
"""

from __future__ import annotations

import argparse
import shutil
import struct
from pathlib import Path

import numpy as np
import torch
import yaml

from srpipe.compress.quant import export_espdl, ptq_espdl
from srpipe.core.config import ML_ROOT, data_paths, device_of, load_run_config, load_yaml
from srpipe.export import pack_models, update_lock
from srpipe.tasks.wake import CONFIG, data
from srpipe.tasks.wake import eval as wake_eval
from srpipe.tasks.wake.model.tcn import Tcn
from srpipe.tasks.wake.postproc.smooth import chip_scores

BRANCH = "wake"
INT8_DIR, LADDER_FILE, GRAPH_FILE = "int8", "ladder.yaml", "graph.native"
NORM_FILE = f"{BRANCH}.norm.bin"
PERMILLE = 1000
PROBE_DIR = ML_ROOT.parent / "firmware" / "components" / "ai_engine" / "test_apps" / "unit" / "main" / "probe"
PROBE_ENTRY = "probe"
# hops, bands, outputs, hops a call, input exponent, output exponent; then int8 input (hops x bands), int8 output
# (hops x outputs).
VECTORS_HEAD = struct.Struct("<4sIIIIii")
VECTORS_MAGIC = b"SRST"
# hops, bands, hops a call, smoothing hops; then float32 raw log-mel (hops x bands), then uint16 scores (hops).
BRANCH_HEAD = struct.Struct("<4sIIII")
BRANCH_MAGIC = b"SRWB"


def n_bands(cfg: dict) -> int:
    return device_of(cfg)["features"]["n_bands"]


def build(cfg: dict) -> Tcn:
    m = cfg["model"]
    return Tcn(n_bands(cfg), m["channels"], m["kernel"], m["dilations"])


def probe(cfg: dict, out: Path, work: Path, step: int = 1) -> list[Path]:
    """Write out/models.bin and out/stream.bin from a seeded random network streamed step hops a call, then
    branch_probe's two files; work keeps the ONNX and the .espdl."""
    p = cfg["probe"]
    if p["hops"] % step:
        raise ValueError(f"probe.hops {p['hops']} is no multiple of {step} hops a call")
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
        graph, work / "probe.espdl", streaming_input_shape=[*shape[:2], step], test_input=x[..., :step]
    )
    out.mkdir(parents=True, exist_ok=True)
    image = out / "models.bin"
    image.write_bytes(pack_models.pack([pack_models.Entry(PROBE_ENTRY, "espdl", espdl.read_bytes())]))
    vectors = out / "stream.bin"
    head = VECTORS_HEAD.pack(VECTORS_MAGIC, shape[2], shape[1], y.shape[1], step, io.input_exponent, io.output_exponent)
    vectors.write_bytes(head + x_int8[0].T.tobytes() + y_int8[0].T.tobytes())
    return [image, vectors, *branch_probe(cfg, graph, espdl, out, rng, step)]


def branch_probe(cfg: dict, graph, espdl: Path, out: Path, rng: np.random.Generator, step: int) -> list[Path]:
    """out/wake_models.bin, an image whose wake is the probe's graph with band statistics drawn by rng, and
    out/wake_branch.bin, raw log-mel frames and the smoothed permille chip_scores gives each, for ai_engine's wake
    branch on board B (KEHOACH 3.11)."""
    p, hops, io = cfg["probe"], cfg["eval"]["smooth_hops"], ptq_espdl.io_of(graph)
    frames, bands = p["hops"], n_bands(cfg)
    mean = rng.normal(0.0, 1.0, bands).astype(np.float32)
    std = rng.uniform(0.25, 0.75, bands).astype(np.float32)
    raw = (mean + std * rng.normal(p["input_mean"], p["input_std"], (frames, bands))).astype(np.float32)
    y = ptq_espdl.Simulator(graph)(normalised(raw, mean, std))[0, 0]
    logits = np.rint(y / np.float32(2.0**io.output_exponent)).astype(np.int64)
    scores = chip_scores(logits, io.output_exponent, hops)
    norm = mean.astype("<f4").tobytes() + std.astype("<f4").tobytes()
    entries = [pack_models.Entry(BRANCH, "espdl", espdl.read_bytes()), pack_models.Entry(BRANCH, "norm", norm)]
    image = out / "wake_models.bin"
    image.write_bytes(pack_models.pack(entries, wake_th_permille=PERMILLE // 2, wake_smooth_hops=hops))
    vectors = out / "wake_branch.bin"
    head = BRANCH_HEAD.pack(BRANCH_MAGIC, frames, bands, step, hops)
    vectors.write_bytes(head + raw.astype("<f4").tobytes() + scores.astype("<u2").tobytes())
    return [image, vectors]


def drawn_windows(folder: Path, count: int, width: int, rng: np.random.Generator) -> list[np.ndarray]:
    """count windows of width hops at places rng draws over the shards of a processed file, read in place."""
    built = yaml.safe_load((folder / "manifest.yaml").read_text(encoding="utf-8"))["sha256"]
    stems = sorted(n.removesuffix(".features.npy") for n in built if n.endswith(".features.npy"))
    windows = []
    for k in sorted(rng.integers(len(stems), size=count)):
        features = np.load(folder / f"{stems[k]}.features.npy", mmap_mode="r")
        start = int(rng.integers(0, len(features) - width + 1))
        windows.append(np.asarray(features[start : start + width], dtype=np.float32))
    return windows


def normalised(features: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    """(1, bands, hops) of features, hops by bands, in float32 as ai_engine normalises a frame."""
    return np.ascontiguousarray(((features.astype(np.float32) - mean) / std).T[None])


def int8_scores(sim: ptq_espdl.Simulator, io: ptq_espdl.Io, shard: data.Shard, stats: dict, hops: int) -> np.ndarray:
    """The chip's smoothed permille of every hop of the shard: the int8 net, whose streaming equals its whole-sequence
    simulation (E11-T10), fed the normalised frames it rounds onto its input grid, its logits through chip_scores."""
    y = sim(normalised(shard.features, stats["mean"], stats["std"]))[0, 0]
    return chip_scores(np.rint(y / np.float32(2.0**io.output_exponent)).astype(np.int64), io.output_exponent, hops)


def ptq(cfg: dict, run: Path, paths: dict, calibrations: list[str] | None = None) -> Path:
    """Rung 2 of the TCN: each of calibrations, by default all of quant.calibrations, kept at
    <run>/int8/<calibration>/graph.native and rowed in <run>/int8/ladder.yaml with the lowest permille threshold whose
    val false accepts stay within eval.false_accepts_per_hour, its val recall and its test false accepts, all scored
    as the chip scores (KEHOACH 3.11, 3.14)."""
    q = cfg["quant"]
    unknown = sorted(set(calibrations or []) - set(q["calibrations"]))
    if unknown:
        raise ValueError(f"{unknown} are not among quant.calibrations {q['calibrations']}")
    model, stats, used, _ = wake_eval.load_run(run, cfg)
    root = paths["processed"] / "wake" / load_run_config(run)["train"]["split"]
    rng, width = np.random.default_rng(q["seed"]), cfg["train"]["window_hops"]
    half = q["calib_windows"] // 2
    raw = drawn_windows(root / "train_pos", half, width, rng)
    raw += drawn_windows(root / "train_neg", q["calib_windows"] - half, width, rng)
    calib = [torch.from_numpy(normalised(w, stats["mean"], stats["std"])) for w in raw]
    around = data.label_hops(cfg["train"]["label_s"])
    sets = {
        name: data.load_set(root / name, name.endswith("_pos"), around, "float32")
        for name in ("val_pos", "val_neg", "val_hard", "test_neg")
        if (root / name).exists()
    }
    spec = used["eval"]
    grid_th = np.round(wake_eval.thresholds(spec["thresholds"]) * PERMILLE).astype(np.int64)
    path = run / INT8_DIR / LADDER_FILE
    ladder = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() and calibrations else {}
    ladder |= {"calib_windows": len(calib), "rows": ladder.get("rows", {})}
    for calibration in calibrations or q["calibrations"]:
        rungs = ptq_espdl.ladder(BRANCH) | {"calibration": calibration}
        work = run / INT8_DIR / calibration
        graph = ptq_espdl.quantize(model, calib, work, rungs)
        export_espdl.save_native(graph, work / GRAPH_FILE)
        sim, io = ptq_espdl.Simulator(graph), ptq_espdl.io_of(graph)

        def score(shard: data.Shard, sim=sim, io=io) -> np.ndarray:
            return int8_scores(sim, io, shard, stats, spec["smooth_hops"])

        negatives = sets["val_neg"] + sets.get("val_hard", [])
        val = wake_eval.sweep_scores(score, sets["val_pos"], negatives, used, grid_th)
        threshold, recall, rate = wake_eval.operating_point(val, spec["false_accepts_per_hour"])
        test = wake_eval.sweep_scores(score, [], sets["test_neg"], used, np.array([int(threshold)]))
        ladder["rows"][calibration] = {
            "calibration": calibration,
            "threshold_permille": int(threshold),
            "val_recall": round(recall, 4),
            "val_fa_per_hour": round(rate, 3),
            "test_fa_per_hour": round(float(test.false_accepts_per_hour[0]), 3),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(ladder, sort_keys=False), encoding="utf-8")
        print(calibration, ladder["rows"][calibration], flush=True)
    return path


def deploy(cfg: dict, run: Path, row: str) -> tuple[Path, Path]:
    """firmware/models/wake/: the row's graph exported to stream deploy.step_frames a call, the run's band statistics as
    its NORM entry, means then deviations in float32, and the branch's meta.json and lock row with the row's threshold
    and the hops its score averages over (KEHOACH 3.11, 6.3)."""
    rows = yaml.safe_load((run / INT8_DIR / LADDER_FILE).read_text(encoding="utf-8"))["rows"]
    if row not in rows:
        raise ValueError(f"{run}: no row {row!r} in {INT8_DIR}/{LADDER_FILE}, rows {sorted(rows)}")
    step = cfg["deploy"]["step_frames"]
    graph = export_espdl.load_native(run / INT8_DIR / row / GRAPH_FILE)
    io = ptq_espdl.io_of(graph)
    stats = dict(np.load(run / "band_stats.npz"))
    shape = (1, len(stats["mean"]), step)
    probe_in = np.random.default_rng(cfg["quant"]["seed"]).normal(0.0, 1.0, shape).astype(np.float32)
    test_input = ptq_espdl.to_int8(probe_in, io.input_exponent).astype(np.float32) * np.float32(2.0**io.input_exponent)
    work = run / INT8_DIR / row / "deploy"
    built = export_espdl.export(
        graph, work / f"{BRANCH}.espdl", streaming_input_shape=list(shape), test_input=test_input
    )
    target = update_lock.MODELS / BRANCH
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(built, target / built.name)
    (target / NORM_FILE).write_bytes(stats["mean"].astype("<f4").tobytes() + stats["std"].astype("<f4").tobytes())
    fields = {
        "row": row,
        "word": load_run_config(run)["word"],
        "step_frames": step,
        "threshold": {"permille": rows[row]["threshold_permille"]},
        "smooth_hops": cfg["eval"]["smooth_hops"],
    }
    files = [
        update_lock.Deployed(target / built.name, BRANCH, "espdl"),
        update_lock.Deployed(target / NORM_FILE, BRANCH, "norm"),
    ]
    return update_lock.record(BRANCH, run, files, fields)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("probe", help="write the streaming probe of E11-T10 for ai_engine/test_apps/unit")
    run.add_argument("--step", type=int, default=1, help="hops the streamed network takes a call")
    run.add_argument("--out", type=Path, default=PROBE_DIR)
    run.add_argument("--work", type=Path, default=ML_ROOT / "artifacts" / "wake" / "probe")
    rung = sub.add_parser("ptq", help="rung 2: each calibration scored as the chip scores into <run>/int8/")
    rung.add_argument("run", type=Path, help="a run directory of python -m srpipe.tasks.wake.train")
    rung.add_argument("--calibrations", nargs="+", help="only these of quant.calibrations: a stopped ladder goes on")
    ship = sub.add_parser("deploy", help="a row exported to stream, with its band statistics, into firmware/models")
    ship.add_argument("run", type=Path)
    ship.add_argument("--row", required=True, help="a row of <run>/int8/ladder.yaml")
    args = parser.parse_args(argv)
    cfg = load_yaml(CONFIG)
    if args.command == "ptq":
        print(ptq(cfg, args.run, data_paths(), args.calibrations))
        return 0
    if args.command == "deploy":
        print("\n".join(map(str, deploy(cfg, args.run, args.row))))
        return 0
    for path in probe(cfg, args.out, args.work, args.step):
        print(f"{path}: {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
