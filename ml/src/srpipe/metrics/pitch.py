"""The online pitch mirror against Kaldi's own tracker on VIVOS test (E11-T8 step 2, KEHOACH 3.11).

Kaldi runs through kalpy in the aligner's Docker image (ml/afe_ref/kaldi_pitch/run.py), twice at the grid hop:
first-pass online, one hop per chunk, no latency, no right context, which srpipe.dsp.spec.pitch ports and must match;
and over the whole file with 0.75 s of context each side, the tracker's best answer. Voiced frames: whole-file POV
above a half. Run: python -m srpipe.metrics.pitch [--limit N]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import soundfile as sf

from srpipe.core.config import CONFIGS, ML_ROOT, data_paths, load_yaml
from srpipe.dsp.spec import pitch
from srpipe.generated import grid
from srpipe.tts import CONFIG as TTS_CONFIG

REFERENCE = ML_ROOT / "afe_ref" / "kaldi_pitch"
CORPUS = Path("speech") / "vivos" / "test" / "waves"
FLUSHED_FRAMES = 3
SAME_LOG_PITCH = 1e-4
VOICED_POV = 0.5
GROSS_RATIO = 0.05
WORKERS = 12


def context_frames(cfg: pitch.PitchConfig) -> int:
    return round(cfg.normalization_left_s * grid.SAMPLE_RATE_HZ / grid.HOP_SAMPLES)


def kaldi_requests(wav: Path, work: Path, cfg: pitch.PitchConfig) -> list[dict]:
    """The online and whole-file runs of one clip, with the options of the same tracker at the grid hop."""
    tracker = {
        "frame_shift_ms": grid.HOP_SAMPLES * 1000.0 / grid.SAMPLE_RATE_HZ,
        "frame_length_ms": cfg.window_s * 1000.0,
        "min_f0": cfg.min_f0_hz,
        "max_f0": cfg.max_f0_hz,
        "soft_min_f0": cfg.soft_min_f0,
        "penalty_factor": cfg.penalty_factor,
        "lowpass_cutoff": cfg.lowpass_cutoff_hz,
        "resample_freq": cfg.resample_hz,
        "delta_pitch": cfg.delta_pitch,
        "nccf_ballast": cfg.nccf_ballast,
        "lowpass_filter_width": cfg.lowpass_zeros,
        "upsample_filter_width": cfg.upsample_zeros,
    }
    process = {
        "normalization_left_context": context_frames(cfg),
        "delta_window": cfg.delta_window,
        "delta_pitch_noise_stddev": 0.0,
        "pov_scale": cfg.pov_scale,
        "pitch_scale": cfg.pitch_scale,
        "delta_pitch_scale": cfg.delta_pitch_scale,
        "add_raw_log_pitch": True,
    }
    online = {"frames_per_chunk": 1, "simulate_first_pass_online": True, "max_frames_latency": 0}
    return [
        {
            "wav": str(wav),
            "out": str(work / "online" / f"{wav.stem}.npy"),
            "pitch": tracker | online,
            "process": process | {"normalization_right_context": 0},
        },
        {
            "wav": str(wav),
            "out": str(work / "whole" / f"{wav.stem}.npy"),
            "pitch": tracker,
            "process": process | {"normalization_right_context": context_frames(cfg)},
        },
    ]


def run_kaldi(requests: list[dict], work: Path, data_root: Path) -> None:
    """Every request through the reference script in the aligner's image, as the user, on the CPU."""
    listing = work / "kaldi_requests.json"
    listing.write_text(json.dumps(requests), encoding="utf-8")
    image = load_yaml(TTS_CONFIG)["align"]["image"]
    mounts = ["-v", f"{REFERENCE}:/ref:ro", "-v", f"{data_root}:{data_root}"]
    user = ["--user", f"{os.getuid()}:{os.getgid()}"]
    subprocess.run(["docker", "run", "--rm", *user, *mounts, image, "python", "/ref/run.py", str(listing)], check=True)


def ours(task: tuple[str, str, dict]) -> None:
    wav, out, spec = task
    x, rate = sf.read(wav, dtype="float32")
    if rate != grid.SAMPLE_RATE_HZ:
        raise ValueError(f"{wav}: {rate} Hz, want {grid.SAMPLE_RATE_HZ}")
    features, raw = pitch.pitch_features(x, pitch.PitchConfig(**spec))
    log_pitch = np.log(np.maximum(raw[:, 1:], np.finfo(np.float32).tiny))
    np.save(out, np.concatenate([features, log_pitch], axis=1)[pitch.LEAD_HOPS :].astype(np.float32))


