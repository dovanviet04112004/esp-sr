"""Train the ctc net of command (E11-T12, KEHOACH 3.12) on board-simulated log-mel and pitch, write a run directory.

An example is one sentence of processed/command/<version> as the device computes its features; its target the lang_vi
units of its text read in the configured dialect, tones in the same sequence. Plain CTC, SpecAugment on the mel bands,
Adam with a cosine decay; val gives the CTC loss and the unit error rate of the best path.
Run: python -m srpipe.tasks.command.ctc.train [--set train.steps=40000] [--resume RUN]"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import yaml
from torch.nn import functional

from srpipe.core import screen, splits
from srpipe.core.config import apply_overrides, data_paths, load_yaml
from srpipe.core.run_dir import create_run_dir
from srpipe.core.seed import seed_everything
from srpipe.dsp.spec import pitch
from srpipe.generated import grid
from srpipe.tasks.command import ctc
from srpipe.tasks.command.ctc.model import encoder
from srpipe.tasks.wake.data import sentence_units

BRANCH = "command_ctc"
HOPS_PER_S = grid.SAMPLE_RATE_HZ / grid.HOP_SAMPLES
VAL_BATCH = 64


@dataclass
class Sentences:
    """Sentences of one role end to end: features (hops, dims), and per sentence its first hop, hops and units."""

    features: np.ndarray
    first: np.ndarray
    hops: np.ndarray
    units: list[np.ndarray]  # lang_vi unit ids, shifted past the CTC blank


def load_role(folders: list[Path], units_of: dict[str, list[int]], longest: int, dtype: str) -> Sentences:
    """Every sentence of the finished builds in folders that has lang_vi units and at most longest hops."""
    features, first, hops, units, offset = [], [], [], [], 0
    for folder in folders:
        built = yaml.safe_load((folder / "manifest.yaml").read_text(encoding="utf-8"))
        if not built.get("pitch"):
            raise ValueError(f"{folder} was simulated without pitch")
        for name in sorted(n for n in built["sha256"] if n.endswith(".items.jsonl")):
            stem = str(folder / name).removesuffix(".items.jsonl")
            mel = np.load(stem + ".features.npy")
            features.append(np.concatenate([mel, np.load(stem + ".pitch.npy")], axis=1).astype(dtype))
            for line in (folder / name).read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                said = units_of.get(item["item"].split("@")[0])
                if said and item["n_frames"] <= longest:
                    first.append(offset + item["frame_offset"])
                    hops.append(item["n_frames"])
                    units.append(np.asarray(said, dtype=np.int64) + 1)
            offset += len(mel)
    if not first:
        raise ValueError(f"no sentence of {[f.name for f in folders]} has units within {longest} hops")
    return Sentences(np.concatenate(features), np.array(first), np.array(hops), units)


def feature_stats(features: np.ndarray, block: int = 1 << 20) -> tuple[np.ndarray, np.ndarray]:
    """Mean and deviation of every dim over every hop, accumulated in float64."""
    total, square = np.zeros(features.shape[1]), np.zeros(features.shape[1])
    for k in range(0, len(features), block):
        x = features[k : k + block].astype(np.float64)
        total, square = total + x.sum(axis=0), square + (x * x).sum(axis=0)
    mean = total / len(features)
    return mean.astype(np.float32), np.sqrt(square / len(features) - mean * mean).astype(np.float32)


def batch_of(data: Sentences, picks: np.ndarray, multiple: int) -> tuple[np.ndarray, np.ndarray, list[np.ndarray]]:
    """The picked sentences zero-padded at the end to the longest, rounded up to multiple: (batch, hops, dims) float32,
    their hops and units."""
    width = -(-int(data.hops[picks].max()) // multiple) * multiple
    x = np.zeros((len(picks), width, data.features.shape[1]), dtype=np.float32)
    for row, k in enumerate(picks):
        x[row, : data.hops[k]] = data.features[data.first[k] : data.first[k] + data.hops[k]]
    return x, data.hops[picks], [data.units[k] for k in picks]


def mask(x: np.ndarray, hops: np.ndarray, spec: dict, n_mel: int, rng: np.random.Generator) -> None:
    """SpecAugment in place on normalised sentences: zero over spans of mel bands and spans of each one's hops."""
    for w, n in zip(x, hops, strict=True):
        for _ in range(spec["bands"]):
            width = int(rng.integers(spec["band_width"] + 1))
            at = int(rng.integers(n_mel - width + 1))
            w[:n, at : at + width] = 0.0
        for _ in range(spec["hops"]):
            width = min(int(rng.integers(spec["hop_width"] + 1)), int(n))
            at = int(rng.integers(n - width + 1))
            w[at : at + width] = 0.0


