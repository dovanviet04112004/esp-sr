"""The quantisation ladder of the ctc net (KEHOACH 3.14, ADR-0013). Run: python -m srpipe.tasks.command.ctc.quant
ptq|int16|qat <run> | deploy <run> --row <row>: rungs 1 and 2, rung 3, rung 4, the last two on the calibration of
rung 2 that Gate 3 rates best. Each adds rows to <run>/int8/ladder.yaml, the test set's unit error rate and Gate 3 on
the board sessions after int8 beside float, and keeps each row's graph under <run>/int8/<row>/ for probe.py. deploy
puts a row's graph into firmware/models/command/ and records it with update_lock (E11-T19).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import yaml
from torch import nn

from srpipe.compress.quant import esp_ppq_patches, export_espdl, mixed_espdl, ptq_espdl, qat_espdl
from srpipe.core import screen, splits
from srpipe.core.config import apply_overrides, data_paths, load_yaml
from srpipe.dsp.spec import pitch
from srpipe.export import update_lock
from srpipe.generated import listen
from srpipe.tasks import command
from srpipe.tasks.command import ctc
from srpipe.tasks.command import eval as gate
from srpipe.tasks.command.ctc import qat, train
from srpipe.tasks.command.ctc.model import encoder
from srpipe.tasks.wake.data import sentence_units

LADDER = "command_ctc"
GRAPH_FILE = "graph.native"
# The image names a branch's entries after its backend (KEHOACH 6.3).
BRANCH, ENTRY = "command", "command_ctc"
CHIP_BITS = 8  # espdl_net views int8 tensors only (KEHOACH 6.3)


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


def gate_row(
    net: gate.Ctc, windows: list[gate.Scored], reject: int, margin: int, heard_by: Callable = gate.ctc_heard
) -> dict:
    """Gate 3 of net on the board windows, each decided by heard_by(net, window): utterances whose best command is
    right, those accepted right at reject and margin, and the false accepts among the rest."""
    heard = [(s.expected, heard_by(net, x)) for s in windows for x in s.decided]
    commands = [(e, h) for e, h in heard if e != gate.REJECT]
    others = [h for e, h in heard if e == gate.REJECT]
    accepted = [(e, h) for e, h in commands if h.accepted(reject, margin)]
    return {
        "best_right": f"{sum(h.command == e for e, h in commands)}/{len(commands)}",
        "accepted_right": f"{sum(h.command == e for e, h in accepted)}/{len(commands)}",
        "false_accepts": f"{sum(h.accepted(reject, margin) for h in others)}/{len(others)}",
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


def bench(cfg: dict, run: Path) -> Bench:
    """The run's net, its calibration, quant.test_sentences test sentences drawn with the seed, and the board
    windows."""
    spec, paths = cfg["quant"], data_paths()
    net = gate.load_ctc(run)
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
    """The unit error rate of model, the float net or an Int8Net, over the picked test sentences, and its Gate 3."""
    spec, n = cfg["quant"], b.net
    errors = unit_error_rate(model, b.test, b.picks, spec["hops"], n.mean, n.std)
    heard = gate.Ctc(model, n.mean, n.std, n.names, n.lexicon, n.cfg)
    return {"unit_error_rate": round(errors, 4), **gate_row(heard, b.windows, spec["reject"], cfg["eval"]["margin"])}


def int8_row(cfg: dict, b: Bench, graph, folder: Path, rungs: dict) -> dict:
    """graph kept at folder/graph.native, and its row headed by the calibration and int16 layers it was built on."""
    export_espdl.save_native(graph, folder / GRAPH_FILE)
    int8 = Int8Net(graph, cfg["quant"]["hops"], b.net.mean, b.net.std, b.net.model, cfg["esp_ppq_patches"])
    return {"calibration": rungs["calibration"], "int16_ops": rungs["int16_ops"], **row_of(cfg, b, int8)}


def ladder_file(run: Path) -> Path:
    return run / "int8" / "ladder.yaml"


def recorded(run: Path, head: dict, rows: dict) -> Path:
    """head and rows merged into the run's ladder.yaml, rows of the other steps kept; each row printed."""
    for name, row in rows.items():
        print(f"{name}: {row}", flush=True)
    out = ladder_file(run)
    kept = yaml.safe_load(out.read_text(encoding="utf-8")) if out.is_file() else {}
    merged = kept | head | {"rows": kept.get("rows", {}) | rows}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(merged, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return out


def counted(share: str) -> int:
    return int(share.split("/")[0])


def best_calibration(run: Path, calibrations: list[str], tie: int) -> str:
    """The calibration of rung 2 rated best (KEHOACH 3.14): among those within tie commands accepted right of the
    most, the lowest unit error rate on the test set, then the fewest false accepts."""
    rows = yaml.safe_load(ladder_file(run).read_text(encoding="utf-8"))["rows"] if ladder_file(run).is_file() else {}
    missing = [c for c in calibrations if c not in rows]
    if missing:
        raise ValueError(f"{ladder_file(run)} has no row of {missing}: run the ptq step first")
    most = max(counted(rows[c]["accepted_right"]) for c in calibrations)
    tied = [c for c in calibrations if counted(rows[c]["accepted_right"]) >= most - tie]
    return min(tied, key=lambda c: (rows[c]["unit_error_rate"], counted(rows[c]["false_accepts"])))


def step_ptq(cfg: dict, run: Path) -> Path:
    """Rungs 1 and 2: the float row, then a row of the net quantised with each calibration."""
    spec, b = cfg["quant"], bench(cfg, run)
    head = {"rungs": ptq_espdl.ladder(LADDER), "quant": spec, "test_sentences": len(b.picks)}
    out = recorded(run, head, {"float": row_of(cfg, b, b.net.model)})
    for name in spec["calibrations"]:
        rungs = ptq_espdl.ladder(LADDER) | {"calibration": name}
        graph = quantized(b.net.model, b.calib, run / "int8" / name, rungs, cfg["esp_ppq_patches"])
        out = recorded(run, head, {name: int8_row(cfg, b, graph, run / "int8" / name, rungs)})
    return out


def step_int16(cfg: dict, run: Path) -> Path:
    """Rung 3 on the best calibration: ESP-PPQ's per-layer error ranks the convolutions, and each row puts the worst
    of them at 16 bits."""
    spec, b = cfg["quant"], bench(cfg, run)
    rungs = ptq_espdl.ladder(LADDER) | {"calibration": best_calibration(run, spec["calibrations"], spec["gate_tie"])}
    base = quantized(b.net.model, b.calib, run / "int8" / "int16_base", rungs, cfg["esp_ppq_patches"])
    with esp_ppq_patches.applied(cfg["esp_ppq_patches"]):
        ranked = mixed_espdl.ranked_layers(base, b.calib[: spec["layerwise_sentences"]])
    layers = [{"op": name, "noise_to_signal": round(error, 6)} for name, error in ranked]
    head = {"int16_base": rungs["calibration"], "layerwise": layers}
    out = recorded(run, head, {})
    for name, ops in mixed_espdl.int16_rows(ranked, spec["int16_tops"]).items():
        wide = rungs | {"int16_ops": ops}
        graph = quantized(b.net.model, b.calib, run / "int8" / name, wide, cfg["esp_ppq_patches"])
        out = recorded(run, head, {name: int8_row(cfg, b, graph, run / "int8" / name, wide)})
    return out


def step_qat(cfg: dict, run: Path, device: str) -> Path:
    """Rung 4 on the best calibration: the graph built for quant.qat.batch learns with CTC, then what it learnt moves
    onto the graph of one, which the row measures; the val rows go to <run>/int8/qat/history.yaml."""
    spec, b = cfg["quant"], bench(cfg, run)
    rungs = ptq_espdl.ladder(LADDER) | {"calibration": best_calibration(run, spec["calibrations"], spec["gate_tie"])}
    folder, patches = run / "int8" / "qat", cfg["esp_ppq_patches"]
    wide = quantized(b.net.model, batched(b.calib, spec["qat"]["batch"]), folder / "batch", rungs, patches)
    stats = (b.net.mean, b.net.std)
    with esp_ppq_patches.applied(patches):
        history = qat.fit(wide, train.load_sets(b.net.cfg), stats, cfg, b.net.cfg, b.net.model, device)
    (folder / "history.yaml").write_text(yaml.safe_dump(history, sort_keys=False), encoding="utf-8")
    graph = quantized(b.net.model, b.calib, folder, rungs, patches)
    with esp_ppq_patches.applied(patches):
        qat_espdl.carry(wide, graph, b.calib[0].numpy())
    return recorded(run, {"qat": spec["qat"]}, {"qat": int8_row(cfg, b, graph, folder, rungs)})


def step_deploy(cfg: dict, run: Path, row: str) -> tuple[Path, Path]:
    """The graph of row as firmware/models/command/ holds it: the .espdl streamed the run's chunk_hops at a time with a
    test sentence's first chunk stored for model->test(), as probe.py streams it, and the train statistics as the NORM
    entry; update_lock records both with the row's rungs and the listen hash. Refused for a run of another listen
    hash, and for a row whose input or output is not int8, which the chip would not run."""
    net = gate.load_ctc(run)
    learnt = net.cfg.get("listen_hash")
    if learnt != listen.HASH:
        raise ValueError(
            f"{run.name} learned on listen hash {learnt and hex(learnt)}, the firmware cuts by {hex(listen.HASH)} "
            "and would leave its command off (KEHOACH 6.3)"
        )
    graph = export_espdl.load_native(run / "int8" / row / GRAPH_FILE)
    if (bits := ptq_espdl.io_bits(graph)) != (CHIP_BITS, CHIP_BITS):
        raise ValueError(f"row {row} reads {bits[0]} and gives {bits[1]} bits; the chip runs int8 at both ends")
    rungs = yaml.safe_load(ladder_file(run).read_text(encoding="utf-8"))["rows"][row]
    x, chunk = test_sentence(cfg, net), net.cfg["chunk_hops"]
    io = ptq_espdl.io_of(graph)
    on_grid = ptq_espdl.to_int8(x, io.input_exponent).astype(np.float32) * np.float32(2.0**io.input_exponent)
    with esp_ppq_patches.applied(cfg["esp_ppq_patches"]):
        built = export_espdl.export(
            graph,
            run / "int8" / row / "deploy" / f"{ENTRY}.espdl",
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
    }
    return update_lock.record(BRANCH, run, files, fields)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["ptq", "int16", "qat", "deploy"])
    parser.add_argument("run", type=Path, help="a run directory of python -m srpipe.tasks.command.ctc.train")
    parser.add_argument("--row", help="deploy: the row of <run>/int8/ladder.yaml to deploy")
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    args = parser.parse_args(argv)
    cfg = apply_overrides(load_yaml(ctc.CONFIG), args.overrides)
    if args.step == "deploy":
        if args.row is None:
            parser.error("deploy needs --row")
        for path in step_deploy(cfg, args.run, args.row):
            print(path)
    elif args.step == "qat":
        print(step_qat(cfg, args.run, "cuda" if torch.cuda.is_available() else "cpu"))
    else:
        print({"ptq": step_ptq, "int16": step_int16}[args.step](cfg, args.run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
