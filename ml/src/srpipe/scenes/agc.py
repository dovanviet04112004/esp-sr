"""agc scored on the vad scenes at input levels -50 .. -10 dBFS, run through vad then agc as the chain runs them.

python -m srpipe.scenes.agc prints, by input level, the speech level that comes out after settle_s against the target,
the highest output sample, samples at full scale, and how far the settled gain wanders (KEHOACH 3.10, 3.15).
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
from srpipe.core.config import data_paths, deep_merge, load_config
from srpipe.dsp.afe import agc, vad
from srpipe.generated import afe, grid
from srpipe.scenes import vad as vad_scenes

HOP = grid.HOP_SAMPLES
FULL_SCALE = np.float32(32767.0 / 32768.0)


@dataclass(frozen=True)
class AgcResult:
    noise: str
    level_dbfs: float
    out_level_dbfs: float
    peak_dbfs: float
    full_scale_samples: int
    gain_spread_db: float


def active_level_dbfs(powers: np.ndarray, gate_db: float = afe.AGC_LEVEL_GATE_DB) -> float:
    """Mean power of the hops within gate_db under that mean, found by iteration, like the ITU-T P.56 active level."""
    level = float(np.mean(powers))
    for _ in range(50):
        new = float(np.mean(powers[powers >= level * 10 ** (-gate_db / 10)]))
        if abs(new - level) <= 1e-9 * level:
            break
        level = new
    return 10 * np.log10(level)


def run_chain(scene: vad_scenes.Scene, aggressiveness: int) -> tuple[np.ndarray, np.ndarray]:
    """Output lined up with the input (the look-ahead removed) and the gain of every hop."""
    detector, control = vad.Vad(aggressiveness), agc.Agc()
    outs, gains = [], []
    for k in range(0, len(scene.signal) - HOP + 1, HOP):
        hop = scene.signal[k : k + HOP]
        out, gain_db = control.process(hop, detector.process(hop).speech)
        outs.append(out)
        gains.append(gain_db)
    lag = agc.lookahead_samples(afe.AGC_LOOKAHEAD_MS)
    out = np.concatenate(outs)
    return np.concatenate([out[lag:], np.zeros(lag, dtype=np.float32)]), np.array(gains)


def _score(job: tuple[int, str, float, float, dict, dict]) -> AgcResult:
    index, noise_name, snr_db, level_dbfs, cfg, files = job
    rng = np.random.default_rng([cfg["seed"], index])
    order = rng.permutation(len(files["speech"]))
    utterances, seconds = [], 0.0
    for i in order:
        u = read_wav(Path(files["speech"][i]))[0][:, 0]
        utterances.append(u)
        seconds += len(u) / grid.SAMPLE_RATE_HZ
        if seconds >= cfg["speech_s_per_scene"]:
            break
    noise = read_wav(Path(files["noise"][noise_name]))[0][:, 0]
    scene = vad_scenes.build(utterances, noise, noise_name, snr_db, level_dbfs, cfg, rng)
    out, gains = run_chain(scene, cfg.get("vad_aggressiveness", afe.VAD_AGGRESSIVENESS))
    settled = round(cfg["settle_s"] * grid.SAMPLE_RATE_HZ / HOP)
    hops = out[: len(scene.labels) * HOP].reshape(-1, HOP).astype(np.float64)
    speech = np.mean(hops[settled:][scene.labels[settled:]] ** 2, axis=1)
    return AgcResult(
        noise_name,
        level_dbfs,
        active_level_dbfs(speech),
        float(20 * np.log10(np.max(np.abs(out)))),
        int(np.sum(np.abs(out) >= FULL_SCALE)),
        float(np.ptp(gains[settled:])),
    )


def evaluate(cfg: dict, raw_root: Path, workers: int) -> list[AgcResult]:
    speech = sorted(str(p) for p in (raw_root / cfg["speech"]).glob("*/*.wav"))
    noise = {p.stem: str(p) for p in sorted((raw_root / cfg["noise"]).glob("*.wav"))}
    if not speech or not noise:
        raise FileNotFoundError(
            f"no speech under {raw_root / cfg['speech']} or no noise under {raw_root / cfg['noise']}"
        )
    files = {"speech": speech, "noise": noise}
    jobs = [(i, n, s, lv, cfg, files) for i, (n, s, lv) in enumerate(product(noise, cfg["snr_db"], cfg["level_dbfs"]))]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_score, jobs))


def table(results: list[AgcResult]) -> str:
    target = afe.AGC_TARGET_DBFS
    lines = [
        "| Mức vào dBFS | Mức ra trung bình dBFS | Lệch đích xa nhất dB | Đỉnh ra cao nhất dBFS | Mẫu chạm toàn thang"
        " | Gain dao động dB (lớn nhất) |",
        "|---|---|---|---|---|---|",
    ]
    for level in sorted({r.level_dbfs for r in results}):
        rs = [r for r in results if r.level_dbfs == level]
        out = [r.out_level_dbfs for r in rs]
        worst = max((o - target for o in out), key=abs)
        cells = [
            vad_scenes.number(level, 0),
            vad_scenes.number(float(np.mean(out)), 2),
            vad_scenes.number(worst, 2),
            vad_scenes.number(max(r.peak_dbfs for r in rs), 2),
            str(sum(r.full_scale_samples for r in rs)),
            vad_scenes.number(max(r.gain_spread_db for r in rs), 1),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(
        f"{len(results)} cảnh; mức ra: mức hoạt động kiểu P.56 trên các bước nói sau settle_s; đích {target:g} dBFS."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("overrides", nargs="*", help="a.b=value overrides of configs/afe/agc.yaml")
    args = parser.parse_args(argv)
    cfg = deep_merge(load_config("afe/vad")["eval"], load_config("afe/agc", overrides=args.overrides)["eval"])
    print(table(evaluate(cfg, data_paths()["raw"], args.workers)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
