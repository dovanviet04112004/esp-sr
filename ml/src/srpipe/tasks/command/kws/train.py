"""Train the kws DS-CNN on board-simulated log-mel and pitch (E11-T17, KEHOACH 3.12) and write a run directory.

Windows sit where the device scores them: a said clip's ends where the simulated vad turns off after it, up to late_s
later; ordinary speech and noise end theirs anywhere. Batches hold classes and files at fixed shares; SpecAugment;
cross-entropy. The last weights are kept, the reject and margin thresholds chosen on val.
Run: python -m srpipe.tasks.command.kws.train [--set model.size=M]
"""

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

from srpipe.core import splits
from srpipe.core.config import apply_overrides, data_paths, load_yaml
from srpipe.core.run_dir import create_run_dir
from srpipe.core.seed import seed_everything
from srpipe.dsp.spec import pitch
from srpipe.generated import grid
from srpipe.tasks import command
from srpipe.tasks.command import kws
from srpipe.tasks.command.kws import data
from srpipe.tasks.command.kws.model import dscnn
from srpipe.tasks.command.kws.postproc import decide

BRANCH = "command_kws"
ANYWHERE, IN_SPEECH, AT_END = "anywhere", "in_speech", "at_end"
NORM_STREAM, VAL_STREAM = 1, 2
VAL_BATCH = 1024


@dataclass
class Source:
    """One processed split file: its features end to end, and per item the first and last hop a window may end at."""

    name: str
    cls: str
    where: str  # ANYWHERE, IN_SPEECH or AT_END
    features: np.ndarray  # (hops, dims): log-mel, then pitch
    first: np.ndarray
    last: np.ndarray


@dataclass
class Held:
    """Every val item once, its window where the device would score it."""

    windows: np.ndarray  # (items, hops, dims) float32
    labels: np.ndarray
    files: np.ndarray  # index into file_names
    file_names: list[str]


def placement(cls: str, source: str) -> str:
    """Where a window of a class and source may end: silence anywhere, ordinary speech inside it, a said clip where
    vad turns off after it."""
    if cls == kws.SILENCE:
        return ANYWHERE
    return IN_SPEECH if source == "speech" else AT_END


def vad_end(vad: np.ndarray, start: int, stop: int) -> int:
    """The hop vad turns off after an utterance of hops [start, stop): the first unvoiced one past its last voiced
    hop, stop when vad never fired in it, the item's last hop when vad stays on to the end."""
    voiced = np.flatnonzero(vad[start:stop])
    if not len(voiced):
        return min(stop, len(vad) - 1)
    after = start + int(voiced[-1]) + 1
    off = np.flatnonzero(vad[after:] == 0)
    return after + int(off[0]) if len(off) else len(vad) - 1


def end_span(vad: np.ndarray, speech: list[int], where: str, window: int, late: int) -> tuple[int, int]:
    """First and last hop of an item a window may end at, never so early that the window starts before the item."""
    off = vad_end(vad, *speech)
    if where == ANYWHERE:
        first, last = 0, len(vad) - 1
    elif where == IN_SPEECH:
        first, last = speech[0] + 1, off
    else:
        first, last = off, min(off + late, len(vad) - 1)
    first = max(first, window - 1)
    if first > last:
        raise ValueError(f"an item of {len(vad)} hops holds no {window}-hop window ending in [{first}, {last}]")
    return first, last


def load_source(folder: Path, names: list[str], window: int, late: int, dtype: str) -> Source:
    """Every shard of one processed split file, its features held as dtype; the folder must hold a finished build
    with pitch."""
    manifest = folder / "manifest.yaml"
    if not manifest.exists():
        raise FileNotFoundError(f"{folder} has no manifest.yaml: run make kws-features to the end")
    built = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    if not built.get("pitch"):
        raise ValueError(f"{folder} was simulated without pitch")
    cls = data.class_of(folder.name, names)
    where = placement(cls, data.source_of(folder.name))
    features, first, last, offset = [], [], [], 0
    for name in sorted(n for n in built["sha256"] if n.endswith(".items.jsonl")):
        stem = str(folder / name).removesuffix(".items.jsonl")
        mel = np.load(stem + ".features.npy")
        features.append(np.concatenate([mel, np.load(stem + ".pitch.npy")], axis=1).astype(dtype))
        vad = np.load(stem + ".figures.npy")[:, 0]
        for line in (folder / name).read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            at = item["frame_offset"]
            lo, hi = end_span(vad[at : at + item["n_frames"]], item["speech_frames"], where, window, late)
            first.append(offset + at + lo)
            last.append(offset + at + hi)
        offset += len(mel)
    return Source(folder.name, cls, where, np.concatenate(features), np.array(first), np.array(last))


