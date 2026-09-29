"""Train the wake TCN on board-simulated log-mel (E11-T11, KEHOACH 3.11) and write a run directory.

Windows around a positive's end, a hard near miss (a TTS phrase's end, anywhere in a corpus sentence's speech) or
anywhere in a negative, with fixed shares of positives and hard negatives per batch; per-hop BCE past the warm-up; the
weights with the best val recall at the false-accept target are kept and scored on test_neg too. Train sets are held as
float16, far finer than the board's int8 input; val and test stay float32. Run: python -m srpipe.tasks.wake.train"""

from __future__ import annotations

import argparse
import copy
import math

import numpy as np
import torch
import yaml
from torch.nn import functional

from srpipe.core.config import data_paths, load_yaml
from srpipe.core.run_dir import create_run_dir
from srpipe.core.seed import seed_everything
from srpipe.tasks.wake import CONFIG
from srpipe.tasks.wake.data import Shard, label_hops, load_set
from srpipe.tasks.wake.eval import operating_point, sweep
from srpipe.tasks.wake.model.tcn import Tcn

# Per processed file: whether every item says the wake word, and the dtype its features are held in.
SETS = {
    "train_pos": (True, "float16"),
    "train_neg": (False, "float16"),
    "train_hard": (False, "float16"),
    "val_pos": (True, "float32"),
    "val_neg": (False, "float32"),
    "val_hard": (False, "float32"),
    "test_neg": (False, "float32"),
}
OPTIONAL = ("train_hard", "val_hard")  # wake/v1 has none


def band_stats(shards: list[Shard]) -> tuple[np.ndarray, np.ndarray]:
    """Mean and standard deviation of every band over the training hops, in float64 before they are stored."""
    total, square, count = 0.0, 0.0, 0
    for shard in shards:
        x = shard.features.astype(np.float64)
        total, square, count = total + x.sum(axis=0), square + (x * x).sum(axis=0), count + len(x)
    mean = total / count
    return mean.astype(np.float32), np.sqrt(square / count - mean * mean).astype(np.float32)


class Windows:
    """Examples of window_hops hops: one that holds a positive's whole label past the warm-up, one over a hard near
    miss, or one anywhere in a negative."""

    def __init__(
        self,
        positives: list[Shard],
        negatives: list[Shard],
        cfg: dict,
        rng: np.random.Generator,
        hard: list[Shard] | None = None,
    ) -> None:
        self.cfg, self.rng = cfg["train"], rng
        self.positives, self.negatives, self.hard = positives, negatives, hard or []
        self.before, self.after = label_hops(self.cfg["label_s"])
        width = self.cfg["window_hops"]
        if short := [len(s.features) for s in positives + negatives + self.hard if len(s.features) < width]:
            raise ValueError(f"a shard of {min(short)} hops is shorter than a {width}-hop window")
        self.ends = [(k, i["frame_offset"] + i["speech_frames"][1]) for k, s in enumerate(positives) for i in s.items]
        self.hard_items = [(k, i) for k, s in enumerate(self.hard) for i in s.items]
        self.weights = np.array([len(s.features) for s in negatives], dtype=np.float64)
        self.weights /= self.weights.sum()
        self.slack = width - self.cfg["warmup_hops"] - self.before - self.after
        if self.slack < 1:
            raise ValueError("window_hops leaves no room for a whole label past the warm-up")

    def cut(self, shard: Shard, stop: int) -> tuple[np.ndarray, np.ndarray]:
        width = self.cfg["window_hops"]
        start = min(max(0, stop - width), len(shard.features) - width)
        return shard.features[start : start + width], shard.labels[start : start + width]

    def counts(self) -> tuple[int, int, int]:
        """Positives, hard negatives and other negatives of a batch."""
        n = self.cfg["batch"]
        n_pos = round(n * self.cfg["positive_share"])
        n_hard = round(n * self.cfg.get("hard_share", 0.0)) if self.hard_items else 0
        return n_pos, n_hard, n - n_pos - n_hard

    def hard_stop(self, item: dict) -> int:
        """A TTS phrase ends inside the loss region as a positive would; a corpus sentence has no word timing, so the
        window ends anywhere in its speech."""
        first = item["frame_offset"] + item["speech_frames"][0]
        end = item["frame_offset"] + item["speech_frames"][1]
        if item["origin"] == "synth":
            return end + self.after + int(self.rng.integers(self.slack))
        return int(self.rng.integers(first + 1, end + self.after + 1))

    def batch(self) -> tuple[np.ndarray, np.ndarray]:
        width = self.cfg["window_hops"]
        n_pos, n_hard, n_neg = self.counts()
        xs, ys = [], []
        for k in self.rng.integers(len(self.ends), size=n_pos):
            shard_index, end = self.ends[k]
            stop = end + self.after + int(self.rng.integers(self.slack))
            x, y = self.cut(self.positives[shard_index], stop)
            xs.append(x), ys.append(y)
        for k in self.rng.integers(len(self.hard_items), size=n_hard) if n_hard else []:
            shard_index, item = self.hard_items[k]
            x, y = self.cut(self.hard[shard_index], self.hard_stop(item))
            xs.append(x), ys.append(y)
        for k in self.rng.choice(len(self.negatives), size=n_neg, p=self.weights):
            shard = self.negatives[k]
            x, y = self.cut(shard, int(self.rng.integers(width, len(shard.features) + 1)))
            xs.append(x), ys.append(y)
        return np.stack(xs), np.stack(ys)


