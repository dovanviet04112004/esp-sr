"""Write the golden cases of the pure dsp blocks into contracts/golden/<block>/ (KEHOACH 3.14, 4.2).

Run from ml/: uv run python -m srpipe.dsp.emit_golden. Output is deterministic, so a re-run on an
unchanged reference rewrites the same bytes, which ml/tests/test_emit_golden.py checks.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from srpipe.dsp.spec import stft
from srpipe.generated import grid
from srpipe.golden.gold import write_gold

REPO_ROOT = Path(__file__).resolve().parents[4]
GOLDEN_ROOT = REPO_ROOT / "contracts" / "golden"
STFT_HOPS = 16
NEGATIVE_HOPS = 4
SEED = 20260926


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=GOLDEN_ROOT)
    args = parser.parse_args()
    for path in emit_stft(args.out):
        print(path.relative_to(args.out) if path.is_relative_to(args.out) else path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
