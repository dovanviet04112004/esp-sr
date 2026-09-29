"""Score the wake TCN as the device runs it (KEHOACH 3.11): each shard streamed whole, the probability smoothed,
positives caught when the smoothed score reaches the threshold before their label ends, false accepts counted on
negatives with a lockout after each and turned into a rate per hour. python -m srpipe.tasks.wake.eval board <run> scores
the sessions recorded through board B the same way, each utterance found by the chain's own vad (KEHOACH 4.3)."""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import yaml

from srpipe.core.config import CONFIGS, data_paths, load_yaml
from srpipe.dsp.afe.chain import ChainConfig
from srpipe.dsp.spec.mel import Mel, MelConfig
from srpipe.generated import array, grid
from srpipe.scenes import device
from srpipe.tasks.wake import CONFIG
from srpipe.tasks.wake.data import Shard
from srpipe.tasks.wake.model.tcn import Tcn
from srpipe.tasks.wake.postproc.smooth import smooth, triggers

HOPS_PER_HOUR = 3600.0 * grid.SAMPLE_RATE_HZ / grid.HOP_SAMPLES


@dataclass(frozen=True)
class Sweep:
    thresholds: np.ndarray
    recall: np.ndarray  # share of positives caught, per threshold
    false_accepts_per_hour: np.ndarray
    positives: int
    negative_hours: float


def probabilities(model: torch.nn.Module, shard: Shard, mean: np.ndarray, std: np.ndarray, device: str) -> np.ndarray:
    """Per-hop probability over the whole shard in one pass, as the device streams it."""
    x = (shard.features.astype(np.float32) - mean) / std
    with torch.inference_mode():
        logits = model(torch.from_numpy(x.T[None]).to(device))[0, 0]
    return torch.sigmoid(logits).float().cpu().numpy()


def thresholds(spec: dict) -> np.ndarray:
    return np.arange(spec["first"], spec["last"] + spec["step"] / 2, spec["step"])


def sweep(model, positives: list[Shard], negatives: list[Shard], mean, std, cfg: dict, device: str) -> Sweep:
    """Recall and false accepts per hour at every threshold of cfg['eval']."""
    spec, rate = cfg["eval"], grid.SAMPLE_RATE_HZ / grid.HOP_SAMPLES
    grid_th = thresholds(spec["thresholds"])
    after = round(cfg["train"]["label_s"][1] * rate)
    peaks = []
    for shard in positives:
        s = smooth(probabilities(model, shard, mean, std, device), spec["smooth_hops"])
        for item in shard.items:
            first = item["frame_offset"] + item["speech_frames"][0]
            peaks.append(float(s[first : item["frame_offset"] + item["speech_frames"][1] + after].max()))
    lockout = round(spec["lockout_s"] * rate)
    fired = np.zeros(len(grid_th))
    hops = 0
    for shard in negatives:
        s = smooth(probabilities(model, shard, mean, std, device), spec["smooth_hops"])
        fired += [len(triggers(s, th, lockout)) for th in grid_th]
        hops += len(s)
    hours = hops / HOPS_PER_HOUR
    recall = (np.array(peaks)[None, :] >= grid_th[:, None]).mean(axis=1)
    return Sweep(grid_th, recall, fired / hours, len(peaks), hours)


def operating_point(result: Sweep, target_per_hour: float) -> tuple[float, float, float]:
    """The lowest threshold whose false accepts stay at or under the target: (threshold, recall, rate)."""
    ok = np.flatnonzero(result.false_accepts_per_hour <= target_per_hour)
    k = int(ok[0]) if len(ok) else len(result.thresholds) - 1
    return float(result.thresholds[k]), float(result.recall[k]), float(result.false_accepts_per_hour[k])


@dataclass(frozen=True)
class BoardSession:
    """One recorded session: per utterance the highest smoothed score up to its label's end, the triggers over the
    whole session, and those falling outside every utterance."""

    session: str
    kind: str
    prompt: str
    seconds: float
    peaks: list[float]
    triggers: int
    stray: int


def utterances(vad: np.ndarray, spec: dict) -> list[tuple[int, int]]:
    """(first, last) hop of each run of vad, runs closer than the gap joined, the shortest dropped."""
    rate = grid.SAMPLE_RATE_HZ / grid.HOP_SAMPLES
    gap, least = round(spec["utterance_gap_s"] * rate), round(spec["utterance_min_s"] * rate)
    runs: list[list[int]] = []
    for hop in np.flatnonzero(vad):
        if runs and hop - runs[-1][1] <= gap:
            runs[-1][1] = int(hop)
        else:
            runs.append([int(hop), int(hop)])
    return [(a, b) for a, b in runs if b - a >= least]


def session_features(session: Path, chain_cfg: ChainConfig, mel: Mel) -> tuple[np.ndarray, np.ndarray]:
    """Log-mel and vad per hop of ch0 and ch1 as the product's chain gives them, the channels cut to a common whole
    number of hops."""
    channels = [sf.read(session / f"ch{m}.wav", dtype="int16")[0] for m in range(array.N_MICS)]
    n = min(len(c) for c in channels) // grid.HOP_SAMPLES * grid.HOP_SAMPLES
    _, figures, features = device.listen(np.stack([c[:n] for c in channels], axis=1), chain_cfg, mel)
    return features, figures[:, 0].astype(bool)


