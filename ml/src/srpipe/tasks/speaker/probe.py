"""The board probe of the speaker branch (E11-T25, E11-T26, E11-T31): a row of <run>/int8/ladder.yaml as
ai_engine/test_apps/unit runs it on board B, its graph and probe.cuts cuts in a model image, the run's window or --pcm's
with its simulated embedding. --chain deploys speaker.espdl in flash with the lock's hash and thresholds, and writes a
full roster for the voice partition, the owner's voiceprint from PCM, the members' from units, then test windows with
the best score, its voiceprint and the decision Python gives on the chip's path (KEHOACH 3.17).
Run: python -m srpipe.tasks.speaker.probe --run <run> --row <row> [--cut-range <a> <b>] [--chain | --pcm <npy>]"""

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
# PCM windows, dims, voiceprints, windows a voiceprint; then the voiceprints, their means and deviations over the
# cohort's top, the members' enrolment units, and per PCM window CHAIN_WINDOW, PCM and unit embedding.
CHAIN_HEAD = struct.Struct("<4sIIII")
CHAIN_MAGIC = b"SRSA"
CHAIN_WINDOW = struct.Struct("<Iiiiiff")  # kind, score, voiceprint, passes, cosine, mean, deviation
ENROL, OWNER, IMPOSTOR, MEMBER = 0, 1, 2, 3
CHAIN_TESTS = 6  # owner windows of other sessions, and as many impostors


def truncated(graph, name: str):
    """A copy of graph that computes only the variable name, made its one output."""
    cut = graph.copy()
    needed = ancestors(cut.variables[name].source_op)
    for op in [op for op in cut.operations.values() if op.name not in needed]:
        cut.remove_operation(op, remove_unlinked_variable=True)
    cut.outputs.clear()
    cut.mark_variable_as_graph_output(cut.variables[name])
    return cut


def ancestors(op) -> set[str]:
    """The names of op and of every op it reads from, as truncated keeps them."""
    needed, stack = set(), [op]
    while stack:
        top = stack.pop()
        if top is not None and top.name not in needed:
            needed.add(top.name)
            stack.extend(v.source_op for v in top.inputs)
    return needed


def cut_points(graph, count: int, span: tuple[int, int] | None = None) -> list[tuple[int, str]]:
    """count (topological index, variable) pairs spread evenly inside span of the graph's ops, by default all of them,
    each the int8 output of a quantised op that is not a graph output, whose cut keeps the op owning every config the
    kept ops follow: a config dominated from a dropped op cannot be copied for export."""
    from esp_ppq.core import QuantizationStates
    from esp_ppq.IR.quantize import QuantableOperation

    ops = graph.topological_sort()
    first, last = span or (0, len(ops))
    owner = {}
    for op in ops:
        if isinstance(op, QuantableOperation):
            for config in [*op.input_quant_config, *op.output_quant_config]:
                owner.setdefault(id(config), op.name)

    def cuttable(op) -> bool:
        if not isinstance(op, QuantableOperation) or op.outputs[0].name in graph.outputs:
            return False
        if not QuantizationStates.is_activated(op.output_quant_config[0].dominated_by.state):
            return False
        kept = ancestors(op)
        configs = [
            c
            for name in kept
            if isinstance(graph.operations[name], QuantableOperation)
            for c in [*graph.operations[name].input_quant_config, *graph.operations[name].output_quant_config]
        ]
        return all(owner.get(id(c.dominated_by), op.name) in kept for c in configs)

    picks = []
    for k in range(1, count + 1):
        i = first + (last - first) * k // (count + 1)
        while i < last and not cuttable(ops[i]):
            i += 1
        if i < last and (not picks or picks[-1][0] != i):
            picks.append((i, ops[i].outputs[0].name))
    return picks


def probe(
    cfg: dict, run: Path, row: str, out: Path, span: tuple[int, int] | None = None, pcm: Path | None = None
) -> tuple[Path, Path]:
    """Write out/speaker_models.bin and out/speaker_windows.bin of the run's row under cfg, the branch's config, the
    cuts of probe.cuts within span among the image's entries; the .espdl files stay beside the row's graph. The window
    is the run's probe window, or the int16 samples saved in pcm through the chip's front end."""
    patches = cfg["esp_ppq_patches"]
    folder = run / quant.INT8_DIR
    graph = export_espdl.load_native(folder / row / quant.GRAPH_FILE)
    io = ptq_espdl.io_of(graph)
    if pcm is None:
        window = np.load(folder / quant.WINDOW_FILE)
    else:
        front = spk_fbank.SpkFbank(spk_fbank.contract_config())
        window = front.run(spk_fbank.tail(np.load(pcm).astype(np.int16), front.cfg.window_samples))[None, None]
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


