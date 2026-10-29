"""The wake output's post-processing that ai_engine/src/wake/ mirrors (KEHOACH 3.11): a trailing moving average
of the per-hop probability, then triggers against a threshold with a lockout; chip_scores is the same in integer
permille as the chip runs it on int8 logits, and python -m srpipe.tasks.wake.postproc.smooth writes its golden."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from srpipe.core.config import ML_ROOT
from srpipe.golden.gold import write_gold

LOGITS = 256  # an int8 logit's values, -128 first
GOLDEN_ROOT = ML_ROOT.parent / "contracts" / "golden"
BLOCK = "wake_score"
GOLDEN_HOPS = 256
SEED = 20261029


def permille_table(exponent: int) -> np.ndarray:
    """The probability in permille of each int8 logit at exponent, 1000 / (1 + e^-x) rounded half up in double, as
    ai_engine builds it at load."""
    x = np.arange(-LOGITS // 2, LOGITS // 2, dtype=np.float64) * 2.0**exponent
    return np.floor(1000.0 / (1.0 + np.exp(-x)) + 0.5).astype(np.uint16)


def chip_scores(logits: np.ndarray, exponent: int, hops: int) -> np.ndarray:
    """Each hop's permille through permille_table, then the mean of it and the hops - 1 before it in integers,
    rounded half up; the first hops average what exists so far."""
    p = permille_table(exponent)[np.asarray(logits, dtype=np.int64) + LOGITS // 2].astype(np.int64)
    total = np.cumsum(p)
    ahead = np.concatenate([np.zeros(hops, dtype=np.int64), total])[: len(p)]
    count = np.minimum(np.arange(1, len(p) + 1), hops)
    return ((total - ahead + count // 2) // count).astype(np.uint16)


def smooth(probs: np.ndarray, hops: int) -> np.ndarray:
    """Mean of each hop and the hops - 1 before it; the first hops average what exists so far."""
    x = np.asarray(probs, dtype=np.float32)
    total = np.cumsum(x, dtype=np.float64)
    ahead = np.concatenate([np.zeros(hops, dtype=np.float64), total[:-hops]])
    count = np.minimum(np.arange(1, len(x) + 1), hops)
    return ((total - ahead[: len(x)]) / count).astype(np.float32)


def triggers(smoothed: np.ndarray, threshold: float, lockout_hops: int) -> np.ndarray:
    """Hops where the smoothed score reaches the threshold, each followed by lockout_hops in which none counts."""
    above = np.flatnonzero(smoothed >= threshold)
    fired, k = [], 0
    while k < len(above):
        fired.append(above[k])
        k = int(np.searchsorted(above, above[k] + lockout_hops, side="right"))
    return np.array(fired, dtype=np.int64)


def case(logits: np.ndarray, exponent: int, hops: int, off_exponent: int = 0) -> dict[str, np.ndarray]:
    """One golden case: the logits, the exponent and hops the C scorer takes, and the scores chip_scores gives them,
    at an exponent off_exponent away for a negative control."""
    return {
        "logits": np.asarray(logits, dtype=np.int8),
        "params": np.array([exponent, hops], dtype=np.float32),
        "score": chip_scores(logits, exponent + off_exponent, hops).astype(np.float32),
    }


def emit(root: Path) -> list[Path]:
    """Random logits at three exponents, one without smoothing, the int8 ends and a full ramp, GOLDEN_HOPS hops each;
    then a negative control scored at an exponent one over the logits'."""
    rng = np.random.default_rng(SEED)
    n = GOLDEN_HOPS
    ends = np.concatenate([np.full(n // 4, 127), np.full(n // 4, -128), np.arange(-128, 128, 2)])[:n]
    cases = {
        "case_000": case(rng.integers(-128, 128, n), -4, 5),
        "case_001": case(rng.integers(-128, 128, n), -3, 1),
        "case_002": case(ends, -5, 5),
        "case_003": case(rng.integers(-128, 128, n), -2, 7),
        "case_neg_000": case(rng.integers(-128, 128, n), -4, 5, off_exponent=1),
    }
    written = []
    for name, tensors in cases.items():
        path = root / BLOCK / f"{name}.gold"
        write_gold(path, tensors)
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=GOLDEN_ROOT)
    for path in emit(parser.parse_args().out):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