def nccf_of(pov_feature: np.ndarray, cfg: pitch.PitchConfig) -> np.ndarray:
    """The NCCF behind Kaldi's POV feature, pov_scale ((1.0001 - n)^0.15 - 1)."""
    return 1.0001 - np.maximum(pov_feature / cfg.pov_scale + 1.0, 0.0) ** (1.0 / 0.15)


def compare(a: np.ndarray, b: np.ndarray, voiced: np.ndarray) -> dict:
    """Two tracks of [POV feature, normalised log pitch, delta, raw log pitch] over the frames both have."""
    n = min(len(a), len(b), len(voiced))
    a, b, voiced = a[:n], b[:n], voiced[:n]
    same = np.abs(a[:, 3] - b[:, 3]) < SAME_LOG_PITCH
    gross = np.abs(np.exp(a[:, 3] - b[:, 3]) - 1.0) > GROSS_RATIO
    return {
        "frames": n,
        "voiced": int(voiced.sum()),
        "same_state": float(same.mean()),
        "same_state_voiced": float(same[voiced].mean()) if voiced.any() else float("nan"),
        "gross_voiced": float(gross[voiced].mean()) if voiced.any() else float("nan"),
        "pov_corr": float(np.corrcoef(a[:, 0], b[:, 0])[0, 1]),
        "max_abs": [float(np.max(np.abs(a[:, k] - b[:, k]))) for k in range(3)],
    }


def summary(name: str, rows: list[dict]) -> str:
    frames, voiced = sum(r["frames"] for r in rows), sum(r["voiced"] for r in rows)
    w_all = np.array([r["frames"] for r in rows], dtype=np.float64)
    w_voiced = np.array([r["voiced"] for r in rows], dtype=np.float64)

    def mean(key: str, w: np.ndarray) -> float:
        keep = w > 0
        return float(np.sum(w[keep] * np.array([r[key] for r in rows])[keep]) / w[keep].sum())

    diffs = np.array([r["max_abs"] for r in rows])
    return (
        f"| {name} | {frames} | {voiced} | {mean('same_state', w_all):.2%} | {mean('same_state_voiced', w_voiced):.2%} "
        f"| {mean('gross_voiced', w_voiced):.2%} | {np.median([r['pov_corr'] for r in rows]):.4f} "
        f"| {np.median(diffs[:, 0]):.2e} / {np.median(diffs[:, 1]):.2e} / {np.median(diffs[:, 2]):.2e} |"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=None, help="only the first N utterances")
    limit = parser.parse_args(argv).limit
    paths = data_paths()
    spec = load_yaml(CONFIGS / "scenes" / "device.yaml")["pitch"]
    cfg = pitch.PitchConfig(**spec)
    wavs = sorted((paths["raw"] / CORPUS).glob("*/*.wav"))[:limit]
    work = paths["cache"] / "pitch_ref"
    for folder in ("ours", "online", "whole"):
        (work / folder).mkdir(parents=True, exist_ok=True)
    run_kaldi([r for w in wavs for r in kaldi_requests(w, work, cfg)], work, paths["data_root"])
    with ProcessPoolExecutor(WORKERS) as pool:
        list(pool.map(ours, [(str(w), str(work / "ours" / f"{w.stem}.npy"), spec) for w in wavs], chunksize=4))
    pairs: dict[str, list[dict]] = {"ours / Kaldi online": [], "ours / Kaldi whole": [], "Kaldi online / whole": []}
    for w in wavs:
        got = {k: np.load(work / k / f"{w.stem}.npy") for k in ("ours", "online", "whole")}
        got["online"] = got["online"][: len(got["online"]) - FLUSHED_FRAMES]
        voiced = np.array([pitch.nccf_to_pov(n) > VOICED_POV for n in nccf_of(got["whole"][:, 0], cfg)])
        pairs["ours / Kaldi online"].append(compare(got["ours"], got["online"], voiced))
        pairs["ours / Kaldi whole"].append(compare(got["ours"], got["whole"], voiced))
        pairs["Kaldi online / whole"].append(compare(got["online"], got["whole"], voiced))
    print(f"{len(wavs)} utterances of {CORPUS}, frame shift {grid.HOP_SAMPLES * 1000 // grid.SAMPLE_RATE_HZ} ms")
    print(
        "| Pair | Frames | Voiced | Same lag, all | Same lag, voiced | F0 off > 5%, voiced | POV corr "
        "| max abs diff POV / norm / delta (median) |"
    )
    print("|---|---|---|---|---|---|---|---|")
    for name, rows in pairs.items():
        print(summary(name, rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