def frames_of(hops: np.ndarray, stride: int) -> np.ndarray:
    """CTC frames the net gives for each sentence's hops."""
    return -(-hops // stride)


def log_probs_of(net: encoder.CtcNet, x: torch.Tensor) -> torch.Tensor:
    """Per-frame log-probabilities (batch, classes, frames) of normalised sentences (batch, hops, dims)."""
    return net(x.transpose(1, 2)).log_softmax(1)


def ctc_loss(log_probs: torch.Tensor, frames: np.ndarray, units: list[np.ndarray]) -> torch.Tensor:
    return functional.ctc_loss(
        log_probs.permute(2, 0, 1),
        torch.from_numpy(np.concatenate(units)).to(log_probs.device),
        torch.from_numpy(frames),
        torch.tensor([len(u) for u in units]),
        blank=encoder.BLANK,
        zero_infinity=True,
    )


def best_path(log_probs: np.ndarray) -> list[int]:
    """The classes of the most likely frame sequence, repeats merged and blanks dropped."""
    path = log_probs.argmax(axis=0)
    return [int(c) for k, c in enumerate(path) if c != encoder.BLANK and (k == 0 or c != path[k - 1])]


def edit_distance(a: list[int], b: list[int] | np.ndarray) -> int:
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, start=1):
        diagonal, row[0] = row[0], i
        for j, y in enumerate(b, start=1):
            diagonal, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, diagonal + int(x != y))
    return row[-1]


def evaluate(net: encoder.CtcNet, data: Sentences, stats: tuple, device: str) -> dict:
    """Mean CTC loss of val and the unit error rate of the best path over every val sentence."""
    mean, std = stats
    stride = net.front.hop_stride
    losses, errors, total = [], 0, 0
    with torch.no_grad():
        for k in range(0, len(data.first), VAL_BATCH):
            picks = np.arange(k, min(k + VAL_BATCH, len(data.first)))
            x, hops, units = batch_of(data, picks, net.chunk_multiple)
            log_probs = log_probs_of(net, torch.from_numpy((x - mean) / std).to(device))
            frames = frames_of(hops, stride)
            losses.append(float(ctc_loss(log_probs, frames, units)) * len(picks))
            heard = log_probs.cpu().numpy()
            for row, n in enumerate(frames):
                errors += edit_distance(best_path(heard[row, :, :n]), units[row])
                total += len(units[row])
    return {"loss": sum(losses) / len(data.first), "unit_error_rate": errors / total}


def checkpoint(run: Path, step: int | None = None) -> Path:
    """An evaluated step's weights, or with no step the last state a resumed run goes on from."""
    return run / "checkpoints" / ("last.pt" if step is None else f"step_{step:06d}.pt")


