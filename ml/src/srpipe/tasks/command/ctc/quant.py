"""The quantisation ladder of the ctc net (KEHOACH 3.14, ADR-0013). Run: python -m srpipe.tasks.command.ctc.quant
ptq|int16|qat <run> | thresholds|deploy <run> --row <row> [--kaldi-pitch] [--hold <dim>]: rungs 1 to 4, the last two on
rung 2's calibration Gate 3 rates best, each adding to <run>/int8/ladder.yaml the test set's unit error rate and Gate 3
after int8 beside float, each row's graph under <run>/int8/<row>/ for probe.py; heard on the board's Kaldi pitch with a
dim folded at its mean, under <run>/int8_kaldi_<dim>/. thresholds picks a row's delta1 and delta2; deploy puts graph
and pair into firmware/models/command/ and records them with update_lock (E11-T19)."""

from __future__ import annotations

import argparse
import copy
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import torch
import yaml
from torch import nn

from srpipe.compress.quant import esp_ppq_patches, export_espdl, mixed_espdl, ptq_espdl, qat_espdl
from srpipe.core import corpus, extract, screen, splits
from srpipe.core.config import apply_overrides, data_paths, load_yaml, on_contract_pitch
from srpipe.dsp.spec import pitch
from srpipe.export import update_lock
from srpipe.generated import listen
from srpipe.tasks import command
from srpipe.tasks.command import ctc
from srpipe.tasks.command import eval as gate
from srpipe.tasks.command.ctc import data, qat, train
from srpipe.tasks.command.ctc.model import encoder
from srpipe.tasks.command.ctc.postproc import ctc_score
from srpipe.tasks.wake.data import sentence_units

LADDER = "command_ctc"
GRAPH_FILE = "graph.native"
# The image names a branch's entries after its backend (KEHOACH 6.3).
BRANCH, ENTRY = "command", "command_ctc"
CHIP_BITS = 8  # espdl_net views int8 tensors only (KEHOACH 6.3)


def kaldi_cfg(cfg: dict, paths: dict) -> dict:
    """cfg on the contract's Kaldi pitch, its sentences read from its simulate.mel_from build, which holds the same
    items and log-mel on that pitch; refused unless that build is what the contract's front makes of the split."""
    source = cfg.get("simulate", {}).get("mel_from")
    if source is None:
        raise ValueError("the run's build tracked its pitch over its own log-mel: no build holds it on Kaldi's pitch")
    heard = on_contract_pitch(cfg) | {"split": cfg["split"] | {"version": source}, "listen_hash": listen.HASH}
    if stale := data.unbuilt(heard, paths):
        raise ValueError(f"{', '.join(str(p) for p in stale)}: not the contract's front over the split, as {source}")
    return heard


def folded(net: gate.Ctc, dims: tuple[int, ...]) -> gate.Ctc:
    """net with the pitch dims at dims held at their train mean inside it: their columns of the front's projection,
    the one way the hop-averaged pitch dims reach the net, at zero (KEHOACH 3.14)."""
    model = copy.deepcopy(net.model)
    proj = model.front.proj
    with torch.no_grad():
        proj.weight[:, [proj.in_channels - gate.N_PITCH + d for d in dims]] = 0.0
    return replace(net, model=model)


@dataclass(frozen=True)
class Hearing:
    """The pitch a ladder hears its run on: the one the run learnt, or the contract's Kaldi pitch as the board gives
    it, with the pitch dims gate.HOLDS names under hold folded at their train mean (KEHOACH 3.14)."""

    kaldi: bool = False
    hold: str | None = None

    def folder(self, run: Path) -> Path:
        return run / "_".join(["int8", *(["kaldi"] if self.kaldi else []), *([self.hold] if self.hold else [])])

    def net(self, run: Path, paths: dict) -> gate.Ctc:
        net = gate.load_ctc(run)
        if self.kaldi:
            net = replace(net, cfg=kaldi_cfg(net.cfg, paths))
        return folded(net, gate.HOLDS[self.hold]) if self.hold else net

    def held(self) -> tuple[int, ...]:
        return gate.HOLDS[self.hold] if self.hold else ()


