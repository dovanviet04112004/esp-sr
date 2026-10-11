"""The board probe of the speaker branch (E11-T25, E11-T26): a row of <run>/int8/ladder.yaml as ai_engine/test_apps/unit
runs it on board B, its graph and probe.cuts cuts of it in a model image, the window ptq kept with its simulated
embedding. --chain puts the deployed speaker.espdl in its place, left in flash with the lock's hash and threshold, and
writes the chain's windows for the voice partition: enrolment and test windows of PCM with the unit embeddings, the
voiceprint, the scores and decisions Python gives on the chip's path (KEHOACH 3.17).
Run: python -m srpipe.tasks.speaker.probe --run <run> --row <row> [--cut-range <first> <last>] [--chain] [--out <d>]"""

from __future__ import annotations

import argparse
import itertools
import json
import struct
from pathlib import Path

import numpy as np

from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl
from srpipe.core.config import CONFIGS, ML_ROOT, data_paths, load_yaml
from srpipe.dsp.spec import spk_fbank
from srpipe.export import pack_models
from srpipe.generated import speaker
from srpipe.scenes import speaker as survey
from srpipe.tasks.speaker import quant
from srpipe.tasks.speaker.postproc import score

PROBE_DIR = ML_ROOT.parent / "firmware" / "components" / "ai_engine" / "test_apps" / "unit" / "main" / "probe"
MODELS_FILE, WINDOWS_FILE = "speaker_models.bin", "speaker_windows.bin"
ENTRY = "speaker"
CUT_ENTRY = "speaker_c{}"
# Timed runs; then entry name, frames, mels, embedding dims, input and output exponents, then the int8 features
# (frames x mels) and the int8 embedding, padded to four bytes.
WINDOWS_HEAD = struct.Struct("<4sI")
WINDOWS_MAGIC = b"SRSV"
RECORD = struct.Struct("<16sIIIii")
CHAIN_FILE = "speaker_chain.bin"
# Windows, dims, the lock's threshold, enrolment windows; then the voiceprint, then per window its kind, score and
# decision, its PCM and its unit embedding.
CHAIN_HEAD = struct.Struct("<4sIIiI")
CHAIN_MAGIC = b"SRSC"
CHAIN_WINDOW = struct.Struct("<Iii")  # kind, score permille, passes
ENROL, OWNER, IMPOSTOR = 0, 1, 2
CHAIN_TESTS = 6  # owner windows of other sessions, and as many impostors


def truncated(graph, name: str):
    """A copy of graph that computes only the variable name, made its one output."""
    cut = graph.copy()
    needed, stack = set(), [cut.variables[name].source_op]
    while stack:
        op = stack.pop()
        if op is not None and op.name not in needed:
            needed.add(op.name)
            stack.extend(v.source_op for v in op.inputs)
    for op in [op for op in cut.operations.values() if op.name not in needed]:
        cut.remove_operation(op, remove_unlinked_variable=True)
    cut.outputs.clear()
    cut.mark_variable_as_graph_output(cut.variables[name])
    return cut


def cut_points(graph, count: int, span: tuple[int, int] | None = None) -> list[tuple[int, str]]:
    """count (topological index, variable) pairs spread evenly inside span of the graph's ops, by default all of them,
    each the int8 output of a quantised op that is not a graph output."""
    from esp_ppq.core import QuantizationStates
    from esp_ppq.IR.quantize import QuantableOperation

    ops = graph.topological_sort()
    first, last = span or (0, len(ops))

    def int8_out(op) -> bool:
        if not isinstance(op, QuantableOperation) or op.outputs[0].name in graph.outputs:
            return False
        return QuantizationStates.is_activated(op.output_quant_config[0].dominated_by.state)

    picks = []
    for k in range(1, count + 1):
        i = first + (last - first) * k // (count + 1)
        while i < last and not int8_out(ops[i]):
            i += 1
        if i < last and (not picks or picks[-1][0] != i):
            picks.append((i, ops[i].outputs[0].name))
    return picks


def probe(cfg: dict, run: Path, row: str, out: Path, span: tuple[int, int] | None = None) -> tuple[Path, Path]:
    """Write out/speaker_models.bin and out/speaker_windows.bin of the run's row under cfg, the branch's config, the
    cuts of probe.cuts within span among the image's entries; the .espdl files stay beside the row's graph."""
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
    entries = [pack_models.Entry(ENTRY, "espdl", espdl)]
    for k, (index, name) in enumerate(cut_points(graph, cfg["probe"]["cuts"], span)):
        with esp_ppq_patches.applied(patches):
            part = export_espdl.export(
                truncated(graph, name), folder / row / f"{CUT_ENTRY.format(k)}.espdl", test_input=x
            )
        entries.append(pack_models.Entry(CUT_ENTRY.format(k), "espdl", part.read_bytes()))
        print(f"{CUT_ENTRY.format(k)}: op {index}, {name}, {part.stat().st_size} bytes of .espdl")
    out.mkdir(parents=True, exist_ok=True)
    image = out / MODELS_FILE
    image.write_bytes(pack_models.pack(entries))
    head = WINDOWS_HEAD.pack(WINDOWS_MAGIC, cfg["probe"]["runs"])
    head += RECORD.pack(ENTRY.encode("ascii"), *x.shape[2:], y.size, io.input_exponent, io.output_exponent)
    body = x_int8.tobytes() + y_int8.tobytes()
    windows = out / WINDOWS_FILE
    windows.write_bytes(head + body + b"\0" * (-len(body) % 4))
    print(f"{ENTRY}: {len(espdl)} bytes of .espdl")
    return image, windows


