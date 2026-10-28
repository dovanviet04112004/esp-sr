"""SwiftF0 on the chip path of KEHOACH 3.11, along speaker's: import writes swift-f0's pinned model.onnx as a run, the
rewrite of model.py, swift_front and decode held to swift-f0's own detect; ptq quantises the network after its front end
with each calibration of rung 2 (3.14) into <run>/int8/<calibration>/, calibrated on train sessions of the command build
simulated again, and rows each against float in <run>/int8/ladder.yaml on the voiced frames of val sessions; rebuild
gives files of that build a row's pitch; timing and same-int8 are the hooks of 3.14's speed loop.
Run: python -m srpipe.tasks.pitch.quant import | ptq <run> | rebuild <run> | timing <run> | same-int8 <run>"""

from __future__ import annotations

import argparse
import hashlib
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import torch
import yaml

from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl
from srpipe.core import run_dir, splits
from srpipe.core.audio_io import to_float
from srpipe.core.config import CONFIGS, ML_ROOT, contract_front, data_paths, load_yaml
from srpipe.dsp.spec import swift_front
from srpipe.generated import grid
from srpipe.scenes import device
from srpipe.tasks.pitch import model
from srpipe.tasks.pitch.postproc import decode

CONFIG = CONFIGS / "models" / "pitch.yaml"
BRANCH = LADDER = "pitch"
MODEL_FILE = "model.onnx"
INT8_DIR, GRAPH_FILE, LADDER_FILE = "int8", "graph.native", "ladder.yaml"
CHUNK_HOPS = 256  # frames of one calibration or row tensor, 4.1 s
EDGE_HOPS = 10  # each chunk end's frames that see zero padding
VOICED = 0.5  # confidence at which swift-f0 calls a frame voiced
CALIB_STREAM, ROWS_STREAM = 1, 2  # rng streams of the drawn sessions, beside the seed
SILENCE_PEAK = 1e-3  # swift-f0's: a hop peaking under it has confidence 0
FLOAT_ROW = "float"  # rebuild's row for swift-f0 itself


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def front_tables(onnx_file: Path) -> tuple[dict[int, np.ndarray], dict[int, np.ndarray]]:
    """The windows and band matrices of the pinned graph by FFT size, as swift_front takes them: each window the
    initializer a Mul multiplies its frames by, each band matrix the one a ScatterElements fills."""
    import onnx
    from onnx import numpy_helper

    graph = onnx.load(str(onnx_file)).graph
    tensors = {t.name: numpy_helper.to_array(t) for t in graph.initializer}
    producer = {o: n for n in graph.node for o in n.output}
    windows = {
        len(tensors[i]): tensors[i].astype(np.float32)
        for n in graph.node
        if n.op_type == "Mul"
        for i in n.input
        if i in tensors and tensors[i].ndim == 1 and len(tensors[i]) in swift_front.SIZES
    }
    bands = {}
    for n in graph.node:
        if n.op_type != "ScatterElements":
            continue
        flat = np.zeros(int(tensors[producer[n.input[0]].input[0]][0]), np.float32)
        flat[tensors[n.input[1]]] = tensors[n.input[2]]
        matrix = flat.reshape(-1, model.BANDS)
        bands[2 * (len(matrix) - 1)] = matrix
    if set(windows) != set(swift_front.SIZES) or set(bands) != set(swift_front.SIZES):
        raise ValueError(f"{onnx_file}: windows of {sorted(windows)} and bands of {sorted(bands)} points")
    return windows, bands


