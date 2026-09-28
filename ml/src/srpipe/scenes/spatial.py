"""The spatial stage scored on the labelled scenes of room.py (KEHOACH 3.6-3.8, 3.15).

doa: the mix through hpf, the STFT and doa, searched on every second hop after a hop where the talker speaks (the
label, from its dry signal), and again through the chain whose own vad gates it; scored on the talker's speaking hops
against its angle, by condition and RT60, and apart near the array's ends. python -m srpipe.scenes.spatial doa
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from srpipe.core.audio_io import read_wav
from srpipe.core.config import data_paths, load_config
from srpipe.dsp.afe import doa, hpf
from srpipe.dsp.afe.chain import PCM_FULL_SCALE, PCM_MAX, PCM_MIN, Chain, ChainConfig
from srpipe.dsp.spec.stft import Stft
from srpipe.generated import afe, array, grid
from srpipe.metrics.doa_err import doa_score
from srpipe.scenes import room

HOP = grid.HOP_SAMPLES
PRODUCT = "product"
VAD_GATED = "product, vad"
CHAIN_MODULES = ("hpf", "doa", "ns_omlsa", "vad", "agc")


@dataclass(frozen=True)
class SceneDoa:
    """Per hop of one scene: the talker speaking, each run's estimate; and the labels it is grouped by."""

    labels: dict
    speaking: np.ndarray
    estimates: dict[str, np.ndarray]


def condition(labels: dict) -> str:
    kind = (labels.get("interferer") or {}).get("kind")
    return "alone" if kind is None else f"{kind} {labels['snr_db']:g} dB"


def gated_estimates(mix: np.ndarray, speaking: np.ndarray, cfg: doa.DoaConfig) -> np.ndarray:
    """doa's output per hop when the search follows the talker label instead of vad, as the chain times it."""
    filters, analysis = hpf.Hpf(), [Stft() for _ in range(array.N_MICS)]
    searcher = doa.Doa(cfg)
    out = np.empty(len(speaking), dtype=np.int16)
    for h in range(len(speaking)):
        hop = mix[h * HOP : (h + 1) * HOP]
        bins = [analysis[m].analyze(filters.process(m, hop[:, m])) for m in range(array.N_MICS)]
        update = h > 0 and bool(speaking[h - 1]) and h % afe.DOA_UPDATE_EVERY_HOPS == 0
        out[h] = searcher.process(bins[0], bins[1], update).angle_deg
    return out


def vad_estimates(mix: np.ndarray, n_hops: int) -> np.ndarray:
    """doa_deg of every frame of the chain with doa and the product's modules, no calibration."""
    pcm = np.clip(np.rint(mix[: n_hops * HOP] * PCM_FULL_SCALE), PCM_MIN, PCM_MAX).astype(np.int16)
    chain = Chain("MM", ChainConfig(modules=CHAIN_MODULES))
    return np.array([chain.process(pcm[h * HOP : (h + 1) * HOP].reshape(-1)).doa_deg for h in range(n_hops)])


def score_scene(job: tuple[Path, dict, dict]) -> SceneDoa:
    folder, variants, scene_cfg = job
    labels = json.loads((folder / "labels.json").read_text(encoding="utf-8"))
    mix, _ = read_wav(folder / "mix.wav")
    dry, _ = read_wav(folder / "talker_dry.wav")
    speaking = room.active_hops(dry[:, 0], scene_cfg["active_below_peak_db"])
    n_hops = min(len(speaking), len(mix) // HOP)
    speaking = speaking[:n_hops]
    estimates = {name: gated_estimates(mix, speaking, cfg) for name, cfg in variants.items()}
    estimates[VAD_GATED] = vad_estimates(mix, n_hops)
    return SceneDoa(labels, speaking, estimates)


def doa_variants(spec: dict) -> dict[str, doa.DoaConfig]:
    return {PRODUCT: doa.DoaConfig()} | {name: replace(doa.DoaConfig(), **o) for name, o in spec["variants"].items()}


def pooled(scenes: list[SceneDoa], run: str, tolerance: float, truth_range: tuple[float, float] | None = None):
    """doa_score over the speaking hops of the scenes, or None when none of them has a truth in truth_range."""
    est = [s.estimates[run][s.speaking] for s in scenes]
    truth = [np.full(int(s.speaking.sum()), s.labels["talker"]["angle_deg"]) for s in scenes]
    if truth_range is not None:
        keep = [truth_range[0] <= s.labels["talker"]["angle_deg"] <= truth_range[1] for s in scenes]
        est, truth = (
            [e for e, k in zip(est, keep, strict=True) if k],
            [t for t, k in zip(truth, keep, strict=True) if k],
        )
    if not est:
        return None
    return doa_score(np.concatenate(est), np.concatenate(truth), tolerance)


def cell(score) -> str:
    return "—" if score is None else f"{score.mean_abs_error_deg:.1f}° / {score.within_pct:.0f}%"


def doa_tables(scenes: list[SceneDoa], spec: dict) -> str:
    """By run: condition by RT60, then the regions of the talker's angle over every scene."""
    tol, end = spec["tolerance_deg"], spec["endfire_deg"]
    rt60s = sorted({s.labels["rt60_target_s"] for s in scenes})
    conditions = list(dict.fromkeys(condition(s.labels) for s in sorted(scenes, key=lambda s: s.labels["scene"])))
    lines = []
    for run in scenes[0].estimates:
        lines += [
            f"\n{run}: mean error / hops within {tol:g} deg",
            "| | " + " | ".join(f"RT60 {r:g} s" for r in rt60s) + " |",
        ]
        lines.append("|---|" + "---|" * len(rt60s))
        groups: dict[tuple[str, float], list[SceneDoa]] = defaultdict(list)
        for s in scenes:
            groups[condition(s.labels), s.labels["rt60_target_s"]].append(s)
        for c in conditions:
            lines.append(f"| {c} | " + " | ".join(cell(pooled(groups[c, r], run, tol)) for r in rt60s) + " |")
        regions = {
            f"0-{end:g}°": (0.0, end),
            f"{end:g}-{180 - end:g}°": (end, 180.0 - end),
            f"{180 - end:g}-180°": (180.0 - end, 180.0),
        }
        alone = [s for s in scenes if condition(s.labels) == "alone"]
        for name, group in (("every scene", scenes), ("talker alone", alone)):
            parts = " · ".join(f"{r} {cell(pooled(group, run, tol, span))}" for r, span in regions.items())
            lines.append(f"| {name}: {parts} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("block", choices=["doa"])
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("overrides", nargs="*", help="a.b=value overrides of configs/afe/<block>.yaml")
    args = parser.parse_args(argv)
    spec = load_config(f"afe/{args.block}", overrides=args.overrides)["eval"]
    scene_cfg = load_config("scenes/standard")
    folders = sorted((data_paths()["interim"] / "scenes" / spec["scenes"]).glob("scene_*"))
    print(f"{len(folders)} scenes of {spec['scenes']}")
    with ProcessPoolExecutor(args.workers) as pool:
        variants = doa_variants(spec)
        print(doa_tables(list(pool.map(score_scene, [(f, variants, scene_cfg) for f in folders])), spec))
    return 0


if __name__ == "__main__":
    sys.exit(main())
