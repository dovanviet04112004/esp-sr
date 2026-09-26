"""Write the golden cases of the pure dsp blocks into contracts/golden/<block>/ (KEHOACH 3.14, 4.2).

Run from ml/: uv run python -m srpipe.dsp.emit_golden. Output is deterministic, so a re-run on an
unchanged reference rewrites the same bytes, which ml/tests/test_emit_golden.py checks.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from srpipe.dsp.afe import chain
from srpipe.dsp.spec import mel, stft
from srpipe.generated import grid
from srpipe.golden.gold import write_gold

REPO_ROOT = Path(__file__).resolve().parents[4]
GOLDEN_ROOT = REPO_ROOT / "contracts" / "golden"
STFT_HOPS = 16
NEGATIVE_HOPS = 4
SEED = 20260926
MEL_FRAMES = 8
CHAIN_HOPS = 16
CHAIN_RESET_HOP = 8
MEL_CASES = (
    (mel.MelConfig(n_bands=40, f_min_hz=20.0, f_max_hz=7600.0, log_floor=1e-6), 13),
    (mel.MelConfig(n_bands=80, f_min_hz=0.0, f_max_hz=8000.0, log_floor=1e-10), 20),
    (mel.MelConfig(n_bands=24, f_min_hz=100.0, f_max_hz=4000.0, log_floor=1e-3), 24),
)


def _signals(rng: np.random.Generator, hops: int) -> dict[str, np.ndarray]:
    n = hops * grid.HOP_SAMPLES
    t = np.arange(n) / grid.SAMPLE_RATE_HZ
    clicks = np.zeros(n)
    clicks[:: grid.SAMPLE_RATE_HZ // 120] = 0.9
    return {
        "noise": rng.uniform(-0.5, 0.5, n),
        "tone_and_clicks": 0.4 * np.sin(2 * np.pi * 440.0 * t) + clicks * 0.5,
        "chirp": 0.5 * np.sin(2 * np.pi * (50.0 * t + (7900.0 - 50.0) / 2.0 * t**2 / t[-1])),
        "near_full_scale": rng.uniform(-0.99, 0.99, n),
    }


def stft_case(signal: np.ndarray) -> dict[str, np.ndarray]:
    """signal (hops * HOP), the bins the analyser gives (hops, N_BINS, 2) and the synthesis of those bins."""
    signal = signal.astype(np.float32)
    spectra = stft.analyze_signal(signal)
    rebuilt = stft.synthesize_signal(spectra)
    bins = np.stack([spectra.real, spectra.imag], axis=-1).astype(np.float32)
    return {"signal": signal, "bins": bins, "rebuilt": rebuilt}


def emit_stft(root: Path) -> list[Path]:
    """Four cases of STFT_HOPS hops, then a negative control whose rebuilt output is one sample late."""
    rng = np.random.default_rng(SEED)
    written = []
    for index, (_, signal) in enumerate(_signals(rng, STFT_HOPS).items()):
        path = root / "stft" / f"case_{index:03d}.gold"
        write_gold(path, stft_case(signal))
        written.append(path)
    negative = stft_case(rng.uniform(-0.5, 0.5, NEGATIVE_HOPS * grid.HOP_SAMPLES))
    negative["rebuilt"] = np.concatenate([[0.0], negative["rebuilt"][:-1]]).astype(np.float32)
    path = root / "stft" / "case_neg_000.gold"
    write_gold(path, negative)
    written.append(path)
    return written


def mel_case(cfg: mel.MelConfig, n_ceps: int, signal: np.ndarray) -> dict[str, np.ndarray]:
    """The configuration as floats, the bins of each frame, and the log-mel and MFCC the reference gives."""
    spectra = stft.analyze_signal(signal.astype(np.float32))
    bank = mel.Mel(cfg)
    log_mel = np.stack([bank.log(s) for s in spectra])
    return {
        "config": np.array([cfg.n_bands, cfg.f_min_hz, cfg.f_max_hz, cfg.log_floor, n_ceps], dtype=np.float32),
        "bins": np.stack([spectra.real, spectra.imag], axis=-1).astype(np.float32),
        "log_mel": log_mel,
        "mfcc": np.stack([bank.mfcc(frame, n_ceps) for frame in log_mel]),
    }


def emit_mel(root: Path) -> list[Path]:
    """Three configurations on noise, two tones and near silence, then a negative control one band off."""
    rng = np.random.default_rng(SEED + 1)
    n = MEL_FRAMES * grid.HOP_SAMPLES
    t = np.arange(n) / grid.SAMPLE_RATE_HZ
    inputs = (
        rng.uniform(-0.5, 0.5, n),
        0.4 * np.sin(2 * np.pi * 300.0 * t) + 0.3 * np.sin(2 * np.pi * 2500.0 * t),
        rng.uniform(-1e-4, 1e-4, n),
    )
    written = []
    for index, ((cfg, n_ceps), signal) in enumerate(zip(MEL_CASES, inputs, strict=True)):
        path = root / "mel" / f"case_{index:03d}.gold"
        write_gold(path, mel_case(cfg, n_ceps, signal))
        written.append(path)
    negative = mel_case(*MEL_CASES[0], rng.uniform(-0.5, 0.5, n))
    negative["log_mel"] = np.roll(negative["log_mel"], -1, axis=1)
    path = root / "mel" / "case_neg_000.gold"
    write_gold(path, negative)
    written.append(path)
    return written


def _chain_inputs(rng: np.random.Generator) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """(ch0, ch1, reset flags) per case: two tones, clipped noise, a chirp with a reset, near silence."""
    n = CHAIN_HOPS * grid.HOP_SAMPLES
    t = np.arange(n) / grid.SAMPLE_RATE_HZ
    no_reset = np.zeros(CHAIN_HOPS, dtype=np.uint8)
    with_reset = no_reset.copy()
    with_reset[CHAIN_RESET_HOP] = 1
    chirp = 9000.0 * np.sin(2 * np.pi * (50.0 * t + (7900.0 - 50.0) / 2.0 * t**2 / t[-1]))
    quiet = rng.integers(-3, 4, n)
    quiet[: n // 2] = 0
    return [
        (
            8000.0 * np.sin(2 * np.pi * 440.0 * t),
            6000.0 * np.sin(2 * np.pi * 1000.0 * t) + rng.normal(0, 300, n),
            no_reset,
        ),
        (rng.uniform(-40000, 40000, n), rng.uniform(-40000, 40000, n), no_reset),
        (chirp, chirp, with_reset),
        (quiet, quiet, no_reset),
    ]


def chain_case(ch0: np.ndarray, ch1: np.ndarray, reset: np.ndarray) -> dict[str, np.ndarray]:
    """Interleaved int16 input, the hops to reset before, and every field of the frames the chain gives."""
    pcm_min, pcm_max = np.iinfo(np.int16).min, np.iinfo(np.int16).max
    mics = np.stack([ch0, ch1], axis=-1)
    interleaved = np.clip(np.rint(mics), pcm_min, pcm_max).astype(np.int16).reshape(reset.size, -1)
    ch = chain.Chain("MM")
    frames = []
    for hop, flag in zip(interleaved, reset, strict=True):
        if flag:
            ch.reset()
        frames.append(ch.process(hop))
    return {
        "input": interleaved,
        "reset": reset,
        "pcm": np.stack([f.pcm for f in frames]),
        "seq": np.array([f.seq for f in frames], dtype=np.int32),
        "doa_deg": np.array([f.doa_deg for f in frames], dtype=np.int16),
        "doa_conf": np.array([f.doa_conf for f in frames], dtype=np.uint8),
        "vad": np.array([f.vad for f in frames], dtype=np.uint8),
        "level_dbfs": np.array([f.level_dbfs for f in frames], dtype=np.int8),
        "gain_db": np.array([f.gain_db for f in frames], dtype=np.int8),
        "flags": np.array([f.flags for f in frames], dtype=np.int32),
    }


def emit_chain(root: Path) -> list[Path]:
    """Four cases through the default facade chain, then a negative control whose output is one sample late."""
    rng = np.random.default_rng(SEED + 2)
    written = []
    for index, inputs in enumerate(_chain_inputs(rng)):
        path = root / "chain" / f"case_{index:03d}.gold"
        write_gold(path, chain_case(*inputs))
        written.append(path)
    n = CHAIN_HOPS * grid.HOP_SAMPLES
    negative = chain_case(rng.normal(0, 4000, n), rng.normal(0, 4000, n), np.zeros(CHAIN_HOPS, dtype=np.uint8))
    late = np.concatenate([[0], negative["pcm"].reshape(-1)[:-1]]).astype(np.int16)
    negative["pcm"] = late.reshape(negative["pcm"].shape)
    path = root / "chain" / "case_neg_000.gold"
    write_gold(path, negative)
    written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=GOLDEN_ROOT)
    args = parser.parse_args()
    for path in emit_stft(args.out) + emit_mel(args.out) + emit_chain(args.out):
        print(path.relative_to(args.out) if path.is_relative_to(args.out) else path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