def chain_windows(
    survey_cfg: dict, q: dict, paths: dict, workers: int, members: int
) -> tuple[list[tuple[int, survey.Window]], list[list[survey.Window]]]:
    """The roster's windows: the first speaker.ENROL_COMMANDS accepted windows of the survey's enrolment session,
    CHAIN_TESTS accepted owner windows taken in turn from the other sessions; members impostors in speaker order, each
    with speaker.ENROL_COMMANDS windows to enrol and one more to test; the first window of each of CHAIN_TESTS other
    impostors. Impostor windows come from outside the calibration windows and the deploy's cohort. Returns the PCM
    windows with their kinds, and each member's enrolment windows."""
    owner_cfg = survey_cfg["owner"]
    enrol_group = f"{owner_cfg['enrol']['date']} {owner_cfg['enrol']['distance_cm']}"
    owner = [w for w in survey.owner_windows(survey_cfg, paths) if w.accepted]
    enrol = [w for w in owner if w.group == enrol_group][: speaker.ENROL_COMMANDS]
    sessions = [[w for w in owner if w.group == g] for g in sorted({w.group for w in owner} - {enrol_group})]
    tests = [w for turn in itertools.zip_longest(*sessions) for w in turn if w is not None][:CHAIN_TESTS]
    impostors = survey.impostor_windows(survey_cfg, paths, workers)
    calibration = set(quant.drawn(q, impostors)[0])
    kept = [w for i, w in enumerate(impostors) if i not in calibration]
    cohort = quant.cohort_speakers(kept, survey_cfg)
    by_spk: dict[str, list[survey.Window]] = {}
    for w in kept:
        if w.spk not in cohort:
            by_spk.setdefault(w.spk, []).append(w)
    enough = [s for s in sorted(by_spk) if len(by_spk[s]) > speaker.ENROL_COMMANDS][:members]
    if len(enough) < members:
        need = speaker.ENROL_COMMANDS + 1
        raise ValueError(f"{len(enough)} impostors have {need} windows, the roster wants {members}")
    strangers = [by_spk[s][0] for s in by_spk if s not in enough][:CHAIN_TESTS]
    picked = [(ENROL, w) for w in enrol] + [(OWNER, w) for w in tests]
    picked += [(MEMBER, by_spk[s][speaker.ENROL_COMMANDS]) for s in enough] + [(IMPOSTOR, w) for w in strangers]
    return picked, [by_spk[s][: speaker.ENROL_COMMANDS] for s in enough]