def class_shares(names: list[str], given: dict[str, float]) -> np.ndarray:
    """A batch's share of each class: as given, the classes not given splitting the rest evenly."""
    rest = [n for n in names if n not in given]
    left = 1.0 - sum(given.values())
    fits = left > 0.0 if rest else math.isclose(left, 0.0, abs_tol=1e-9)
    if not fits:
        raise ValueError(f"class_share {given} leaves {left:g} for {len(rest)} classes")
    return np.array([given[n] if n in given else left / len(rest) for n in names])


def file_shares(found: list[Source], given: dict[str, float]) -> np.ndarray:
    """A class's draws among its files: by the share given to each one's source, over those it has; else by items."""
    if not given:
        counts = np.array([len(s.first) for s in found], dtype=np.float64)
        return counts / counts.sum()
    if unlisted := [s.name for s in found if data.source_of(s.name) not in given]:
        raise ValueError(f"source_share gives no share to {', '.join(unlisted)}")
    p = np.array([given[data.source_of(s.name)] for s in found], dtype=np.float64)
    return p / p.sum()


class Batches:
    """Training windows: a class by its share, one of its files by its share, an item, a last hop in its span."""

    def __init__(self, sources: list[Source], names: list[str], cfg: dict, rng: np.random.Generator) -> None:
        spec = cfg["train"]
        self.rng, self.window, self.size = rng, cfg["window_hops"], spec["batch"]
        self.files = [[s for s in sources if s.cls == c] for c in names]
        if missing := [c for c, found in zip(names, self.files, strict=True) if not found]:
            raise ValueError(f"no training file holds {', '.join(missing)}")
        self.class_p = class_shares(names, spec["class_share"])
        shares = spec["source_share"]
        self.file_p = [file_shares(found, shares.get(c, {})) for c, found in zip(names, self.files, strict=True)]
        self.dims = sources[0].features.shape[1]

    def draw(self) -> tuple[np.ndarray, np.ndarray]:
        """Windows (batch, hops, dims) in float32 and their classes."""
        labels = self.rng.choice(len(self.class_p), size=self.size, p=self.class_p)
        x = np.empty((self.size, self.window, self.dims), dtype=np.float32)
        for k, c in enumerate(labels):
            s = self.files[c][self.rng.choice(len(self.files[c]), p=self.file_p[c])]
            i = self.rng.integers(len(s.first))
            end = int(self.rng.integers(s.first[i], s.last[i] + 1))
            x[k] = s.features[end - self.window + 1 : end + 1]
        return x, labels


def feature_stats(batches: Batches, count: int) -> tuple[np.ndarray, np.ndarray]:
    """Mean and deviation of every dim over count batches, accumulated in float64."""
    total, square, n = 0.0, 0.0, 0
    for _ in range(count):
        x = batches.draw()[0].reshape(-1, batches.dims).astype(np.float64)
        total, square, n = total + x.sum(axis=0), square + (x * x).sum(axis=0), n + len(x)
    mean = total / n
    return mean.astype(np.float32), np.sqrt(square / n - mean * mean).astype(np.float32)


def mask(x: np.ndarray, spec: dict, n_bands: int, rng: np.random.Generator) -> None:
    """SpecAugment in place on normalised windows: zero, train's mean, over spans of mel bands and spans of hops."""
    for w in x:
        for _ in range(spec["bands"]):
            width = int(rng.integers(spec["band_width"] + 1))
            at = int(rng.integers(n_bands - width + 1))
            w[:, at : at + width] = 0.0
        for _ in range(spec["hops"]):
            width = int(rng.integers(spec["hop_width"] + 1))
            at = int(rng.integers(len(w) - width + 1))
            w[at : at + width] = 0.0


def held_windows(sources: list[Source], names: list[str], window: int, seed: int) -> Held:
    """Every val item once: a said clip's window ends where vad turns off, any other at a seeded hop of its span."""
    rng = np.random.default_rng([seed, VAL_STREAM])
    xs, labels, files = [], [], []
    for k, s in enumerate(sources):
        ends = s.first if s.where == AT_END else rng.integers(s.first, s.last + 1)
        xs.append(np.stack([s.features[e - window + 1 : e + 1] for e in ends]).astype(np.float32))
        labels.append(np.full(len(ends), names.index(s.cls)))
        files.append(np.full(len(ends), k))
    held = Held(np.concatenate(xs), np.concatenate(labels), np.concatenate(files), [s.name for s in sources])
    if not (held.labels < names.index(kws.OTHER)).any():
        raise ValueError("val holds no window of any command")
    return held


