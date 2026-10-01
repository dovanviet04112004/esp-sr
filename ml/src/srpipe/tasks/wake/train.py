"""Train the wake TCN on board-simulated log-mel (E11-T11, KEHOACH 3.11) and write a run directory.

Windows around a positive's end, a hard near miss (the end of a TTS phrase or of a word-long clip, anywhere in a corpus
sentence's speech), the first seconds of a negative sentence, or anywhere in a negative, with fixed shares per batch;
per-hop BCE past the warm-up, plus a CTC head on the trunk reading the lang_vi units of real sentences of train_neg.
The last weights are kept, each evaluated one saved beside them. Run: python -m srpipe.tasks.wake.train"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import torch
import yaml
from torch import nn
from torch.nn import functional

from srpipe.core import screen
from srpipe.core.config import apply_overrides, data_paths, load_yaml
from srpipe.core.run_dir import create_run_dir
from srpipe.core.seed import seed_everything
from srpipe.generated import grid
from srpipe.lang import g2p
from srpipe.tasks.wake import CONFIG
from srpipe.tasks.wake.data import Shard, label_hops, load_set, sentence_units
from srpipe.tasks.wake.eval import checkpoint, operating_point, sweep
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
    miss, one ending in the first onset_s of a negative sentence's speech, or one anywhere in a negative."""

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
        spoken = [i["origin"] != "synth" for s in positives for i in s.items]
        self.real = [e for e, r in zip(self.ends, spoken, strict=True) if r]
        self.synth = [e for e, r in zip(self.ends, spoken, strict=True) if not r]
        self.hard_items = [(k, i) for k, s in enumerate(self.hard) for i in s.items]
        self.onsets = [(k, i["frame_offset"] + i["speech_frames"][0]) for k, s in enumerate(negatives) for i in s.items]
        rate = grid.SAMPLE_RATE_HZ / grid.HOP_SAMPLES
        self.onset_hops, self.word_hops = round(self.cfg["onset_s"] * rate), round(self.cfg["word_s"] * rate)
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

    def expected_real(self, n_pos: int) -> float:
        """Real positives a batch draws on average: real_share of them while both kinds exist, else all of one."""
        if not self.real or not self.synth:
            return float(n_pos) if self.real else 0.0
        return n_pos * self.cfg.get("real_share", 0.0)

    def real_count(self, n_pos: int) -> int:
        """Positives of this batch from real speech: expected_real rounded at random, so a share under one clip a batch
        still comes up in its proportion of batches."""
        wanted = self.expected_real(n_pos)
        return int(wanted) + int(self.rng.random() < wanted - int(wanted))

    def positive_ends(self, n_pos: int) -> list[tuple[int, int]]:
        n_real = self.real_count(n_pos)
        picks = [self.real[k] for k in self.rng.integers(len(self.real), size=n_real)] if n_real else []
        if n_pos > n_real:
            picks += [self.synth[k] for k in self.rng.integers(len(self.synth), size=n_pos - n_real)]
        return picks

    def hard_stop(self, item: dict) -> int:
        """A TTS phrase or a clip no longer than a word ends inside the loss region as a positive would; a corpus
        sentence has no word timing, so the window ends anywhere in its speech."""
        first = item["frame_offset"] + item["speech_frames"][0]
        end = item["frame_offset"] + item["speech_frames"][1]
        if item["origin"] == "synth" or end - first <= self.word_hops:
            return end + self.after + int(self.rng.integers(self.slack))
        return int(self.rng.integers(first + 1, end + self.after + 1))

    def batch(self) -> tuple[np.ndarray, np.ndarray]:
        width = self.cfg["window_hops"]
        n_pos, n_hard, n_neg = self.counts()
        xs, ys = [], []
        for shard_index, end in self.positive_ends(n_pos):
            stop = end + self.after + int(self.rng.integers(self.slack))
            x, y = self.cut(self.positives[shard_index], stop)
            xs.append(x), ys.append(y)
        for k in self.rng.integers(len(self.hard_items), size=n_hard) if n_hard else []:
            shard_index, item = self.hard_items[k]
            x, y = self.cut(self.hard[shard_index], self.hard_stop(item))
            xs.append(x), ys.append(y)
        n_onset = round(n_neg * self.cfg["onset_share"]) if self.onsets else 0
        for k in self.rng.integers(len(self.onsets), size=n_onset) if n_onset else []:
            shard_index, first = self.onsets[k]
            x, y = self.cut(self.negatives[shard_index], first + 1 + int(self.rng.integers(self.onset_hops)))
            xs.append(x), ys.append(y)
        for k in self.rng.choice(len(self.negatives), size=n_neg - n_onset, p=self.weights):
            shard = self.negatives[k]
            x, y = self.cut(shard, int(self.rng.integers(width, len(shard.features) + 1)))
            xs.append(x), ys.append(y)
        return np.stack(xs), np.stack(ys)