def chain_windows(survey_cfg: dict, q: dict, paths: dict, workers: int) -> list[tuple[int, survey.Window]]:
    """The first speaker.ENROL_COMMANDS accepted windows of the survey's enrolment session, CHAIN_TESTS accepted owner
    windows taken in turn from the other sessions, and the first window of each of CHAIN_TESTS impostors outside the
    calibration windows."""
    owner_cfg = survey_cfg["owner"]
    enrol_group = f"{owner_cfg['enrol']['date']} {owner_cfg['enrol']['distance_cm']}"
    owner = [w for w in survey.owner_windows(survey_cfg, paths) if w.accepted]
    enrol = [w for w in owner if w.group == enrol_group][: speaker.ENROL_COMMANDS]
    sessions = [[w for w in owner if w.group == g] for g in sorted({w.group for w in owner} - {enrol_group})]
    tests = [w for turn in itertools.zip_longest(*sessions) for w in turn if w is not None][:CHAIN_TESTS]
    impostors = survey.impostor_windows(survey_cfg, paths, workers)
    calibration = set(quant.drawn(q, impostors)[0])
    first: dict[str, survey.Window] = {}
    for i, w in enumerate(impostors):
        if i not in calibration:
            first.setdefault(w.spk, w)
    strangers = list(first.values())[:CHAIN_TESTS]
    return [(ENROL, w) for w in enrol] + [(OWNER, w) for w in tests] + [(IMPOSTOR, w) for w in strangers]


def chain(cfg: dict, run: Path, row: str, out: Path, workers: int) -> tuple[Path, Path]:
    """Write out/speaker_models.bin with the deployed speaker.espdl left in flash beside the row's cuts, and
    out/speaker_chain.bin: every window of chain_windows as the chip should take it, the voiceprint of the enrolment
    ones, and each test window's score and decision at the lock's threshold. Refused when the lock deploys another
    row or run."""
    lock = json.loads(pack_models.LOCK.read_text(encoding="utf-8"))["models"][quant.BRANCH]
    if (lock["run"], lock["row"]) != (run.name, row):
        raise ValueError(f"the lock deploys {lock['run']} row {lock['row']}, not {run.name} row {row}")
    image, _ = probe(cfg, run, row, out)
    deployed = (pack_models.MODELS / quant.BRANCH / lock["files"][0]["file"]).read_bytes()
    entries = [pack_models.Entry(ENTRY, "espdl", deployed, pack_models.IN_FLASH)]
    for k in range(cfg["probe"]["cuts"]):
        cut = run / quant.INT8_DIR / row / f"{CUT_ENTRY.format(k)}.espdl"
        entries.append(pack_models.Entry(CUT_ENTRY.format(k), "espdl", cut.read_bytes()))
    threshold = int(lock["threshold"]["permille"])
    image.write_bytes(pack_models.pack(entries, speaker_hash=speaker.HASH, speaker_permille=threshold))
    survey_cfg, paths = load_yaml(CONFIGS / cfg["source"]["survey"]), data_paths()
    picked = chain_windows(survey_cfg, cfg["quant"], paths, workers)
    graph = export_espdl.load_native(run / quant.INT8_DIR / row / quant.GRAPH_FILE)
    front = spk_fbank.SpkFbank(spk_fbank.contract_config())
    units, _ = quant.chip_units(graph, cfg["esp_ppq_patches"], [w for _, w in picked], front, quant.load_wrap(run))
    voiceprint = score.voiceprint([u for (kind, _), u in zip(picked, units, strict=True) if kind == ENROL])
    body = bytearray(CHAIN_HEAD.pack(CHAIN_MAGIC, len(picked), len(voiceprint), threshold, speaker.ENROL_COMMANDS))
    body += voiceprint.astype("<f4").tobytes()
    for (kind, w), u in zip(picked, units, strict=True):
        pcm = spk_fbank.tail(np.rint(w.samples * quant.PCM_FULL_SCALE).astype(np.int16), front.cfg.window_samples)
        s = score.score_permille(u, voiceprint)
        body += CHAIN_WINDOW.pack(kind, s, int(score.accepts(s, threshold))) + pcm.astype("<i2").tobytes()
        body += u.astype("<f4").tobytes()
        verdict = "passes" if score.accepts(s, threshold) else "stopped"
        print(f"chain window {kind}: {w.spk} {w.group}, score {s}, {verdict}")
    path = out / CHAIN_FILE
    path.write_bytes(bytes(body))
    return image, path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, required=True, help="a run of python -m srpipe.tasks.speaker.quant import")
    parser.add_argument("--row", required=True, help="the row of <run>/int8/ladder.yaml whose graph the board runs")
    parser.add_argument("--cut-range", type=int, nargs=2, help="topological op indices the cuts are spread between")
    parser.add_argument("--chain", action="store_true", help="the deployed speaker and the chain's windows")
    parser.add_argument("--workers", type=int, default=6, help="processes simulating the impostors' sessions")
    parser.add_argument("--out", type=Path, default=PROBE_DIR)
    args = parser.parse_args(argv)
    span = tuple(args.cut_range) if args.cut_range else None
    cfg = load_yaml(quant.CONFIG)
    if args.chain:
        paths = chain(cfg, args.run, args.row, args.out, args.workers)
    else:
        paths = probe(cfg, args.run, args.row, args.out, span)
    for path in paths:
        print(f"{path}: {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