LEARNT = Hearing()


def padded(x: np.ndarray, hops: int, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    """Raw features (n, dims) zero-padded at the end to hops, as training pads a batch, then normalised: (1, dims,
    hops)."""
    out = np.zeros((hops, x.shape[1]), dtype=np.float32)
    out[: len(x)] = x
    return ((out - mean) / std).T[None].astype(np.float32)


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
    return padded(x, hops, net.mean, net.std)


def calibration_items(spec: dict, root: Path) -> list[tuple[Path, int]]:
    """spec's calib_sentences first hops, drawn with spec's seed, of spans of spec's hops in the run's train shards
    under root: each a sentence's start with a whole span after it in its shard, with its listing, in file order. A
    sentence padded to the graph's hops would fill most of it with padding the chip never runs (KEHOACH 3.14)."""
    found = []
    for listing in sorted(root.glob("train_*/*.items.jsonl")):
        items = [json.loads(line) for line in listing.read_text(encoding="utf-8").splitlines()]
        end = max((i["frame_offset"] + i["n_frames"] for i in items), default=0)
        found += [(listing, i["frame_offset"]) for i in items if i["frame_offset"] + spec["hops"] <= end]
    picks = np.random.default_rng(spec["seed"]).choice(len(found), spec["calib_sentences"], replace=False)
    return [found[k] for k in sorted(picks)]


def calibration(spec: dict, mean: np.ndarray, std: np.ndarray, root: Path) -> list[torch.Tensor]:
    """The spans of calibration_items normalised as the net reads them, (1, dims, hops) each."""
    out = []
    for listing, first in calibration_items(spec, root):
        stem = str(listing).removesuffix(".items.jsonl")
        span = slice(first, first + spec["hops"])
        x = np.concatenate([np.load(stem + s, mmap_mode="r")[span] for s in (".features.npy", ".pitch.npy")], 1)
        out.append(torch.from_numpy(((x.astype(np.float32) - mean) / std).T[None].astype(np.float32)))
    return out


def batched(calib: list[torch.Tensor], batch: int) -> list[torch.Tensor]:
    """calib stacked batch sentences a tensor, for a graph built for that batch; a short last batch is left out."""
    return [torch.cat(calib[k : k + batch]) for k in range(0, len(calib) - batch + 1, batch)]


class Int8Net:
    """A quantised graph called as the float net is, on normalised features (1, dims, hops) of at most the graph's
    hops: padded on as training pads, the input on its int8 grid as the chip takes it, logits as the chip gives
    them, simulated under the branch's ESP-PPQ fixes."""

    def __init__(
        self, graph, hops: int, mean: np.ndarray, std: np.ndarray, like: encoder.CtcNet, patches: list[str]
    ) -> None:
        self.simulate, self.io, self.hops = ptq_espdl.Simulator(graph, patches), ptq_espdl.io_of(graph), hops
        self.zero = (-mean / std).astype(np.float32)
        self.chunk_multiple, self.front = like.chunk_multiple, like.front

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        x = x.numpy()
        if x.shape[2] > self.hops:
            raise ValueError(f"{x.shape[2]} hops, the graph takes {self.hops}")
        whole = np.repeat(self.zero[None, :, None], self.hops, axis=2)
        whole[..., : x.shape[2]] = x
        exponent = np.float32(2.0**self.io.input_exponent)
        return torch.from_numpy(self.simulate(ptq_espdl.to_int8(whole, self.io.input_exponent) * exponent))


def unit_error_rate(net, data: train.Sentences, picks: np.ndarray, hops: int, mean: np.ndarray, std: np.ndarray):
    """Unit error rate of net's best path over the picked sentences, each padded to hops as the graph reads it."""
    errors = total = 0
    for k in picks:
        x = data.features[data.first[k] : data.first[k] + data.hops[k]]
        with torch.no_grad():
            logits = net(torch.from_numpy(padded(x, hops, mean, std)))
        frames = -(-int(data.hops[k]) // net.front.hop_stride)
        log_probs = logits.log_softmax(1)[0, :, :frames].numpy()
        errors += train.edit_distance(train.best_path(log_probs), data.units[k])
        total += len(data.units[k])
    return errors / total


def chip_heard(net: gate.Ctc, x: np.ndarray) -> gate.Heard:
    """One window decided as the chip decides it on net.model, an Int8Net: the int8 logits of its own frames to
    log-probabilities by ctc_score.frame_log_probs, as ai_engine_command_ctc_log_probs takes them, with no threshold."""
    window, frames = gate.normalised_window(net, x)
    exponent = net.model.io.output_exponent
    with torch.no_grad():
        logits = net.model(window).numpy()[0, :, :frames]
    log_probs = ctc_score.frame_log_probs(ptq_espdl.to_int8(logits, exponent), exponent)
    per_frames = ctc_score.window_frames(net.model.front.hop_stride)
    return gate.heard_of(net, *ctc_score.decide(log_probs, net.lexicon, ctc_score.CAP, 0, per_frames))


def gate_row(
    net: gate.Ctc,
    windows: list[gate.Scored],
    thresholds: tuple[int, int, int],
    heard_by: Callable = gate.ctc_heard,
) -> dict:
    """Gate 3 of net on the board windows, each decided by heard_by(net, window): utterances whose best command is
    right, those accepted right at thresholds, reject, margin and syllable, and the false accepts among the rest."""
    heard = [(s.expected, heard_by(net, x)) for s in windows for x in s.decided]
    commands = [(e, h) for e, h in heard if e != gate.REJECT]
    others = [h for e, h in heard if e == gate.REJECT]
    accepted = [(e, h) for e, h in commands if h.accepted(*thresholds)]
    return {
        "best_right": f"{sum(h.command == e for e, h in commands)}/{len(commands)}",
        "accepted_right": f"{sum(h.command == e for e, h in accepted)}/{len(commands)}",
        "false_accepts": f"{sum(h.accepted(*thresholds) for h in others)}/{len(others)}",
    }


def quantized(model: nn.Module, calib: list[torch.Tensor], folder: Path, rungs: dict, patches: list[str]):
    """model quantised under rungs into folder with the named ESP-PPQ fixes in force, refused unless ESP-PPQ fused
    every norm into esp-dl's RMSNormalization, as the chip runs it: a norm left an int8 chain is another net."""
    with esp_ppq_patches.applied(patches):
        graph = ptq_espdl.quantize(model, calib, folder, rungs)
    norms = sum(isinstance(m, encoder.ScalarRmsNorm) for m in model.modules())
    fused = sum(op.type == "RMSNormalization" for op in graph.operations.values())
    if fused != norms:
        raise ValueError(f"{folder}: ESP-PPQ fused {fused} of {norms} norms into esp-dl's RMSNormalization")
    return graph


@dataclass
class Bench:
    """What every row is measured on: the float net and its calibration, the test sentences and the board windows."""

    net: gate.Ctc
    calib: list[torch.Tensor]
    test: train.Sentences
    picks: np.ndarray
    windows: list[gate.Scored]


def board_windows(cfg: dict, net: gate.Ctc, paths: dict) -> list[gate.Scored]:
    """The command windows svc_listen cuts on the board sessions of Gate 3 (KEHOACH 5.4)."""
    said = {c["id"]: c["text"] for c in json.loads(command.COMMANDS.read_text(encoding="utf-8"))["commands"]}

    def window_of(clean, features, pitch, spans):
        return gate.ctc_windows(features, pitch, spans)

    return gate.board(net.cfg, load_yaml(command.CONFIG)["eval"]["board"], paths, said, window_of)


def bench(cfg: dict, run: Path, hearing: Hearing = LEARNT) -> Bench:
    """The run's net as hearing hears it, its calibration, quant.test_sentences test sentences drawn with the seed,
    and the board windows."""
    spec, paths = cfg["quant"], data_paths()
    net = hearing.net(run, paths)
    version = net.cfg["split"]["version"]
    root = paths["processed"] / "command" / version
    listed = {r.item for r in splits.read_split(paths["splits"] / "command" / version / "test.txt")}
    clips = screen.kept_clips(load_yaml(screen.CONFIG), paths, "speech")
    units_of = sentence_units(clips, listed, net.cfg["train"]["dialect"])
    test = train.load_role([root / "test"], units_of, spec["hops"], "float32")
    rng = np.random.default_rng(spec["seed"])
    picks = np.sort(rng.choice(len(test.first), min(spec["test_sentences"], len(test.first)), replace=False))
    windows = board_windows(cfg, net, paths)
    return Bench(net, calibration(spec, net.mean, net.std, root), test, picks, windows)


def row_of(cfg: dict, b: Bench, model) -> dict:
    """The unit error rate of model, the float net or an Int8Net, over the picked test sentences, and its Gate 3, an
    Int8Net's decided as the chip decides."""
    spec, n = cfg["quant"], b.net
    errors = unit_error_rate(model, b.test, b.picks, spec["hops"], n.mean, n.std)
    heard = gate.Ctc(model, n.mean, n.std, n.names, n.lexicon, n.cfg)
    by = chip_heard if isinstance(model, Int8Net) else gate.ctc_heard
    gated = gate_row(heard, b.windows, (spec["reject"], cfg["eval"]["margin"], ctc_score.CAP), by)
    return {"unit_error_rate": round(errors, 4), **gated}


def int8_row(cfg: dict, b: Bench, graph, folder: Path, rungs: dict) -> dict:
    """graph kept at folder/graph.native, and its row headed by the calibration and int16 layers it was built on."""
    export_espdl.save_native(graph, folder / GRAPH_FILE)
    int8 = Int8Net(graph, cfg["quant"]["hops"], b.net.mean, b.net.std, b.net.model, cfg["esp_ppq_patches"])
    return {"calibration": rungs["calibration"], "int16_ops": rungs["int16_ops"], **row_of(cfg, b, int8)}


def ladder_file(run: Path, hearing: Hearing = LEARNT) -> Path:
    return hearing.folder(run) / "ladder.yaml"


def recorded(run: Path, head: dict, rows: dict, hearing: Hearing = LEARNT) -> Path:
    """head and rows merged into the ladder.yaml of the run as hearing hears it, rows of the other steps kept; each
    row printed."""
    for name, row in rows.items():
        print(f"{name}: {row}", flush=True)
    out = ladder_file(run, hearing)
    kept = yaml.safe_load(out.read_text(encoding="utf-8")) if out.is_file() else {}
    merged = kept | head | {"rows": kept.get("rows", {}) | rows}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(merged, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return out


def counted(share: str) -> int:
    return int(share.split("/")[0])


def best_calibration(run: Path, calibrations: list[str], tie: int, hearing: Hearing = LEARNT) -> str:
    """The calibration of rung 2 rated best (KEHOACH 3.14): among those within tie commands accepted right of the
    most, the lowest unit error rate on the test set, then the fewest false accepts."""
    out = ladder_file(run, hearing)
    rows = yaml.safe_load(out.read_text(encoding="utf-8"))["rows"] if out.is_file() else {}
    missing = [c for c in calibrations if c not in rows]
    if missing:
        raise ValueError(f"{out} has no row of {missing}: run the ptq step first")
    most = max(counted(rows[c]["accepted_right"]) for c in calibrations)
    tied = [c for c in calibrations if counted(rows[c]["accepted_right"]) >= most - tie]
    return min(tied, key=lambda c: (rows[c]["unit_error_rate"], counted(rows[c]["false_accepts"])))


def heard_head(hearing: Hearing) -> dict:
    return {"heard": {"pitch": "kaldi" if hearing.kaldi else "learnt", "hold": hearing.hold}}


def hearing_of(record: dict) -> Hearing:
    """The hearing a ladder head or a deploy record names under heard, the run's own pitch when it names none."""
    heard = record.get("heard") or {}
    return Hearing(heard.get("pitch") == "kaldi", heard.get("hold"))


def step_ptq(cfg: dict, run: Path, hearing: Hearing = LEARNT) -> Path:
    """Rungs 1 and 2: the float row, then a row of the net quantised with each calibration."""
    spec, b, folder = cfg["quant"], bench(cfg, run, hearing), hearing.folder(run)
    head = {"rungs": ptq_espdl.ladder(LADDER), "quant": spec, "test_sentences": len(b.picks)} | heard_head(hearing)
    out = recorded(run, head, {"float": row_of(cfg, b, b.net.model)}, hearing)
    for name in spec["calibrations"]:
        rungs = ptq_espdl.ladder(LADDER) | {"calibration": name}
        graph = quantized(b.net.model, b.calib, folder / name, rungs, cfg["esp_ppq_patches"])
        out = recorded(run, head, {name: int8_row(cfg, b, graph, folder / name, rungs)}, hearing)
    return out


def step_int16(cfg: dict, run: Path, hearing: Hearing = LEARNT) -> Path:
    """Rung 3 on the best calibration: ESP-PPQ's per-layer error ranks the convolutions, and each row puts the worst
    of them at 16 bits."""
    spec, b, folder = cfg["quant"], bench(cfg, run, hearing), hearing.folder(run)
    best = best_calibration(run, spec["calibrations"], spec["gate_tie"], hearing)
    rungs = ptq_espdl.ladder(LADDER) | {"calibration": best}
    base = quantized(b.net.model, b.calib, folder / "int16_base", rungs, cfg["esp_ppq_patches"])
    with esp_ppq_patches.applied(cfg["esp_ppq_patches"]):
        ranked = mixed_espdl.ranked_layers(base, b.calib[: spec["layerwise_sentences"]])
    layers = [{"op": name, "noise_to_signal": round(error, 6)} for name, error in ranked]
    head = {"int16_base": rungs["calibration"], "layerwise": layers}
    out = recorded(run, head, {}, hearing)
    for name, ops in mixed_espdl.int16_rows(ranked, spec["int16_tops"]).items():
        wide = rungs | {"int16_ops": ops}
        graph = quantized(b.net.model, b.calib, folder / name, wide, cfg["esp_ppq_patches"])
        out = recorded(run, head, {name: int8_row(cfg, b, graph, folder / name, wide)}, hearing)
    return out


def step_qat(cfg: dict, run: Path, device: str, hearing: Hearing = LEARNT) -> Path:
    """Rung 4 on the best calibration: the graph built for quant.qat.batch learns with CTC, the held pitch dims at
    their train mean, then what it learnt moves onto the graph of one, which the row measures; the val rows go to
    qat/history.yaml of the ladder."""
    spec, b = cfg["quant"], bench(cfg, run, hearing)
    best = best_calibration(run, spec["calibrations"], spec["gate_tie"], hearing)
    rungs = ptq_espdl.ladder(LADDER) | {"calibration": best}
    folder, patches = hearing.folder(run) / "qat", cfg["esp_ppq_patches"]
    wide = quantized(b.net.model, batched(b.calib, spec["qat"]["batch"]), folder / "batch", rungs, patches)
    stats, sets = (b.net.mean, b.net.std), train.load_sets(b.net.cfg)
    with esp_ppq_patches.applied(patches):
        history = qat.fit(wide, sets, stats, cfg, b.net.cfg, b.net.model, device, hearing.held())
    (folder / "history.yaml").write_text(yaml.safe_dump(history, sort_keys=False), encoding="utf-8")
    graph = quantized(b.net.model, b.calib, folder, rungs, patches)
    with esp_ppq_patches.applied(patches):
        qat_espdl.carry(wide, graph, b.calib[0].numpy())
    return recorded(run, {"qat": spec["qat"]}, {"qat": int8_row(cfg, b, graph, folder, rungs)}, hearing)


THRESHOLD_KEYS = ("reject_permille", "margin_permille", "syllable_permille")


def thresholds_file(run: Path, row: str, hearing: Hearing = LEARNT) -> Path:
    return hearing.folder(run) / row / "thresholds.yaml"


def built_windows(folder: Path) -> list[tuple[list[str], np.ndarray]]:
    """Each window of a finished build of the split: the items of the clips it holds, and its log-mel and pitch."""
    out = []
    for listing in sorted(folder.glob("*.items.jsonl")):
        stem = str(listing).removesuffix(".items.jsonl")
        mel, pitch_ = (np.load(stem + s, mmap_mode="r") for s in (".features.npy", ".pitch.npy"))
        for line in listing.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            span = slice(item["frame_offset"], item["frame_offset"] + item["n_frames"])
            x = np.concatenate([mel[span], pitch_[span]], axis=1).astype(np.float32)
            out.append(([c.split("@")[0] for c in item.get("clips", [item["item"]])], x))
    return out


def operating_point(
    said: dict[str, list[gate.Heard]],
    heard: list[gate.Heard],
    meant: list[bool],
    outside: list[gate.Heard],
    spec: dict,
    sweep: list[int],
) -> dict:
    """The triple of sweep x spec's margins x spec's syllable caps accepting the most of the worst command of said, its
    windows' Heard by id, among those with spec's min_windows, then the most of said, then the fewest false accepts, of
    the triples keeping false accepts of heard, speech windows, an accept counting unless meant says it says just that
    command, within spec's false_accept and accepts of outside, said's windows decided over the set without their own
    command, within its out_of_set_accept; with every row. With none within both, the fewest accepts of outside among
    those within false_accept, or else the fewest false accepts, marked so."""
    ranked = [c for c, hs in said.items() if len(hs) >= spec["min_windows"]]
    total = sum(len(hs) for hs in said.values())
    rows = []
    for reject in sweep:
        for margin in spec["margin_sweep"]:
            for syllable in spec["syllable_sweep"]:
                limits = (reject, margin, syllable)
                right = {c: sum(h.command == c and h.accepted(*limits) for h in hs) for c, hs in said.items()}
                false = sum(h.accepted(*limits) and not ok for h, ok in zip(heard, meant, strict=True))
                row = {"reject_permille": reject, "margin_permille": margin, "syllable_permille": syllable}
                row["worst"] = round(min(right[c] / len(said[c]) for c in ranked), 4)
                row |= {"overall": round(sum(right.values()) / total, 4), "false_accepts": false}
                row["out_of_set_accepts"] = sum(h.accepted(*limits) for h in outside)
                rows.append(row | {"commands": {c: f"{right[c]}/{len(said[c])}" for c in said}})
    clean = [r for r in rows if r["false_accepts"] <= spec["false_accept"] * len(heard)]
    within = [r for r in clean if r["out_of_set_accepts"] <= spec["out_of_set_accept"] * len(outside)]
    if within:
        best = max(within, key=lambda r: (r["worst"], r["overall"], -r["false_accepts"], -r["out_of_set_accepts"]))
    elif clean:
        best = min(clean, key=lambda r: (r["out_of_set_accepts"], -r["worst"], -r["overall"]))
    else:
        best = min(rows, key=lambda r: (r["false_accepts"], -r["worst"]))
    return {
        "reject_permille": best["reject_permille"],
        "margin_permille": best["margin_permille"],
        "syllable_permille": best["syllable_permille"],
        "within_target": bool(within),
        "chosen": best,
        "table": rows,
    }


def step_thresholds(cfg: dict, run: Path, row: str, hearing: Hearing = LEARNT) -> Path:
    """delta1, delta2 and delta3 for row (KEHOACH 3.12): every window of val_commands, real voices saying one learned
    command, decided over the learned set and again over it without that command, a phrase the set lacks, and every
    window of val, speech that says none, decided as the chip decides on the row's int8 graph, each cut back to
    window_s as svc_listen cuts it; operating_point over eval.reject_sweep and quant.thresholds, written with its
    table and Gate 3 at the triple to <run>/int8/<row>/thresholds.yaml for deploy."""
    paths = data_paths()
    net = hearing.net(run, paths)
    graph = export_espdl.load_native(hearing.folder(run) / row / GRAPH_FILE)
    int8 = Int8Net(graph, cfg["quant"]["hops"], net.mean, net.std, net.model, cfg["esp_ppq_patches"])
    chip = gate.Ctc(int8, net.mean, net.std, net.names, net.lexicon, net.cfg)
    root = paths["processed"] / "command" / net.cfg["split"]["version"]
    spec = net.cfg["split"]["commands"]
    phrase_of = {
        f"speech/{spec['extract']}/{r['file']}": r["phrase"]
        for r in extract.read_index(paths["raw"] / "speech" / spec["extract"])
    }
    id_of = {c["text"]: c["id"] for c in command.learned(load_yaml(command.CONFIG))}
    lacking = {
        c: replace(
            chip,
            names=[n for n in chip.names if n != c],
            lexicon=[f for n, f in zip(chip.names, chip.lexicon, strict=True) if n != c],
        )
        for c in id_of.values()
    }
    said: dict[str, list[gate.Heard]] = {}
    outside: list[gate.Heard] = []
    for items, x in built_windows(root / data.VAL_COMMANDS.removesuffix(".txt")):
        if len(items) == 1:
            c, window = id_of[phrase_of[items[0]]], x[-listen.WINDOW_HOPS :]
            said.setdefault(c, []).append(chip_heard(chip, window))
            outside.append(chip_heard(lacking[c], window))
    text_of = {c.item: corpus.words(c.text or "") for c in screen.kept_clips(load_yaml(screen.CONFIG), paths, "speech")}
    texts = {
        c["id"]: corpus.words(c["text"]) for c in json.loads(command.COMMANDS.read_text(encoding="utf-8"))["commands"]
    }
    heard, meant = [], []
    for items, x in built_windows(root / "val"):
        h = chip_heard(chip, x[-listen.WINDOW_HOPS :])
        heard.append(h)
        meant.append(h.command != gate.REJECT and [w for i in items for w in text_of[i]] == texts[h.command])
    chosen = operating_point(said, heard, meant, outside, cfg["quant"]["thresholds"], cfg["eval"]["reject_sweep"])
    triple = tuple(chosen[k] for k in THRESHOLD_KEYS)
    gate3 = gate_row(chip, board_windows(cfg, net, paths), triple, chip_heard)
    out = thresholds_file(run, row, hearing)
    counts = {"val_commands": {c: len(hs) for c, hs in said.items()}, "val": len(heard)}
    body = chosen | {"gate3": gate3, "windows": counts}
    out.write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"{row}: delta1..3 {', '.join(map(str, triple))}: {chosen['chosen']}; Gate 3 at them {gate3}", flush=True)
    return out


def step_deploy(cfg: dict, run: Path, row: str, hearing: Hearing = LEARNT) -> tuple[Path, Path]:
    """The graph of row as firmware/models/command/ holds it: the .espdl streamed the run's chunk_hops at a time with a
    test sentence's first chunk stored for model->test(), as probe.py streams it, and the train statistics as the NORM
    entry; update_lock records both with the row's rungs and the listen hash. Refused for a run of another listen
    hash, and for a row whose input or output is not int8, which the chip would not run."""
    net = hearing.net(run, data_paths())
    learnt = net.cfg.get("listen_hash")
    if learnt != listen.HASH:
        raise ValueError(
            f"{run.name} learned on listen hash {learnt and hex(learnt)}, the firmware cuts by {hex(listen.HASH)} "
            "and would leave its command off (KEHOACH 6.3)"
        )
    graph = export_espdl.load_native(hearing.folder(run) / row / GRAPH_FILE)
    if (bits := ptq_espdl.io_bits(graph)) != (CHIP_BITS, CHIP_BITS):
        raise ValueError(f"row {row} reads {bits[0]} and gives {bits[1]} bits; the chip runs int8 at both ends")
    rungs = yaml.safe_load(ladder_file(run, hearing).read_text(encoding="utf-8"))["rows"][row]
    if not thresholds_file(run, row, hearing).is_file():
        raise ValueError(f"row {row} has no thresholds chosen: make ctc-thresholds RUN={run} ROW={row} first")
    chosen = yaml.safe_load(thresholds_file(run, row, hearing).read_text(encoding="utf-8"))
    x, chunk = test_sentence(cfg, net), net.cfg["chunk_hops"]
    io = ptq_espdl.io_of(graph)
    on_grid = ptq_espdl.to_int8(x, io.input_exponent).astype(np.float32) * np.float32(2.0**io.input_exponent)
    with esp_ppq_patches.applied(cfg["esp_ppq_patches"]):
        built = export_espdl.export(
            graph,
            hearing.folder(run) / row / "deploy" / f"{ENTRY}.espdl",
            streaming_input_shape=[1, x.shape[1], chunk],
            test_input=on_grid[..., :chunk],
        )
    folder = update_lock.MODELS / BRANCH
    folder.mkdir(parents=True, exist_ok=True)
    espdl, norm = folder / f"{ENTRY}.espdl", folder / f"{ENTRY}.norm.bin"
    espdl.write_bytes(built.read_bytes())
    norm.write_bytes(np.concatenate([net.mean, net.std]).astype("<f4").tobytes())
    files = [update_lock.Deployed(espdl, ENTRY, "espdl"), update_lock.Deployed(norm, ENTRY, "norm")]
    fields = {
        "backend": "ctc",
        "listen_hash": f"0x{listen.HASH:08x}",
        "features": {
            "name": f"log_mel{x.shape[1] - pitch.N_FEATURES}_pitch{pitch.N_FEATURES}",
            "dims": int(x.shape[1]),
        },
        "row": row,
        "rungs": {"calibration": rungs["calibration"], "int16_ops": rungs["int16_ops"]},
        "thresholds": {k: chosen[k] for k in THRESHOLD_KEYS},
    }
    if hearing != LEARNT:
        fields |= heard_head(hearing)
    return update_lock.record(BRANCH, run, files, fields)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["ptq", "int16", "qat", "thresholds", "deploy"])
    parser.add_argument("run", type=Path, help="a run directory of python -m srpipe.tasks.command.ctc.train")
    parser.add_argument("--row", help="thresholds, deploy: the row of <run>/int8/ladder.yaml")
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument(
        "--kaldi-pitch", action="store_true", help="hear the run on the contract's Kaldi pitch, as the board gives it"
    )
    parser.add_argument("--hold", choices=sorted(gate.HOLDS), help="pitch dims folded at the run's train mean")
    args = parser.parse_args(argv)
    cfg = apply_overrides(load_yaml(ctc.CONFIG), args.overrides)
    hearing = Hearing(args.kaldi_pitch, args.hold)
    if args.step in ("thresholds", "deploy") and args.row is None:
        parser.error(f"{args.step} needs --row")
    if args.step == "thresholds":
        print(step_thresholds(cfg, args.run, args.row, hearing))
    elif args.step == "deploy":
        for path in step_deploy(cfg, args.run, args.row, hearing):
            print(path)
    elif args.step == "qat":
        print(step_qat(cfg, args.run, "cuda" if torch.cuda.is_available() else "cpu", hearing))
    else:
        print({"ptq": step_ptq, "int16": step_int16}[args.step](cfg, args.run, hearing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