class Sentences:
    """Whole public sentences of the negatives with their lang_vi units, for the CTC side task (KEHOACH 3.11): each
    example is warmup_hops of what came before it, then the sentence, padded to the longest of its batch."""

    def __init__(self, shards: list[Shard], units: dict[str, list[int]], cfg: dict, rng: np.random.Generator) -> None:
        self.cfg, self.rng, self.shards = cfg["train"], rng, shards
        longest = round(self.cfg["aux"]["max_s"] * grid.SAMPLE_RATE_HZ / grid.HOP_SAMPLES)
        self.items = [
            (k, item["frame_offset"], item["n_frames"], units[item["item"]])
            for k, shard in enumerate(shards)
            for item in shard.items
            if item["item"] in units and item["n_frames"] <= longest
        ]
        if not self.items:
            raise ValueError("no sentence of the negatives has lang_vi units within aux.max_s")

    def batch(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Features (batch, hops, bands), each sentence's hops, all units shifted past the CTC blank, their counts."""
        lead = self.cfg["warmup_hops"]
        picks = [self.items[k] for k in self.rng.integers(len(self.items), size=self.cfg["aux"]["batch"])]
        width = lead + max(n for _, _, n, _ in picks)
        xs = []
        for k, offset, n, _ in picks:
            start = max(0, offset - lead)
            x = self.shards[k].features[start : offset + n]
            xs.append(np.pad(x, ((lead - (offset - start), width - lead - n), (0, 0)), mode="edge"))
        lengths = np.array([n for _, _, n, _ in picks], dtype=np.int64)
        targets = np.concatenate([np.asarray(u, dtype=np.int64) + 1 for *_, u in picks])
        counts = np.array([len(u) for *_, u in picks], dtype=np.int64)
        return np.stack(xs), lengths, targets, counts


def coverage(cfg: dict, windows: Windows) -> str:
    """How often training sees each positive, each hard near miss and each negative hop, for the run's log."""
    spec = cfg["train"]
    n_pos, n_hard, n_neg = windows.counts()
    n_real = windows.expected_real(n_pos)
    negative_hops = sum(len(s.features) for s in windows.negatives)
    passes = spec["steps"] * n_neg * spec["window_hops"] / negative_hops
    kinds = [(windows.real, n_real, "real"), (windows.synth, n_pos - n_real, "TTS")]
    line = f"{spec['steps']} steps: " + ", ".join(
        f"each of {len(ends)} {name} positives about {spec['steps'] * n / len(ends):.0f} times"
        for ends, n, name in kinds
        if ends
    )
    line += f", each of {negative_hops} negative hops about {passes:.1f} times"
    if n_hard:
        hard_visits = spec["steps"] * n_hard / len(windows.hard_items)
        line += f", each of {len(windows.hard_items)} hard near misses about {hard_visits:.0f} times"
    return line


def train(
    cfg: dict,
    sets: dict[str, list[Shard]],
    device: str,
    units: dict[str, list[int]] | None = None,
    run: Path | None = None,
) -> tuple[Tcn, dict, list[dict]]:
    """The weights of the last step, the band statistics and one row of val figures per evaluation; with units, the
    trunk also learns the CTC side task; each evaluated net is saved in the run's checkpoints when run is given."""
    spec, rng = cfg["train"], seed_everything(cfg["train"]["seed"])
    mean, std = band_stats(sets["train_pos"] + sets["train_neg"])
    n_bands = len(mean)
    model = Tcn(n_bands, **cfg["model"]).to(device)
    sentences = Sentences(sets["train_neg"], units, cfg, rng) if units else None
    reader = nn.Conv1d(cfg["model"]["channels"], len(g2p.UNIT_ID) + 1, 1).to(device) if sentences else None
    learned = list(model.parameters()) + (list(reader.parameters()) if reader else [])
    optimiser = torch.optim.Adam(learned, lr=spec["learning_rate"])
    final = spec["final_learning_rate"] / spec["learning_rate"]
    schedule = torch.optim.lr_scheduler.LambdaLR(
        optimiser, lambda step: final + (1 - final) * 0.5 * (1 + math.cos(math.pi * step / spec["steps"]))
    )
    windows = Windows(sets["train_pos"], sets["train_neg"], cfg, rng, sets.get("train_hard"))
    mean_t, std_t = torch.tensor(mean, device=device)[:, None], torch.tensor(std, device=device)[:, None]
    history, losses, side = [], [], []
    print(coverage(cfg, windows) + (side_coverage(cfg, sentences) if sentences else ""), flush=True)
    for step in range(1, spec["steps"] + 1):
        x, y = windows.batch()
        x = (torch.from_numpy(x.astype(np.float32)).to(device).transpose(1, 2) - mean_t) / std_t
        y = torch.from_numpy(y.astype(np.float32)).to(device)
        logits = model(x)[:, 0, spec["warmup_hops"] :]
        target = y[:, spec["warmup_hops"] :] * (1.0 - spec["label_smoothing"]) + spec["label_smoothing"] / 2
        loss = functional.binary_cross_entropy_with_logits(logits, target)
        if sentences:
            heard, lengths, targets, counts = sentences.batch()
            heard = (torch.from_numpy(heard.astype(np.float32)).to(device).transpose(1, 2) - mean_t) / std_t
            log_probs = reader(model.trunk(heard))[:, :, spec["warmup_hops"] :].log_softmax(1).permute(2, 0, 1)
            ctc = functional.ctc_loss(
                log_probs,
                torch.from_numpy(targets).to(device),
                torch.from_numpy(lengths),
                torch.from_numpy(counts),
                zero_infinity=True,
            )
            side.append(float(ctc))
            loss = loss + spec["aux"]["weight"] * ctc
        optimiser.zero_grad()
        loss.backward()
        optimiser.step()
        schedule.step()
        losses.append(float(loss))
        if step % spec["eval_every"] == 0 or step == spec["steps"]:
            model.eval()
            val = sweep(model, sets["val_pos"], sets["val_neg"] + sets.get("val_hard", []), mean, std, cfg, device)
            model.train()
            threshold, recall, rate = operating_point(val, cfg["eval"]["false_accepts_per_hour"])
            row = {"step": step, "loss": float(np.mean(losses)), "lr": schedule.get_last_lr()[0]}
            row |= {"ctc_loss": float(np.mean(side))} if side else {}
            row |= {"threshold": threshold, "recall": recall, "fa_per_hour": rate}
            losses, side = [], []
            history.append(row)
            print(" ".join(f"{k} {v:.4g}" for k, v in row.items()), flush=True)
            if run:
                checkpoint(run, step).parent.mkdir(parents=True, exist_ok=True)
                torch.save(model.state_dict(), checkpoint(run, step))
    model.eval()
    return model, {"mean": mean, "std": std, "kept": history[-1]}, history


def side_coverage(cfg: dict, sentences: Sentences) -> str:
    spec = cfg["train"]["aux"]
    visits = cfg["train"]["steps"] * spec["batch"] / len(sentences.items)
    return f"; CTC side task on {len(sentences.items)} sentences, each about {visits:.1f} times"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="a dotted override of wake.yaml, e.g. model.channels=64",
    )
    cfg, paths = apply_overrides(load_yaml(CONFIG), parser.parse_args(argv).overrides), data_paths()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    root = paths["processed"] / "wake" / cfg["train"]["split"]
    around = label_hops(cfg["train"]["label_s"])
    sets = {
        name: load_set(root / name, positive, around, dtype)
        for name, (positive, dtype) in SETS.items()
        if name not in OPTIONAL or (root / name).exists()
    }
    split_files = sorted((paths["splits"] / "wake" / cfg["train"]["split"]).glob("*.txt"))
    units = None
    if cfg["train"].get("aux", {}).get("weight", 0) > 0:
        spoken = {i["item"] for s in sets["train_neg"] for i in s.items if i["origin"] == "public"}
        clips = screen.kept_clips(load_yaml(screen.CONFIG), paths, "speech")
        units = sentence_units(clips, spoken, cfg["train"]["aux"]["dialect"])
    run = create_run_dir(paths["artifacts"], "wake", cfg, split_files)
    model, stats, history = train(cfg, sets, device, units, run)
    threshold = stats["kept"]["threshold"]
    report = {"val": stats["kept"], "history": history}
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