def logits_of(model: dscnn.DsCnn, x: np.ndarray, mean: np.ndarray, std: np.ndarray, device: str) -> np.ndarray:
    out = []
    with torch.no_grad():
        for k in range(0, len(x), VAL_BATCH):
            batch = torch.from_numpy((x[k : k + VAL_BATCH] - mean) / std).to(device)
            out.append(model(batch[:, None]).float().cpu().numpy())
    return np.concatenate(out)


def decisions(logits: np.ndarray, n_commands: int) -> np.ndarray:
    """Per window, as the device decides with both thresholds at zero: the winning command or REJECTED when other
    or silence wins, and its score and margin in permille."""
    return np.stack([decide.decide(row, n_commands, 0, 0)[:3] for row in logits])


def accepted_at(decided: np.ndarray, reject_permille: int, margin_permille: int) -> np.ndarray:
    command, score, margin = decided.T
    return (command != decide.REJECTED) & (score >= reject_permille) & (margin >= margin_permille)


def operating_point(decided: np.ndarray, labels: np.ndarray, names: list[str], spec: dict) -> dict:
    """The reject and margin thresholds of val: the best worst-command recall among pairs that reject reject_target
    of other and silence, else the pair that rejects most; ties go to the higher mean recall, then rejection."""
    n_commands = names.index(kws.OTHER)
    said = labels < n_commands
    right = said & (decided[:, 0] == labels)
    counts = np.bincount(labels[said], minlength=n_commands)
    # A command with no val window has no recall to weigh in the choice.
    present = np.flatnonzero(counts)
    steps = range(0, int(decide.PERMILLE) + 1, spec["step_permille"])
    best, chosen = None, None
    for r in steps:
        for m in steps:
            accepted = accepted_at(decided, r, m)
            rejection = 1.0 - float(accepted[~said].mean())
            recall = np.bincount(labels[right & accepted], minlength=n_commands)[present] / counts[present]
            meets = rejection >= spec["reject_target"]
            key = (meets, float(recall.min()) if meets else rejection, float(recall.mean()), rejection)
            if best is None or key > best:
                best, chosen = key, (r, m, rejection, recall)
    r, m, rejection, recall = chosen
    wrong = said & accepted_at(decided, r, m) & ~right
    return {
        "reject_permille": r,
        "margin_permille": m,
        "rejection": rejection,
        "min_recall": float(recall.min()),
        "mean_recall": float(recall.mean()),
        "wrong_command": float(wrong[said].mean()),
        "recall": {names[k]: float(v) for k, v in zip(present, recall, strict=True)},
    }


def by_file(decided: np.ndarray, held: Held, n_commands: int, reject_permille: int, margin_permille: int) -> dict:
    """At the chosen thresholds, per val file: the share a command's file gets right, the share any other rejects."""
    accepted = accepted_at(decided, reject_permille, margin_permille)
    out = {}
    for k, name in enumerate(held.file_names):
        rows = held.files == k
        if held.labels[rows][0] < n_commands:
            out[name] = float(np.mean(accepted[rows] & (decided[rows, 0] == held.labels[rows])))
        else:
            out[name] = 1.0 - float(accepted[rows].mean())
    return out


def evaluate(model: dscnn.DsCnn, held: Held, stats: tuple, names: list[str], cfg: dict, device: str) -> dict:
    """Val figures: plain accuracy over every class, then the operating point and each file's share at it."""
    logits = logits_of(model, held.windows, *stats, device)
    n_commands = names.index(kws.OTHER)
    decided = decisions(logits, n_commands)
    point = operating_point(decided, held.labels, names, cfg["eval"])
    files = by_file(decided, held, n_commands, point["reject_permille"], point["margin_permille"])
    return {"accuracy": float(np.mean(logits.argmax(axis=1) == held.labels))} | point | {"by_file": files}


def checkpoint(run: Path, step: int) -> Path:
    return run / "checkpoints" / f"step_{step:06d}.pt"


