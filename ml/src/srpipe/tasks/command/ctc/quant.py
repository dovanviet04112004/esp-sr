"""The quantisation ladder of the ctc net (KEHOACH 3.14, ADR-0013). Run: python -m srpipe.tasks.command.ctc.quant
ptq|int16|qat <run>: rungs 1 and 2, rung 3, rung 4, the last two on the calibration of rung 2 that Gate 3 rates best.
Each step adds rows to <run>/int8/ladder.yaml, the test set's unit error rate and Gate 3 on the board sessions after
int8 beside float, and keeps each row's graph under <run>/int8/<row>/ for probe.py.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import yaml
from torch import nn

from srpipe.compress.quant import export_espdl, mixed_espdl, ptq_espdl, qat_espdl
from srpipe.core import screen, splits
from srpipe.core.config import apply_overrides, data_paths, load_yaml
from srpipe.tasks import command
from srpipe.tasks.command import ctc
from srpipe.tasks.command import eval as gate
from srpipe.tasks.command.ctc import qat, train
from srpipe.tasks.command.ctc.model import encoder
from srpipe.tasks.wake.data import sentence_units

LADDER = "command_ctc"
GRAPH_FILE = "graph.native"
# The image names a branch's entries after its backend (KEHOACH 6.3).
ENTRY = "command_ctc"


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


def calibration(trained: dict, spec: dict, mean: np.ndarray, std: np.ndarray, root: Path) -> list[torch.Tensor]:
    """spec's calib_sentences sentences of the run's train files under root, drawn with spec's seed among those within
    spec's hops, normalised and padded as the net reads them."""
    found = []
    for listing in sorted(root.glob("train_*/*.items.jsonl")):
        for line in listing.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if item["n_frames"] <= spec["hops"]:
                found.append((listing, item["frame_offset"], item["n_frames"]))
    picks = np.random.default_rng(spec["seed"]).choice(len(found), spec["calib_sentences"], replace=False)
    out = []
    for listing, first, n in (found[k] for k in sorted(picks)):
        stem = str(listing).removesuffix(".items.jsonl")
        mel = np.load(stem + ".features.npy", mmap_mode="r")[first : first + n]
        pitch = np.load(stem + ".pitch.npy", mmap_mode="r")[first : first + n]
        out.append(torch.from_numpy(padded(np.concatenate([mel, pitch], axis=1), spec["hops"], mean, std)))
    return out


def batched(calib: list[torch.Tensor], batch: int) -> list[torch.Tensor]:
    """calib stacked batch sentences a tensor, for a graph built for that batch; a short last batch is left out."""
    return [torch.cat(calib[k : k + batch]) for k in range(0, len(calib) - batch + 1, batch)]


class Int8Net:
    """A quantised graph called as the float net is, on normalised features (1, dims, hops) of at most the graph's
    hops: padded on as training pads, the input on its int8 grid as the chip takes it, logits as the chip gives
    them."""

    def __init__(self, graph, hops: int, mean: np.ndarray, std: np.ndarray, like: encoder.CtcNet) -> None:
        self.simulate, self.io, self.hops = ptq_espdl.Simulator(graph), ptq_espdl.io_of(graph), hops
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


def gate_row(net: gate.Ctc, windows: list[gate.Scored], reject: int, margin: int) -> dict:
    """Gate 3 of net on the board windows: utterances whose best command is right, those accepted right at reject and
    margin, and the false accepts among the rest."""
    heard = [(s.expected, gate.ctc_heard(net, x)) for s in windows for x in s.decided]
    commands = [(e, h) for e, h in heard if e != gate.REJECT]
    others = [h for e, h in heard if e == gate.REJECT]
    accepted = [(e, h) for e, h in commands if h.accepted(reject, margin)]
    return {
        "best_right": f"{sum(h.command == e for e, h in commands)}/{len(commands)}",
        "accepted_right": f"{sum(h.command == e for e, h in accepted)}/{len(commands)}",
        "false_accepts": f"{sum(h.accepted(reject, margin) for h in others)}/{len(others)}",
    }