def float_outputs(net: model.SwiftF0Net, spectra: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(PITCH_BINS, frames) pitch logits and (frames,) confidence logits of one stream's spectra."""
    with torch.no_grad():
        logits, confidence = net(torch.from_numpy(spectra[None]))
    return logits[0].numpy(), confidence[0].numpy()


@dataclass(frozen=True)
class Found:
    """A stream's frames as swift-f0's detect gives them."""

    pitch_hz: np.ndarray
    confidence: np.ndarray


class Detector:
    """SwiftF0 behind swift-f0's detect, as the chip runs it: the front end on dsp_spec's FFT, the network of a row of
    the run's ladder as ESP-PPQ simulates it, float without a row, the decoding, and swift-f0's silence gate."""

    def __init__(self, run: Path, row: str | None = None) -> None:
        self.windows, self.bands = front_tables(run / MODEL_FILE)
        if row is None:
            net = model.load(run / MODEL_FILE)
            self.outputs = lambda spectra: float_outputs(net, spectra)
        else:
            graph = export_espdl.load_native(run / INT8_DIR / row / GRAPH_FILE)
            sim = ptq_espdl.Simulator(graph, load_yaml(CONFIG)["esp_ppq_patches"])
            self.outputs = lambda spectra: tuple(o[0] for o in sim.run(spectra[None]))

    def detect(self, audio: np.ndarray, sample_rate: int, f_min_hz: float, f_max_hz: float) -> Found:
        """Pitch and confidence of each whole hop of float audio, at least one."""
        if sample_rate != grid.SAMPLE_RATE_HZ:
            raise ValueError(f"{sample_rate} Hz: the chip's SwiftF0 hears {grid.SAMPLE_RATE_HZ} Hz")
        spectra = swift_front.spectra(audio, self.windows, self.bands)
        pitch_hz, confidence = decode.decode(*self.outputs(spectra), f_min_hz, f_max_hz)
        hops = np.zeros((len(confidence), grid.HOP_SAMPLES), np.float32)
        hops.reshape(-1)[: min(hops.size, len(audio))] = audio[: hops.size]
        return Found(pitch_hz, np.where(np.abs(hops).max(axis=1) < SILENCE_PEAK, np.float32(0.0), confidence))


def rewrite_gap(run: Path, f_min_hz: float, f_max_hz: float) -> float:
    """Largest relative gap of the rewritten chain's pitch on voiced frames, and of its confidence on every frame, to
    swift-f0's detect, on a glide with its harmonics in light noise."""
    import swift_f0

    rng = np.random.default_rng(0)
    t = np.arange(3 * grid.SAMPLE_RATE_HZ) / grid.SAMPLE_RATE_HZ
    f0_hz = 150.0 + 70.0 * np.sin(2.0 * np.pi * 0.5 * t)
    phase = 2.0 * np.pi * np.cumsum(f0_hz) / grid.SAMPLE_RATE_HZ
    audio = 0.1 * (np.sin(phase) + 0.5 * np.sin(2 * phase)) * (t % 1.0 < 0.7) + rng.normal(0, 0.01, t.size)
    audio = audio.astype(np.float32)
    want = swift_f0.SwiftF0(threads=1, spin=False).detect(audio, grid.SAMPLE_RATE_HZ, f_min_hz, f_max_hz)
    found = Detector(run).detect(audio, grid.SAMPLE_RATE_HZ, f_min_hz, f_max_hz)
    pitch_hz, confidence = found.pitch_hz, found.confidence
    n = min(len(pitch_hz), len(want.pitch_hz))
    voiced = want.confidence[:n] >= VOICED
    pitch_gap = np.abs(pitch_hz[:n][voiced] / want.pitch_hz[:n][voiced] - 1.0).max(initial=0.0)
    return float(max(pitch_gap, np.abs(confidence[:n] - want.confidence[:n]).max()))


def import_run(cfg: dict) -> Path:
    """artifacts/pitch/runs/<run>/ holding swift-f0's model.onnx at its pin; refused when the installed file is another,
    or when the rewrite strays from swift-f0's detect by more than rewrite_rtol."""
    import swift_f0

    src = cfg["source"]
    found = Path(swift_f0.__file__).parent / src["file"]
    if (digest := sha256(found)) != src["sha256"]:
        raise ValueError(f"{found}: sha256 {digest}, the pin is {src['sha256']}")
    run = run_dir.create_run_dir(ML_ROOT / "artifacts", BRANCH, cfg)
    shutil.copyfile(found, run / MODEL_FILE)
    if (gap := rewrite_gap(run, decode.F_MIN_HZ, decode.F_MAX_HZ)) > cfg["rewrite_rtol"]:
        raise ValueError(f"{run}: the rewrite strays {gap:.3g} from swift-f0's detect, over {cfg['rewrite_rtol']}")
    return run


def drawn_cleans(paths: dict, build: str, files: list[str], count: int, rng: np.random.Generator) -> list[np.ndarray]:
    """count sessions of the build's files drawn by rng, simulated again: each one's clean samples as floats."""
    from srpipe.tasks.command.ctc.tone_flip import simulation

    sessions, built_of = [], {}
    for name in files:
        folder = paths["processed"] / "command" / build / name
        built_of[name] = yaml.safe_load((folder / "manifest.yaml").read_text(encoding="utf-8"))
        rows = splits.read_split(paths["splits"] / "command" / build / f"{name}.txt") * built_of[name]["repeats"]
        per = built_of[name]["config"]["session"]["items"]
        sessions += [(name, k, rows[i : i + per]) for k, i in enumerate(range(0, len(rows), per))]
    sims, out = {}, []
    for j in sorted(rng.choice(len(sessions), count, replace=False)):
        name, k, rows = sessions[j]
        sim = sims.setdefault(name, simulation(built_of[name], paths))
        captured, _, draws, _ = device.simulate_session(
            sim.cfg, k, rows, sim.bank, sim.mics, sim.pools, sim.readers, sim.floor, sim.speeds
        )
        clean, _, _ = device.listen(captured, replace(sim.chain_cfg, agc_start_db=draws.get("agc_start_db")), sim.mel)
        out.append(to_float(clean))
    return out


def chunks(spectra: np.ndarray) -> list[np.ndarray]:
    """A stream's spectra as whole CHUNK_HOPS-frame pieces, the rest left out."""
    return [spectra[:, :, i : i + CHUNK_HOPS] for i in range(0, spectra.shape[-1] - CHUNK_HOPS + 1, CHUNK_HOPS)]


def row_figures(want: list[tuple], got: list[tuple]) -> dict:
    """How far int8's decoded pitch and confidence sit from float's over each chunk's inner frames: the cents on
    frames both call voiced, and the share of frames whose voicing flips."""
    cents, flips, frames = [], 0, 0
    for (pitch_f, sure_f), (pitch_q, sure_q) in zip(want, got, strict=True):
        inner = slice(EDGE_HOPS, CHUNK_HOPS - EDGE_HOPS)
        voiced_f, voiced_q = sure_f[inner] >= VOICED, sure_q[inner] >= VOICED
        both = voiced_f & voiced_q
        cents += (1200.0 * np.abs(np.log2(pitch_q[inner][both] / pitch_f[inner][both]))).tolist()
        flips += int((voiced_f != voiced_q).sum())
        frames += len(voiced_f)
    c = np.asarray(cents)
    return {
        "voiced_frames": len(c),
        "cents_median": float(np.median(c)),
        "cents_p90": float(np.percentile(c, 90)),
        "cents_p99": float(np.percentile(c, 99)),
        "over_50_cents": float(np.mean(c > 50.0)),
        "voicing_flips": flips / frames,
    }


def calib_chunks(cfg: dict, run: Path, paths: dict) -> list[torch.Tensor]:
    """The ladder's calibration: every whole chunk of the spectra of quant.calib_sessions train sessions of the command
    build, drawn with the seed and simulated again, each a (1, SPECTRA, BANDS, CHUNK_HOPS) tensor."""
    q = cfg["quant"]
    folder = paths["processed"] / "command" / q["build"]
    train_files = sorted(p.name for p in folder.glob("train*") if p.is_dir())
    windows, bands = front_tables(run / MODEL_FILE)
    rng = np.random.default_rng([q["seed"], CALIB_STREAM])
    streams = drawn_cleans(paths, q["build"], train_files, q["calib_sessions"], rng)
    return [torch.from_numpy(c[None]) for s in streams for c in chunks(swift_front.spectra(s, windows, bands))]


def val_chunks(cfg: dict, run: Path, paths: dict, sessions: int) -> list[np.ndarray]:
    """Every whole chunk of the spectra of sessions val sessions of the command build, drawn with the seed as the
    ladder's rows draw theirs and simulated again."""
    q = cfg["quant"]
    windows, bands = front_tables(run / MODEL_FILE)
    streams = drawn_cleans(paths, q["build"], ["val"], sessions, np.random.default_rng([q["seed"], ROWS_STREAM]))
    return [c for s in streams for c in chunks(swift_front.spectra(s, windows, bands))]


def ptq(cfg: dict, run: Path, paths: dict, calibrations: list[str] | None = None) -> Path:
    """Rung 2 of the network: each of calibrations, by default all of quant.calibrations, kept at
    <run>/int8/<calibration>/graph.native and rowed in <run>/int8/ladder.yaml against float, both decoded over
    contracts/listen.yaml's F0 range."""
    q, patches = cfg["quant"], cfg["esp_ppq_patches"]
    unknown = sorted(set(calibrations or []) - set(q["calibrations"]))
    if unknown:
        raise ValueError(f"{unknown} are not among quant.calibrations {q['calibrations']}")
    calib = calib_chunks(cfg, run, paths)
    rows_in = val_chunks(cfg, run, paths, q["rows_sessions"])
    net = model.load(run / MODEL_FILE)
    pitch = contract_front()["pitch"]
    f0_range = (pitch["min_f0_hz"], pitch["max_f0_hz"])
    want = [decode.decode(*float_outputs(net, c), *f0_range) for c in rows_in]
    path = run / INT8_DIR / LADDER_FILE
    ladder = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() and calibrations else {}
    ladder |= {"calib_chunks": len(calib), "row_chunks": len(rows_in), "rows": ladder.get("rows", {})}
    for calibration in calibrations or q["calibrations"]:
        rungs = ptq_espdl.ladder(LADDER) | {"calibration": calibration}
        work = run / INT8_DIR / calibration
        with esp_ppq_patches.applied(patches):
            graph = ptq_espdl.quantize(net, calib, work, rungs)
        export_espdl.save_native(graph, work / GRAPH_FILE)
        sim = ptq_espdl.Simulator(graph, patches)
        got = [decode.decode(*(o[0] for o in sim.run(c[None])), *f0_range) for c in rows_in]
        ladder["rows"][calibration] = {"calibration": calibration} | row_figures(want, got)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(ladder, sort_keys=False), encoding="utf-8")
        print(calibration, ladder["rows"][calibration], flush=True)
    return path


