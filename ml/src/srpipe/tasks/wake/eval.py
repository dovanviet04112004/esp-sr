"""Score the wake TCN as the device runs it (KEHOACH 3.11): each shard streamed whole, the probability smoothed,
positives caught when the smoothed score reaches the threshold before their label ends, false accepts counted on
negatives with a lockout after each and turned into a rate per hour."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch

from srpipe.generated import grid
from srpipe.tasks.wake.data import Shard
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