def board(model, mean: np.ndarray, std: np.ndarray, cfg: dict, paths: dict, threshold: float, dev: str):
    """Every session of the board manifest at the product's pcm_shift, scored at threshold."""
    spec, rate = cfg["eval"]["board"], grid.SAMPLE_RATE_HZ / grid.HOP_SAMPLES
    device_cfg = load_yaml(CONFIGS / cfg["features"])
    mics = device.load_microphones(device_cfg["microphone"])
    chain_cfg, mel = ChainConfig(balance_gains=mics.gains), Mel(MelConfig(**device_cfg["features"]))
    after = round(cfg["train"]["label_s"][1] * rate)
    lockout = round(cfg["eval"]["lockout_s"] * rate)
    listing = paths["manifests"] / spec["manifest"]
    rows = [r for r in csv.DictReader(listing.open(encoding="utf-8")) if r["pcm_shift"] == str(spec["pcm_shift"])]
    root = paths["raw"] / "device" / rows[0]["board"] if rows else paths["raw"]
    results = []
    for r in rows:
        if not (root / r["session"]).exists():
            continue
        features, vad = session_features(root / r["session"], chain_cfg, mel)
        s = smooth(probabilities(model, Shard(features, vad, [], False), mean, std, dev), cfg["eval"]["smooth_hops"])
        spans = utterances(vad, spec)
        fired = triggers(s, threshold, lockout)
        inside = [any(a <= f <= b + after for a, b in spans) for f in fired]
        peaks = [float(s[a : b + after + 1].max()) for a, b in spans]
        seconds = len(vad) / rate
        results.append(
            BoardSession(r["session"], r["kind"], r["prompt"], seconds, peaks, len(fired), inside.count(False))
        )
    return results


def board_table(results: list[BoardSession], threshold: float) -> str:
    """Wake and near-miss sessions utterance by utterance, then false accepts over every session without the word."""
    lines = [
        f"threshold {threshold:.3f}",
        "",
        "| Session | Kind | Prompt | Over threshold | Stray | Utterance scores |",
    ]
    lines.append("|---|---|---|---|---|---|")
    for r in results:
        if r.kind in ("wake", "neg"):
            over = sum(p >= threshold for p in r.peaks)
            scores = " ".join(f"{p:.2f}" for p in r.peaks)
            prompt = r.prompt[:40].replace("|", "\\|")
            lines.append(f"| {r.session} | {r.kind} | {prompt} | {over}/{len(r.peaks)} | {r.stray} | {scores} |")
    others = [r for r in results if r.kind != "wake"]
    hours, fired = sum(r.seconds for r in others) / 3600, sum(r.triggers for r in others)
    rate = fired / hours if hours else float("nan")
    lines += [
        "",
        f"false accepts on {len(others)} sessions without the word, {hours:.2f} h: {fired}, {rate:.2f} per hour",
    ]
    return "\n".join(lines)


def checkpoint(run: Path, step: int) -> Path:
    """Where a run keeps the net of one evaluated step."""
    return run / "checkpoints" / f"step_{step:06d}.pt"


def load_run(run: Path, cfg: dict, step: int | None = None) -> tuple[Tcn, dict, dict, float]:
    """The run's kept network, or the one of an evaluated step, its band statistics and its val threshold, and cfg
    with the run's own model and features, since the net was built and fed by those; scoring keeps cfg's rules."""
    metrics = yaml.safe_load((run / "metrics.yaml").read_text(encoding="utf-8"))
    rows = [r for r in metrics["history"] if r["step"] == step] if step else [metrics["val"]]
    if not rows:
        raise ValueError(f"{run} evaluated no step {step}")
    trained = load_yaml(run / "config.resolved.yaml")
    stats = dict(np.load(run / "band_stats.npz"))
    model = Tcn(len(stats["mean"]), **trained["model"])
    model.load_state_dict(torch.load(checkpoint(run, step) if step else run / "model.pt", map_location="cpu"))
    model.eval()
    return model, stats, cfg | {"model": trained["model"], "features": trained["features"]}, rows[0]["threshold"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("what", choices=["board"])
    parser.add_argument("run", type=Path, help="a run directory of python -m srpipe.tasks.wake.train")
    parser.add_argument("--threshold", type=float, help="instead of the one the run chose on val")
    parser.add_argument("--step", type=int, help="score the net saved at this evaluated step, not the kept one")
    args = parser.parse_args(argv)
    model, stats, cfg, chosen = load_run(args.run, load_yaml(CONFIG), args.step)
    threshold = args.threshold if args.threshold is not None else chosen
    print(board_table(board(model, stats["mean"], stats["std"], cfg, data_paths(), threshold, "cpu"), threshold))
    return 0


if __name__ == "__main__":
    sys.exit(main())
