"""Train the wake TCN on board-simulated log-mel (E11-T11, KEHOACH 3.11) and write a run directory.

Windows around a positive's end or anywhere in a negative, a fixed share of positives per batch, per-hop BCE past the
warm-up; the weights with the best val recall at the false-accept target are kept and scored on test_neg too. Train
sets are held as float16 to fit in memory, far finer than the board's int8 input; val and test stay float32.
Run: python -m srpipe.tasks.wake.train"""

from __future__ import annotations

import argparse
import copy

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
    "val_pos": (True, "float32"),
    "val_neg": (False, "float32"),
    "test_neg": (False, "float32"),
}


def band_stats(shards: list[Shard]) -> tuple[np.ndarray, np.ndarray]:
    """Mean and standard deviation of every band over the training hops, in float64 before they are stored."""
    total, square, count = 0.0, 0.0, 0
    for shard in shards:
        x = shard.features.astype(np.float64)
        total, square, count = total + x.sum(axis=0), square + (x * x).sum(axis=0), count + len(x)
    mean = total / count
    return mean.astype(np.float32), np.sqrt(square / count - mean * mean).astype(np.float32)


class Windows:
    """Examples of window_hops hops: around the labelled end of a positive, or anywhere in a negative."""

    def __init__(self, positives: list[Shard], negatives: list[Shard], cfg: dict, rng: np.random.Generator) -> None:
        self.cfg, self.rng = cfg["train"], rng
        self.positives, self.negatives = positives, negatives
        self.ends = [
            (k, int(e)) for k, s in enumerate(positives) for e in np.flatnonzero(np.diff(s.labels.astype(int)) < 0)
        ]
        self.weights = np.array([len(s.features) for s in negatives], dtype=np.float64)
        self.weights /= self.weights.sum()

    def cut(self, shard: Shard, stop: int) -> tuple[np.ndarray, np.ndarray]:
        width = self.cfg["window_hops"]
        start = min(max(0, stop - width), max(0, len(shard.features) - width))
        return shard.features[start : start + width], shard.labels[start : start + width]

    def batch(self) -> tuple[np.ndarray, np.ndarray]:
        n, width, warm = self.cfg["batch"], self.cfg["window_hops"], self.cfg["warmup_hops"]
        n_pos = round(n * self.cfg["positive_share"])
        xs, ys = [], []
        for k in self.rng.integers(len(self.ends), size=n_pos):
            shard_index, end = self.ends[k]
            stop = end + 1 + int(self.rng.integers(width - warm))
            x, y = self.cut(self.positives[shard_index], stop)
            xs.append(x), ys.append(y)
        for k in self.rng.choice(len(self.negatives), size=n - n_pos, p=self.weights):
            shard = self.negatives[k]
            x, y = self.cut(shard, int(self.rng.integers(width, max(width, len(shard.features)) + 1)))
            xs.append(x), ys.append(y)
        return np.stack(xs), np.stack(ys)


def train(cfg: dict, sets: dict[str, list[Shard]], device: str) -> tuple[Tcn, dict, list[dict]]:
    """The kept weights, the band statistics and one row of val figures per evaluation."""
    spec, rng = cfg["train"], seed_everything(cfg["train"]["seed"])
    mean, std = band_stats(sets["train_pos"] + sets["train_neg"])
    n_bands = len(mean)
    model = Tcn(n_bands, **cfg["model"]).to(device)
    optimiser = torch.optim.Adam(model.parameters(), lr=spec["learning_rate"])
    windows = Windows(sets["train_pos"], sets["train_neg"], cfg, rng)
    mean_t, std_t = torch.tensor(mean, device=device)[:, None], torch.tensor(std, device=device)[:, None]
    best, history = None, []
    for step in range(1, spec["steps"] + 1):
        x, y = windows.batch()
        x = (torch.from_numpy(x.astype(np.float32)).to(device).transpose(1, 2) - mean_t) / std_t
        y = torch.from_numpy(y.astype(np.float32)).to(device)
        logits = model(x)[:, 0, spec["warmup_hops"] :]
        loss = functional.binary_cross_entropy_with_logits(logits, y[:, spec["warmup_hops"] :])
        optimiser.zero_grad()
        loss.backward()
        optimiser.step()
        if step % spec["eval_every"] == 0:
            model.eval()
            val = sweep(model, sets["val_pos"], sets["val_neg"], mean, std, cfg, device)
            model.train()
            threshold, recall, rate = operating_point(val, cfg["eval"]["false_accepts_per_hour"])
            row = {"step": step, "loss": float(loss), "threshold": threshold, "recall": recall, "fa_per_hour": rate}
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
    sets = {name: load_set(root / name, positive, around, dtype) for name, (positive, dtype) in SETS.items()}
    split_files = sorted((paths["splits"] / "wake" / cfg["train"]["split"]).glob("*.txt"))
    run = create_run_dir(paths["artifacts"], "wake", cfg, split_files)
    model, stats, history = train(cfg, sets, device)
    threshold = stats["best"]["threshold"]
    report = {"val": stats["best"], "history": history}
    for name in ("val", "test"):
        pos = sets.get(f"{name}_pos", [])
        result = sweep(model, pos, sets[f"{name}_neg"], stats["mean"], stats["std"], cfg, device)
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