def quantized(model: nn.Module, calib: list[torch.Tensor], folder: Path, rungs: dict):
    """model quantised under rungs into folder, refused unless ESP-PPQ fused every norm into esp-dl's
    RMSNormalization, as the chip runs it: a norm left an int8 chain is another net."""
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
    """The windows LENH keeps on the board sessions of Gate 3: each utterance from vad-off back at most window_s."""
    longest = round(cfg["window_s"] * gate.HOPS_PER_S)
    said = {c["id"]: c["text"] for c in json.loads(command.COMMANDS.read_text(encoding="utf-8"))["commands"]}

    def window_of(clean, features, spans, tracker):
        return gate.ctc_windows(clean, features, spans, longest, tracker)

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
    return Bench(net, calibration(net.cfg, spec, net.mean, net.std, root), test, picks, windows)


def row_of(cfg: dict, b: Bench, model) -> dict:
    """The unit error rate of model, the float net or an Int8Net, over the picked test sentences, and its Gate 3."""
    spec, n = cfg["quant"], b.net
    errors = unit_error_rate(model, b.test, b.picks, spec["hops"], n.mean, n.std)
    heard = gate.Ctc(model, n.mean, n.std, n.names, n.lexicon, n.cfg)
    return {"unit_error_rate": round(errors, 4), **gate_row(heard, b.windows, spec["reject"], cfg["eval"]["margin"])}


def int8_row(cfg: dict, b: Bench, graph, folder: Path, rungs: dict) -> dict:
    """graph kept at folder/graph.native, and its row headed by the calibration and int16 layers it was built on."""
    export_espdl.save_native(graph, folder / GRAPH_FILE)
    int8 = Int8Net(graph, cfg["quant"]["hops"], b.net.mean, b.net.std, b.net.model)
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
        graph = quantized(b.net.model, b.calib, run / "int8" / name, rungs)
        out = recorded(run, head, {name: int8_row(cfg, b, graph, run / "int8" / name, rungs)})
    return out


def step_int16(cfg: dict, run: Path) -> Path:
    """Rung 3 on the best calibration: ESP-PPQ's per-layer error ranks the convolutions, and each row puts the worst
    of them at 16 bits."""
    spec, b = cfg["quant"], bench(cfg, run)
    rungs = ptq_espdl.ladder(LADDER) | {"calibration": best_calibration(run, spec["calibrations"], spec["gate_tie"])}
    base = quantized(b.net.model, b.calib, run / "int8" / "int16_base", rungs)
    ranked = mixed_espdl.ranked_layers(base, b.calib[: spec["layerwise_sentences"]])
    layers = [{"op": name, "noise_to_signal": round(error, 6)} for name, error in ranked]
    head = {"int16_base": rungs["calibration"], "layerwise": layers}
    out = recorded(run, head, {})
    for name, ops in mixed_espdl.int16_rows(ranked, spec["int16_tops"]).items():
        wide = rungs | {"int16_ops": ops}
        graph = quantized(b.net.model, b.calib, run / "int8" / name, wide)
        out = recorded(run, head, {name: int8_row(cfg, b, graph, run / "int8" / name, wide)})
    return out


def step_qat(cfg: dict, run: Path, device: str) -> Path:
    """Rung 4 on the best calibration: the graph built for quant.qat.batch learns with CTC, then what it learnt moves
    onto the graph of one, which the row measures; the val rows go to <run>/int8/qat/history.yaml."""
    spec, b = cfg["quant"], bench(cfg, run)
    rungs = ptq_espdl.ladder(LADDER) | {"calibration": best_calibration(run, spec["calibrations"], spec["gate_tie"])}
    folder = run / "int8" / "qat"
    wide = quantized(b.net.model, batched(b.calib, spec["qat"]["batch"]), folder / "batch", rungs)
    stats = (b.net.mean, b.net.std)
    history = qat.fit(wide, train.load_sets(b.net.cfg), stats, cfg, b.net.cfg, b.net.model, device)
    (folder / "history.yaml").write_text(yaml.safe_dump(history, sort_keys=False), encoding="utf-8")
    graph = quantized(b.net.model, b.calib, folder, rungs)
    qat_espdl.carry(wide, graph, b.calib[0].numpy())
    return recorded(run, {"qat": spec["qat"]}, {"qat": int8_row(cfg, b, graph, folder, rungs)})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["ptq", "int16", "qat"])
    parser.add_argument("run", type=Path, help="a run directory of python -m srpipe.tasks.command.ctc.train")
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    args = parser.parse_args(argv)
    cfg = apply_overrides(load_yaml(ctc.CONFIG), args.overrides)
    if args.step == "qat":
        print(step_qat(cfg, args.run, "cuda" if torch.cuda.is_available() else "cpu"))
    else:
        print({"ptq": step_ptq, "int16": step_int16}[args.step](cfg, args.run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