def rebuilt_root(paths: dict, build: str, row: str) -> Path:
    """Where rebuild writes the files of a command build heard on a row of the pitch ladder, or on swift-f0 itself."""
    return paths["processed"] / "command" / f"{build}_resim_{row}"


def rebuild(cfg: dict, run: Path, row: str, paths: dict, files: list[str], workers: int) -> list[Path]:
    """Each of files of the command build quant.build simulated afresh into rebuilt_root, its three pitch dims from
    row of the run's ladder as the chip runs it, or from swift-f0 itself for FLOAT_ROW. The log-mel is today's, which
    the build's may no longer match, so the two rows compare on one log-mel (KEHOACH 3.11)."""
    build = cfg["quant"]["build"]
    out = []
    for name in files:
        built = yaml.safe_load((paths["processed"] / "command" / build / name / "manifest.yaml").read_text("utf-8"))
        pitch = built["config"]["pitch"] | ({} if row == FLOAT_ROW else {"int8": {"run": run.name, "row": row}})
        out.append(
            device.build(
                built["config"] | {"pitch": pitch},
                paths["splits"] / "command" / build / f"{name}.txt",
                paths["raw"],
                paths["interim"],
                rebuilt_root(paths, build, row) / name,
                workers,
                repeats=built["repeats"],
                pitch=True,
                keep_pcm=False,
                cut=built.get("cut", "pads"),
            )
        )
    return out


