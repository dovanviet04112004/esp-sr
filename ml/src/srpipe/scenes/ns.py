"""The ns slot scored on the vad scenes: how far the noise goes down and how much speech is lost (KEHOACH 3.9, 3.15).

Each part goes through hpf first, as the chain runs it. The gains the noisy mixture gets are applied, hop by hop, to its
speech alone and to its noise alone, through the grid's STFT and iSTFT; each part is compared with itself at unit gain.
python -m srpipe.scenes.ns prints, by noise and SNR, the noise and speech lost and the SNR gained, after settle_s.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from itertools import product
from pathlib import Path

import numpy as np

from srpipe.core.audio_io import read_wav
from srpipe.core.config import data_paths, deep_merge, load_config
from srpipe.dsp.afe import hpf, ns_omlsa
from srpipe.dsp.spec.stft import Istft, Stft
from srpipe.generated import grid
from srpipe.scenes import vad as vad_scenes

HOP = grid.HOP_SAMPLES
GainsOf = Callable[[np.ndarray], np.ndarray]


@dataclass(frozen=True)
class Shadow:
    """Speech and noise after the mixture's gains, and each at unit gain, all with the same delay."""

    speech: np.ndarray
    noise: np.ndarray
    speech_ref: np.ndarray
    noise_ref: np.ndarray


def high_passed(x: np.ndarray) -> np.ndarray:
    """x through dsp_afe's hpf, one hop at a time; hpf is linear, so the parts still add up to the mixture."""
    filt = hpf.Hpf(n_channels=1)
    x = x.astype(np.float32)
    return np.concatenate([filt.process(0, x[k : k + HOP]) for k in range(0, len(x) - HOP + 1, HOP)])


def shadow(speech: np.ndarray, noise: np.ndarray, gains_of: GainsOf) -> Shadow:
    """Run the mixture's power through gains_of hop by hop and apply each hop's gains to both parts."""
    speech, noise = (high_passed(x) for x in (speech, noise))
    mixture = speech + noise
    analyse = [Stft(), Stft(), Stft()]
    rebuild = [Istft(), Istft(), Istft(), Istft()]
    outs: list[list[np.ndarray]] = [[], [], [], []]
    for k in range(0, len(mixture) - HOP + 1, HOP):
        x, s, n = (a.analyze(sig[k : k + HOP]) for a, sig in zip(analyse, (mixture, speech, noise), strict=True))
        gains = gains_of((x.real * x.real + x.imag * x.imag).astype(np.float32))
        for out, synth, bins in zip(outs, rebuild, (gains * s, gains * n, s, n), strict=True):
            out.append(synth.synthesize(bins.astype(np.complex64)))
    return Shadow(*(np.concatenate(o) for o in outs))


@dataclass(frozen=True)
class NsFigures:
    noise_down_db: float
    pause_noise_down_db: float
    speech_down_db: float
    snr_gain_db: float


def hop_energy(x: np.ndarray) -> np.ndarray:
    return np.sum(x[: len(x) // HOP * HOP].reshape(-1, HOP).astype(np.float64) ** 2, axis=1)


def figures(sh: Shadow, labels: np.ndarray, settle_hops: int) -> NsFigures:
    """Noise down over every hop and over pauses, speech down and SNR gained over speech hops, after settle_hops."""
    s, n, s_ref, n_ref = (hop_energy(x) for x in (sh.speech, sh.noise, sh.speech_ref, sh.noise_ref))
    hops = len(s)
    # Output hop k carries input hop k - 1.
    speech_hops = np.concatenate([[False], labels[: hops - 1]]) & (np.arange(hops) >= settle_hops)
    after = np.arange(hops) >= settle_hops
    pauses = after & ~np.concatenate([[False], labels[: hops - 1]])
    noise_down = 10 * np.log10(n_ref[after].sum() / n[after].sum())
    pause_down = 10 * np.log10(n_ref[pauses].sum() / n[pauses].sum())
    speech_down = 10 * np.log10(s_ref[speech_hops].sum() / s[speech_hops].sum())
    snr_in = 10 * np.log10(s_ref[speech_hops].sum() / n_ref[speech_hops].sum())
    snr_out = 10 * np.log10(s[speech_hops].sum() / n[speech_hops].sum())
    return NsFigures(float(noise_down), float(pause_down), float(speech_down), float(snr_out - snr_in))


@dataclass(frozen=True)
class NsResult:
    noise: str
    snr_db: float
    figures: NsFigures


def omlsa_gains() -> GainsOf:
    model = ns_omlsa.Omlsa()
    return lambda power: model.process(power).gain


def _score(job: tuple[int, str, float, float, dict, dict]) -> NsResult:
    index, noise_name, snr_db, level_dbfs, cfg, files = job
    rng = np.random.default_rng([cfg["seed"], index])
    utterances = vad_scenes.pick_utterances(files["speech"], cfg, rng)
    noise = read_wav(Path(files["noise"][noise_name]))[0][:, 0]
    speech, looped, labels = vad_scenes.parts(utterances, noise, snr_db, level_dbfs, cfg, rng)
    settle = round(cfg["settle_s"] * grid.SAMPLE_RATE_HZ / HOP)
    return NsResult(noise_name, snr_db, figures(shadow(speech, looped, omlsa_gains()), labels, settle))


def evaluate(cfg: dict, raw_root: Path, workers: int) -> list[NsResult]:
    files = vad_scenes.scene_files(cfg, raw_root)
    conditions = product(files["noise"], cfg["snr_db"], cfg["level_dbfs"])
    jobs = [(i, n, s, lv, cfg, files) for i, (n, s, lv) in enumerate(conditions)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_score, jobs))


def table(results: list[NsResult], cfg: dict) -> str:
    number = vad_scenes.number
    snrs = cfg["snr_db"]
    lines = [
        "| Nhiễu | " + " | ".join(f"SNR {number(s, 0)} dB" for s in snrs) + " |",
        "|---" * (len(snrs) + 1) + "|",
    ]
    rows = [(n, [r for r in results if r.noise == n]) for n in sorted({r.noise for r in results})]
    rows.append(("trung bình", results))
    for name, rs in rows:
        cells = []
        for snr in snrs:
            f = [r.figures for r in rs if r.snr_db == snr]
            keys = ("noise_down_db", "pause_noise_down_db", "speech_down_db", "snr_gain_db")
            mean = [float(np.mean([getattr(x, k) for x in f])) for k in keys]
            cells.append(" / ".join(number(m, 1) for m in mean))
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(
        f"{len(results)} cảnh; ô: nhiễu giảm dB (mọi bước) / nhiễu giảm dB (quãng nghỉ) / tiếng nói giảm dB (bước nói)"
        f" / SNR tăng dB (bước nói), sau {cfg['settle_s']:g} s đầu."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("overrides", nargs="*", help="a.b=value overrides of configs/afe/ns_omlsa.yaml")
    args = parser.parse_args(argv)
    cfg = deep_merge(load_config("afe/vad")["eval"], load_config("afe/ns_omlsa", overrides=args.overrides)["eval"])
    print(table(evaluate(cfg, data_paths()["raw"], args.workers), cfg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
