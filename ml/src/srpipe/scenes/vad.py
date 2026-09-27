"""Labelled scenes for vad: read speech with pauses and one noise at a chosen SNR and level (KEHOACH 3.10, 3.15).

Labels come from the clean utterance alone, hop by hop (configs/afe/vad.yaml says how far under its loudest hop and how
far over its own quiet hops a speech hop lies). python -m srpipe.scenes.vad scores srpipe.dsp.afe.vad at every
aggressiveness against a bare energy detector given its best single threshold; both hold 240 ms like dsp_afe.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from itertools import product
from pathlib import Path

import numpy as np

from srpipe.core.audio_io import read_wav
from srpipe.core.config import data_paths, load_config
from srpipe.dsp.afe import vad
from srpipe.generated import afe, grid
from srpipe.metrics.vad import VadScores, scores, with_hangover

HOP = grid.HOP_SAMPLES
ENERGY_FLOOR = 1e-12


@dataclass(frozen=True)
class Scene:
    """signal in -1 .. 1, one label per hop, and the condition it was built for."""

    signal: np.ndarray
    labels: np.ndarray
    noise: str
    snr_db: float
    level_dbfs: float


def hop_energy_db(x: np.ndarray) -> np.ndarray:
    """Mean square of each whole hop in dB, square full scale."""
    hops = x[: len(x) // HOP * HOP].reshape(-1, HOP).astype(np.float64)
    return 10.0 * np.log10(np.mean(hops**2, axis=1) + ENERGY_FLOOR)


def utterance_labels(x: np.ndarray, cfg: dict) -> np.ndarray:
    """Speech hops of one clean utterance, x a whole number of hops long."""
    level = hop_energy_db(x)
    floor = np.percentile(level, cfg["floor_percentile"])
    return (level > level.max() - cfg["label_below_peak_db"]) & (level > floor + cfg["label_above_floor_db"])


def whole_hops(x: np.ndarray) -> np.ndarray:
    return np.concatenate([x, np.zeros(-len(x) % HOP, dtype=x.dtype)])


def parts(
    utterances: list[np.ndarray],
    noise: np.ndarray,
    snr_db: float,
    level_dbfs: float,
    cfg: dict,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Speech, noise and labels apart: utterances in the given order with a pause before each, speech scaled to
    level_dbfs over its speech hops, noise looped from a random offset and scaled to snr_db under that level."""
    pieces, labels = [], []
    for u in utterances:
        pause = np.zeros(round(rng.uniform(*cfg["pause_s"]) * grid.SAMPLE_RATE_HZ / HOP) * HOP)
        u = whole_hops(u.astype(np.float64))
        pieces += [pause, u]
        labels += [np.zeros(len(pause) // HOP, dtype=bool), utterance_labels(u, cfg)]
    speech = np.concatenate(pieces)
    label = np.concatenate(labels)
    active = np.mean(speech.reshape(-1, HOP)[label] ** 2)
    speech *= 10 ** (level_dbfs / 20) / np.sqrt(active)
    start = int(rng.integers(len(noise)))
    looped = np.resize(np.roll(noise.astype(np.float64), -start), len(speech))
    looped *= 10 ** ((level_dbfs - snr_db) / 20) / np.sqrt(np.mean(looped**2))
    return speech, looped, label


def build(
    utterances: list[np.ndarray],
    noise: np.ndarray,
    noise_name: str,
    snr_db: float,
    level_dbfs: float,
    cfg: dict,
    rng: np.random.Generator,
) -> Scene:
    """The scene of parts(), speech and noise summed."""
    speech, looped, label = parts(utterances, noise, snr_db, level_dbfs, cfg, rng)
    return Scene((speech + looped).astype(np.float32), label, noise_name, snr_db, level_dbfs)


def gmm_raw(scene: Scene, aggressiveness: int) -> np.ndarray:
    detector = vad.Vad(aggressiveness, hangover_ms=0)
    x = scene.signal
    return np.array([detector.process(x[i : i + HOP]).raw for i in range(0, len(x) - HOP + 1, HOP)])


@dataclass(frozen=True)
class SceneResult:
    scene: Scene
    gmm_raw: dict[int, np.ndarray]
    energy_db: np.ndarray


def pick_utterances(files: list[str], cfg: dict, rng: np.random.Generator) -> list[np.ndarray]:
    """Utterances in a random order until speech_s_per_scene seconds of read speech."""
    utterances, seconds = [], 0.0
    for i in rng.permutation(len(files)):
        u = read_wav(Path(files[i]))[0][:, 0]
        utterances.append(u)
        seconds += len(u) / grid.SAMPLE_RATE_HZ
        if seconds >= cfg["speech_s_per_scene"]:
            break
    return utterances


def scene_files(cfg: dict, raw_root: Path) -> dict:
    """Every speech file and every noise, by name, that cfg points to under raw/."""
    speech = sorted(str(p) for p in (raw_root / cfg["speech"]).glob("*/*.wav"))
    noise = {p.stem: str(p) for p in sorted((raw_root / cfg["noise"]).glob("*.wav"))}
    if not speech or not noise:
        raise FileNotFoundError(
            f"no speech under {raw_root / cfg['speech']} or no noise under {raw_root / cfg['noise']}"
        )
    return {"speech": speech, "noise": noise}


def _score_scene(job: tuple[int, str, float, float, dict, dict]) -> SceneResult:
    index, noise_name, snr_db, level_dbfs, cfg, files = job
    rng = np.random.default_rng([cfg["seed"], index])
    utterances = pick_utterances(files["speech"], cfg, rng)
    noise = read_wav(Path(files["noise"][noise_name]))[0][:, 0]
    scene = build(utterances, noise, noise_name, snr_db, level_dbfs, cfg, rng)
    raws = {a: gmm_raw(scene, a) for a in cfg["aggressiveness"]}
    return SceneResult(scene, raws, hop_energy_db(scene.signal))


def evaluate(cfg: dict, raw_root: Path, workers: int) -> list[SceneResult]:
    files = scene_files(cfg, raw_root)
    conditions = list(product(files["noise"], cfg["snr_db"], cfg["level_dbfs"]))
    jobs = [(i, n, s, lv, cfg, files) for i, (n, s, lv) in enumerate(conditions)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_score_scene, jobs))


def _pooled(results: list[SceneResult], decide, hangover: bool = True) -> VadScores:
    hang = vad.hangover_hops(afe.VAD_HANGOVER_MS) if hangover else 0
    decisions = np.concatenate([with_hangover(decide(r), hang) for r in results])
    labels = np.concatenate([r.scene.labels[: len(decide(r))] for r in results])
    return scores(decisions, labels)


def best_energy_threshold(results: list[SceneResult], sweep: list[float], hangover: bool = True) -> float:
    first, last, step = sweep
    thresholds = np.arange(first, last + step / 2, step)
    return float(max(thresholds, key=lambda t: _pooled(results, lambda r, t=t: r.energy_db > t, hangover).f1))


def number(value: float, digits: int) -> str:
    """Decimal comma and a true minus, as the tables of docs/measurements write numbers."""
    return f"{value:.{digits}f}".replace(".", ",").replace("-", "\u2212")


def table(results: list[SceneResult], cfg: dict, hangover: bool = True) -> str:
    """Pooled scores by condition, with dsp_afe's hangover or on the raw decisions; the energy detector gets the
    threshold that serves it best over every scene under the same rule."""
    threshold = best_energy_threshold(results, cfg["energy_thresholds_dbfs"], hangover)
    detectors = [(f"vad GMM, aggressiveness {a}", lambda r, a=a: r.gmm_raw[a]) for a in cfg["aggressiveness"]]
    detectors.append(
        (f"năng lượng trần, ngưỡng tốt nhất {number(threshold, 0)} dBFS", lambda r: r.energy_db > threshold)
    )
    groups = [("tất cả", results)]
    groups += [(f"SNR {number(s, 0)} dB", [r for r in results if r.scene.snr_db == s]) for s in cfg["snr_db"]]
    groups += [
        (f"mức {number(lv, 0)} dBFS", [r for r in results if r.scene.level_dbfs == lv]) for lv in cfg["level_dbfs"]
    ]
    lines = ["| Máy dò | " + " | ".join(g for g, _ in groups) + " |", "|---" * (len(groups) + 1) + "|"]
    for name, decide in detectors:
        cells = []
        for _, rs in groups:
            s = _pooled(rs, decide, hangover)
            cells.append(f"{number(s.f1, 3)} ({number(100 * s.miss, 1)} / {number(100 * s.false_alarm, 1)})")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    total = _pooled(results, detectors[0][1])
    lines.append("")
    counts = f"{len(results)} cảnh, {total.speech_hops} bước nói, {total.silent_hops} bước im"
    lines.append(f"{counts}; ô: F1 (% bỏ sót / % báo nhầm).")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("overrides", nargs="*", help="a.b=value overrides of configs/afe/vad.yaml")
    args = parser.parse_args(argv)
    cfg = load_config("afe/vad", overrides=args.overrides)["eval"]
    results = evaluate(cfg, data_paths()["raw"], args.workers)
    print("Kéo dài 240 ms, như dsp_afe:\n")
    print(table(results, cfg))
    print("\nQuyết định thô, không kéo dài:\n")
    print(table(results, cfg, hangover=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