def coverage(cfg: dict, windows: Windows) -> str:
    """How often training sees each positive, each hard near miss and each negative hop, for the run's log."""
    spec = cfg["train"]
    n_pos, n_hard, n_neg = windows.counts()
    negative_hops = sum(len(s.features) for s in windows.negatives)
    visits = spec["steps"] * n_pos / len(windows.ends)
    passes = spec["steps"] * n_neg * spec["window_hops"] / negative_hops
    line = (
        f"{spec['steps']} steps: each of {len(windows.ends)} positives about {visits:.0f} times,"
        f" each of {negative_hops} negative hops about {passes:.1f} times"
    )
    if n_hard:
        hard_visits = spec["steps"] * n_hard / len(windows.hard_items)
        line += f", each of {len(windows.hard_items)} hard near misses about {hard_visits:.0f} times"
    return line


def train(cfg: dict, sets: dict[str, list[Shard]], device: str) -> tuple[Tcn, dict, list[dict]]:
    """The kept weights, the band statistics and one row of val figures per evaluation."""
    spec, rng = cfg["train"], seed_everything(cfg["train"]["seed"])
    mean, std = band_stats(sets["train_pos"] + sets["train_neg"])
    n_bands = len(mean)
    model = Tcn(n_bands, **cfg["model"]).to(device)
    optimiser = torch.optim.Adam(model.parameters(), lr=spec["learning_rate"])
    final = spec["final_learning_rate"] / spec["learning_rate"]
    schedule = torch.optim.lr_scheduler.LambdaLR(
        optimiser, lambda step: final + (1 - final) * 0.5 * (1 + math.cos(math.pi * step / spec["steps"]))
    )
    windows = Windows(sets["train_pos"], sets["train_neg"], cfg, rng, sets.get("train_hard"))
    mean_t, std_t = torch.tensor(mean, device=device)[:, None], torch.tensor(std, device=device)[:, None]
    best, history, losses = None, [], []
    print(coverage(cfg, windows), flush=True)
    for step in range(1, spec["steps"] + 1):
        x, y = windows.batch()
        x = (torch.from_numpy(x.astype(np.float32)).to(device).transpose(1, 2) - mean_t) / std_t
        y = torch.from_numpy(y.astype(np.float32)).to(device)
        logits = model(x)[:, 0, spec["warmup_hops"] :]
        loss = functional.binary_cross_entropy_with_logits(logits, y[:, spec["warmup_hops"] :])
        optimiser.zero_grad()
        loss.backward()
        optimiser.step()
        schedule.step()
        losses.append(float(loss))
        if step % spec["eval_every"] == 0:
            model.eval()
            val = sweep(model, sets["val_pos"], sets["val_neg"] + sets.get("val_hard", []), mean, std, cfg, device)
            model.train()
            threshold, recall, rate = operating_point(val, cfg["eval"]["false_accepts_per_hour"])
            row = {"step": step, "loss": float(np.mean(losses)), "lr": schedule.get_last_lr()[0]}
            row |= {"threshold": threshold, "recall": recall, "fa_per_hour": rate}
            losses = []
            history.append(row)
            print(" ".join(f"{k} {v:.4g}" for k, v in row.items()), flush=True)
            if best is None or (recall, -rate) > (best["recall"], -best["fa_per_hour"]):
                best = row | {"state": copy.deepcopy(model.state_dict())}
    model.load_state_dict(best.pop("state"))
    model.eval()
    return model, {"mean": mean, "std": std, "best": best}, history


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    cfg, paths = load_yaml(CONFIG), data_paths()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    root = paths["processed"] / "wake" / cfg["train"]["split"]
    around = label_hops(cfg["train"]["label_s"])
    sets = {
        name: load_set(root / name, positive, around, dtype)
        for name, (positive, dtype) in SETS.items()
        if name not in OPTIONAL or (root / name).exists()
    }
    split_files = sorted((paths["splits"] / "wake" / cfg["train"]["split"]).glob("*.txt"))
    run = create_run_dir(paths["artifacts"], "wake", cfg, split_files)
    model, stats, history = train(cfg, sets, device)
    threshold = stats["best"]["threshold"]
    report = {"val": stats["best"], "history": history}
    for name in ("val", "test"):
        pos = sets.get(f"{name}_pos", [])
        negatives = sets[f"{name}_neg"] + sets.get(f"{name}_hard", [])
        result = sweep(model, pos, negatives, stats["mean"], stats["std"], cfg, device)
        k = int(np.argmin(np.abs(result.thresholds - threshold)))
        report[name] = {
            "threshold": threshold,
            "recall": float(result.recall[k]) if pos else None,
            "fa_per_hour": float(result.false_accepts_per_hour[k]),
            "positives": result.positives,
            "negative_hours": round(result.negative_hours, 2),
        }
    torch.save(model.state_dict(), run / "model.pt")
    np.savez(run / "band_stats.npz", mean=stats["mean"], std=stats["std"])
    (run / "metrics.yaml").write_text(yaml.safe_dump(report, sort_keys=False), encoding="utf-8")
    print(f"{run}\n" + yaml.safe_dump({k: report[k] for k in ("val", "test")}, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