def train(cfg: dict, sets: dict[str, Sentences], device: str, run: Path | None = None, resume: bool = False) -> tuple:
    """The net of the last step, the feature statistics and one row of val figures per evaluation. With run, each
    evaluated net is saved beside the state the run goes on from when resumed: weights, optimiser, schedule, the
    batch draws and the history, so a resumed run ends where an unbroken one would."""
    spec = cfg["train"]
    rng = seed_everything(spec["seed"])
    data = sets["train"]
    mean, std = feature_stats(data.features)
    net = encoder.build(cfg).to(device)
    optimiser = torch.optim.Adam(net.parameters(), lr=spec["learning_rate"])
    final = spec["final_learning_rate"] / spec["learning_rate"]
    schedule = torch.optim.lr_scheduler.LambdaLR(
        optimiser, lambda step: final + (1 - final) * 0.5 * (1 + math.cos(math.pi * step / spec["steps"]))
    )
    history, first = [], 1
    if resume:
        state = torch.load(checkpoint(run), map_location=device, weights_only=False)
        net.load_state_dict(state["model"])
        optimiser.load_state_dict(state["optimiser"])
        schedule.load_state_dict(state["schedule"])
        rng.bit_generator.state = state["draws"]
        history, first = state["history"], state["step"] + 1
    n_mel = data.features.shape[1] - pitch.N_FEATURES
    hours = data.hops.sum() / HOPS_PER_S / splits.SECONDS_PER_HOUR
    said = f"{len(data.first)} sentences, {hours:.1f} h"
    print(f"{said}; steps {first} to {spec['steps']} of {spec['batch']}", flush=True)
    losses = []
    for step in range(first, spec["steps"] + 1):
        x, hops, units = batch_of(data, rng.integers(len(data.first), size=spec["batch"]), net.chunk_multiple)
        x = (x - mean) / std
        mask(x, hops, spec["masks"], n_mel, rng)
        log_probs = log_probs_of(net, torch.from_numpy(x).to(device))
        loss = ctc_loss(log_probs, frames_of(hops, net.front.hop_stride), units)
        optimiser.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(net.parameters(), spec["clip_norm"])
        optimiser.step()
        schedule.step()
        losses.append(loss.item())
        if step % spec["eval_every"] == 0 or step == spec["steps"]:
            net.eval()
            row = {"step": step, "train_loss": float(np.mean(losses)), "lr": schedule.get_last_lr()[0]}
            row |= evaluate(net, sets["val"], (mean, std), device)
            net.train()
            losses = []
            history.append(row)
            print(" ".join(f"{k} {v:.4g}" for k, v in row.items()), flush=True)
            if run:
                checkpoint(run, step).parent.mkdir(parents=True, exist_ok=True)
                torch.save(net.state_dict(), checkpoint(run, step))
                state = {"model": net.state_dict(), "optimiser": optimiser.state_dict(), "step": step}
                state |= {"schedule": schedule.state_dict(), "draws": rng.bit_generator.state, "history": history}
                torch.save(state, checkpoint(run))
    net.eval()
    return net, (mean, std), history


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--resume", type=Path, metavar="RUN", help="go on from a run's last checkpoint")
    args = parser.parse_args(argv)
    if args.resume:
        cfg = load_yaml(args.resume / "config.resolved.yaml")
    else:
        cfg = apply_overrides(load_yaml(ctc.CONFIG), args.overrides)
    paths = data_paths()
    spec, version = cfg["train"], cfg["split"]["version"]
    folder = paths["splits"] / "command" / version
    split_files = sorted(folder.glob("*.txt"))
    root = paths["processed"] / "command" / version
    roles = {"train": [f for f in split_files if splits.role_of(f.name) == "train"], "val": [folder / "val.txt"]}
    listed = {r.item for files in roles.values() for f in files for r in splits.read_split(f)}
    clips = screen.kept_clips(load_yaml(screen.CONFIG), paths, "speech")
    units_of = sentence_units(clips, listed, spec["dialect"])
    longest = round(spec["max_s"] * HOPS_PER_S)
    sets = {
        role: load_role([root / f.stem for f in files], units_of, longest, dtype)
        for (role, files), dtype in zip(roles.items(), ("float16", "float32"), strict=True)
    }
    device = "cuda" if torch.cuda.is_available() else "cpu"
    run = args.resume or create_run_dir(paths["artifacts"], BRANCH, cfg, split_files)
    net, (mean, std), history = train(cfg, sets, device, run, bool(args.resume))
    torch.save(net.state_dict(), run / "model.pt")
    np.savez(run / "feature_stats.npz", mean=mean, std=std)
    report = {"val": history[-1], "history": history}
    (run / "metrics.yaml").write_text(yaml.safe_dump(report, sort_keys=False), encoding="utf-8")
    print(f"{run}\n" + yaml.safe_dump({"val": history[-1]}, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