def coverage(batches: Batches, names: list[str], spec: dict) -> str:
    """How often training sees each item of each file, for the run's log."""
    draws = spec["steps"] * spec["batch"]
    parts = [
        f"{s.name}: {len(s.first)} items, each about {draws * p * q / len(s.first):.0f} times"
        for files, file_p, p in zip(batches.files, batches.file_p, batches.class_p, strict=True)
        for s, q in zip(files, file_p, strict=True)
    ]
    return f"{spec['steps']} steps of {spec['batch']}; " + "; ".join(parts)


def train(
    cfg: dict, names: list[str], sources: list[Source], held: Held, device: str, run: Path | None = None
) -> tuple[dscnn.DsCnn, dict, list[dict]]:
    """The weights of the last step; the feature statistics with the last val figures in full; one row of scalar val
    figures per evaluation. Each evaluated net is saved in the run's checkpoints when run is given."""
    spec = cfg["train"]
    rng = seed_everything(spec["seed"])
    batches = Batches(sources, names, cfg, rng)
    norm = Batches(sources, names, cfg, np.random.default_rng([spec["seed"], NORM_STREAM]))
    mean, std = feature_stats(norm, spec["norm_batches"])
    n_bands = len(mean) - pitch.N_FEATURES
    model = dscnn.build(cfg, (cfg["window_hops"], len(mean)), len(names)).to(device)
    optimiser = torch.optim.Adam(model.parameters(), lr=spec["learning_rate"])
    final = spec["final_learning_rate"] / spec["learning_rate"]
    schedule = torch.optim.lr_scheduler.LambdaLR(
        optimiser, lambda step: final + (1 - final) * 0.5 * (1 + math.cos(math.pi * step / spec["steps"]))
    )
    print(coverage(batches, names, spec), flush=True)
    history, losses, figures = [], [], {}
    for step in range(1, spec["steps"] + 1):
        x, y = batches.draw()
        x = (x - mean) / std
        mask(x, spec["masks"], n_bands, rng)
        logits = model(torch.from_numpy(x).to(device)[:, None])
        loss = functional.cross_entropy(logits, torch.from_numpy(y).to(device))
        optimiser.zero_grad()
        loss.backward()
        optimiser.step()
        schedule.step()
        losses.append(loss.item())
        if step % spec["eval_every"] == 0 or step == spec["steps"]:
            model.eval()
            figures = evaluate(model, held, (mean, std), names, cfg, device)
            model.train()
            row = {"step": step, "loss": float(np.mean(losses)), "lr": schedule.get_last_lr()[0]}
            row |= {k: v for k, v in figures.items() if not isinstance(v, dict)}
            losses = []
            history.append(row)
            print(" ".join(f"{k} {v:.4g}" for k, v in row.items()), flush=True)
            if run:
                checkpoint(run, step).parent.mkdir(parents=True, exist_ok=True)
                torch.save(model.state_dict(), checkpoint(run, step))
    model.eval()
    return model, {"mean": mean, "std": std, "val": figures}, history


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="a dotted override of command_kws.yaml, e.g. model.size=M",
    )
    cfg, paths = apply_overrides(load_yaml(kws.CONFIG), parser.parse_args(argv).overrides), data_paths()
    names = kws.classes(cfg, load_yaml(command.CONFIG))
    window, version = cfg["window_hops"], cfg["split"]["version"]
    late = round(cfg["train"]["late_s"] * grid.SAMPLE_RATE_HZ / grid.HOP_SAMPLES)
    split_files = sorted((paths["splits"] / BRANCH / version).glob("*.txt"))
    root = paths["processed"] / BRANCH / version
    loaded = {
        role: [
            load_source(root / f.stem, names, window, late, dtype)
            for f in split_files
            if splits.role_of(f.name) == role
        ]
        for role, dtype in (("train", "float16"), ("val", "float32"))
    }
    held = held_windows(loaded["val"], names, window, cfg["train"]["seed"])
    device = "cuda" if torch.cuda.is_available() else "cpu"
    run = create_run_dir(paths["artifacts"], BRANCH, cfg, split_files)
    model, stats, history = train(cfg, names, loaded["train"], held, device, run)
    torch.save(model.state_dict(), run / "model.pt")
    np.savez(run / "feature_stats.npz", mean=stats["mean"], std=stats["std"])
    report = {"val": stats["val"], "history": history}
    (run / "metrics.yaml").write_text(yaml.safe_dump(report, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"{run}\n" + yaml.safe_dump({"val": stats["val"]}, sort_keys=False, allow_unicode=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