def chain(cfg: dict, run: Path, row: str, out: Path, workers: int) -> tuple[Path, Path]:
    """Write out/speaker_models.bin with the deployed speaker.espdl left in flash beside the row's cuts, and
    out/speaker_chain.bin: a roster of pack_models.SPEAKERS voiceprints, the owner's of the enrolment windows and each
    member's of their units, each one's mean and deviation over the deployed cohort's top, then every other window of
    chain_windows as the chip should take it, with its best AS-norm score over the roster, the voiceprint giving it,
    the decision at the lock's threshold for a full roster, that voiceprint's cosine and the window's own mean and
    deviation (ADR-0023). Refused when the lock deploys another row or run."""
    lock = json.loads(pack_models.LOCK.read_text(encoding="utf-8"))["models"][quant.BRANCH]
    if (lock["run"], lock["row"]) != (run.name, row):
        raise ValueError(f"the lock deploys {lock['run']} row {lock['row']}, not {run.name} row {row}")
    image, _ = probe(cfg, run, row, out)
    by_kind = {f["kind"]: (pack_models.MODELS / quant.BRANCH / f["file"]).read_bytes() for f in lock["files"]}
    entries = [
        pack_models.Entry(ENTRY, "espdl", by_kind["espdl"], pack_models.IN_FLASH),
        pack_models.Entry(quant.COHORT_ENTRY, "cohort", by_kind["cohort"], pack_models.IN_FLASH),
    ]
    for k in range(cfg["probe"]["cuts"]):
        cut = run / quant.INT8_DIR / row / f"{CUT_ENTRY.format(k)}.espdl"
        entries.append(pack_models.Entry(CUT_ENTRY.format(k), "espdl", cut.read_bytes()))
    table = tuple(int(t) for t in lock["threshold"]["milli_z"])
    top = quant.deployed_top(lock)
    image.write_bytes(
        pack_models.pack(entries, speaker_hash=speaker.HASH, speaker_milli_z=table, speaker_cohort_top=top)
    )
    threshold = table[pack_models.SPEAKERS - 1]
    survey_cfg, paths = load_yaml(CONFIGS / cfg["source"]["survey"]), data_paths()
    picked, enrolments = chain_windows(survey_cfg, cfg["quant"], paths, workers, pack_models.SPEAKERS - 1)
    graph = export_espdl.load_native(run / quant.INT8_DIR / row / quant.GRAPH_FILE)
    front = spk_fbank.SpkFbank(spk_fbank.contract_config())
    windows = [w for _, w in picked] + [w for theirs in enrolments for w in theirs]
    units, _ = quant.chip_units(graph, cfg["esp_ppq_patches"], windows, front, quant.load_wrap(run))
    units, members = units[: len(picked)], units[len(picked) :]
    prints = [score.voiceprint([u for (kind, _), u in zip(picked, units, strict=True) if kind == ENROL])]
    for k in range(len(enrolments)):
        prints.append(score.voiceprint(members[k * speaker.ENROL_COMMANDS : (k + 1) * speaker.ENROL_COMMANDS]))
    cohort = quant.deployed_cohort(lock, len(prints[0]))
    norms = [score.norm(p, cohort, top) for p in prints]
    body = bytearray(CHAIN_HEAD.pack(CHAIN_MAGIC, len(picked), len(prints[0]), len(prints), speaker.ENROL_COMMANDS))
    body += np.stack(prints).astype("<f4").tobytes() + np.array(norms, dtype="<f4").tobytes()
    body += np.stack(members).astype("<f4").tobytes()
    for (kind, w), u in zip(picked, units, strict=True):
        pcm = spk_fbank.tail(np.rint(w.samples * quant.PCM_FULL_SCALE).astype(np.int16), front.cfg.window_samples)
        unit_norm = score.norm(u, cohort, top)
        z, best, cosine = score.best(u, prints, norms, unit_norm)
        passes = score.accepts(z, threshold)
        body += CHAIN_WINDOW.pack(kind, z, best, int(passes), cosine, *unit_norm) + pcm.astype("<i2").tobytes()
        body += u.astype("<f4").tobytes()
        verdict = "passes" if passes else "stopped"
        print(
            f"chain window {kind}: {w.spk} {w.group}, best {z} milli-z of voiceprint {best}, cosine {cosine}, {verdict}"
        )
    path = out / CHAIN_FILE
    path.write_bytes(bytes(body))
    return image, path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, required=True, help="a run of python -m srpipe.tasks.speaker.quant import")
    parser.add_argument("--row", required=True, help="the row of <run>/int8/ladder.yaml whose graph the board runs")
    parser.add_argument("--cut-range", type=int, nargs=2, help="topological op indices the cuts are spread between")
    parser.add_argument("--cuts", type=int, help="cuts of the graph in the image; probe.cuts otherwise")
    parser.add_argument("--chain", action="store_true", help="the deployed speaker and the chain's windows")
    parser.add_argument("--pcm", type=Path, help="a .npy of int16 samples to probe instead of the run's window")
    parser.add_argument("--workers", type=int, default=6, help="processes simulating the impostors' sessions")
    parser.add_argument("--out", type=Path, default=PROBE_DIR)
    args = parser.parse_args(argv)
    span = tuple(args.cut_range) if args.cut_range else None
    cfg = load_yaml(quant.CONFIG)
    if args.cuts is not None:
        cfg = cfg | {"probe": cfg["probe"] | {"cuts": args.cuts}}
    if args.chain:
        paths = chain(cfg, args.run, args.row, args.out, args.workers)
    else:
        paths = probe(cfg, args.run, args.row, args.out, span, args.pcm)
    for path in paths:
        print(f"{path}: {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