def timing(cfg: dict, run: Path, paths: dict) -> Path:
    """<run>/int8/timing/graph.native: the network quantised under quant.yaml's timing rungs on the ladder's
    calibration chunks, quick to make, its time on the chip the ladder's own (KEHOACH 3.14 step 2.1)."""
    folder = run / INT8_DIR / ptq_espdl.TIMING_ROW
    rungs = ptq_espdl.timing_rungs(LADDER)
    with esp_ppq_patches.applied(cfg["esp_ppq_patches"]):
        graph = ptq_espdl.quantize(model.load(run / MODEL_FILE), calib_chunks(cfg, run, paths), folder, rungs)
    return export_espdl.save_native(graph, folder / GRAPH_FILE)


def same_int8(cfg: dict, run: Path, paths: dict, against: str, calibration: str | None) -> tuple[int, float] | None:
    """(off, worst) of ptq_espdl.same_integers for model.py's network as it stands and at commit against, both
    quantised under the ladder's rungs on quant.check_chunks of its calibration chunks and compared on as many val
    chunks; None when model.py is the commit's (KEHOACH 3.14 step 2.4)."""
    old = ptq_espdl.at_commit(Path(model.__file__), model.__name__, against)
    if old is None:
        return None
    count = cfg["quant"]["check_chunks"]
    calib = calib_chunks(cfg, run, paths)[:count]
    inputs = [c[None] for c in val_chunks(cfg, run, paths, 1)[:count]]
    rungs = ptq_espdl.ladder(LADDER) | ({"calibration": calibration} if calibration else {})
    nets = [old.load(run / MODEL_FILE), model.load(run / MODEL_FILE)]
    return ptq_espdl.same_integers(nets, calib, inputs, rungs, cfg["esp_ppq_patches"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("import", help="swift-f0's pinned model.onnx as a run under artifacts/pitch/runs")
    rung = sub.add_parser("ptq", help="rung 2: each calibration against float into <run>/int8/")
    rung.add_argument("run", type=Path, help="a run directory of python -m srpipe.tasks.pitch.quant import")
    rung.add_argument("--calibrations", nargs="+", help="only these of quant.calibrations: a stopped ladder goes on")
    again = sub.add_parser("rebuild", help="files of the command build with their pitch from a row of the ladder")
    again.add_argument("run", type=Path)
    again.add_argument("--row", required=True, help="a row of <run>/int8/ladder.yaml, or float for swift-f0 itself")
    again.add_argument("--files", nargs="+", default=["val"], help="files of the build, val by default")
    again.add_argument("--workers", type=int, default=6, help="processes simulating the sessions")
    quick = sub.add_parser("timing", help="the timing row: a quick graph that times the chip as the ladder's rows")
    quick.add_argument("run", type=Path)
    check = sub.add_parser("same-int8", help="whether a rewrite of the network keeps every integer under the rungs")
    check.add_argument("run", type=Path)
    check.add_argument("--against", default="HEAD", help="the commit whose network code the edited one is held to")
    check.add_argument("--calibration", help="one of quant.calibrations; the ladder's default otherwise")
    args = parser.parse_args(argv)
    cfg = load_yaml(CONFIG)
    if args.command == "import":
        print(import_run(cfg))
    elif args.command == "rebuild":
        for path in rebuild(cfg, args.run, args.row, data_paths(), args.files, args.workers):
            print(path)
    elif args.command == "timing":
        print(timing(cfg, args.run, data_paths()))
    elif args.command == "same-int8":
        found = same_int8(cfg, args.run, data_paths(), args.against, args.calibration)
        return ptq_espdl.same_int8_verdict(found, args.against)
    else:
        print(ptq(cfg, args.run, data_paths(), args.calibrations))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
