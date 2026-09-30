"""The kws decision that ai_engine/src/command_kws/ mirrors (KEHOACH 3.12): a float32 softmax over logits of the learned
commands, then other, then silence; the winner, or REJECTED by the three rules; and the three scores of
ai_engine_command_result_t in permille, as NVS keeps kws/cmd_reject and kws/cmd_margin. emit writes
contracts/golden/command_kws/, which the C matches exactly.
Run: python -m srpipe.tasks.command.kws.postproc.decide [--out <golden root>]
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np

from srpipe.core.config import ML_ROOT, load_yaml
from srpipe.golden.gold import write_gold
from srpipe.tasks import command

GOLDEN_ROOT = ML_ROOT.parent / "contracts" / "golden"
BLOCK = "command_kws"
REJECTED = -1
PERMILLE = np.float32(1000.0)
DECISION = ("command", "score_permille", "margin_permille", "rest_permille")
SEED = 20260930
RANDOM_ROWS = 256
SMALL_SET = 3
SPREAD = (0.25, 8.0)


def softmax(logits: np.ndarray) -> np.ndarray:
    """Probabilities as the C computes them in float32: exp in double of each logit less the largest, rounded to
    float, summed in order, each divided by the sum."""
    x = np.asarray(logits, dtype=np.float32)
    top = x.max()
    powers = np.array([math.exp(float(v - top)) for v in x], dtype=np.float32)
    total = np.float32(0.0)
    for v in powers:
        total = np.float32(total + v)
    return powers / total


def permille(p: np.float32) -> int:
    """p in thousandths, rounded half to even as lrintf does."""
    return int(np.rint(np.float32(p * PERMILLE)))


def decide(logits: np.ndarray, n_commands: int, reject_permille: int, margin_permille: int) -> np.ndarray:
    """The decision of one window, int32 in the order of DECISION."""
    p = softmax(logits)
    top = int(np.argmax(p))
    second = np.max(np.delete(p, top))
    score = permille(p[top])
    margin = permille(np.float32(p[top] - second))
    rest = permille(np.float32(p[n_commands] + p[n_commands + 1]))
    accepted = top < n_commands and score >= reject_permille and margin >= margin_permille
    return np.array([top if accepted else REJECTED, score, margin, rest], dtype=np.int32)


def case(logits: np.ndarray, n_commands: int, thresholds: np.ndarray, rule=decide) -> dict[str, np.ndarray]:
    """One golden case: rows of logits, each with its reject and margin thresholds, and what rule makes of them."""
    logits = np.asarray(logits, dtype=np.float32)
    thresholds = np.asarray(thresholds, dtype=np.int32)
    return {
        "n_commands": np.array([n_commands], dtype=np.int32),
        "logits": logits,
        "thresholds": thresholds,
        "probs": np.stack([softmax(row) for row in logits]),
        "decision": np.stack([rule(row, n_commands, *t) for row, t in zip(logits, thresholds, strict=True)]),
    }


def random_case(rng: np.random.Generator, n_commands: int, rows: int) -> dict[str, np.ndarray]:
    """Logits of every confidence, from near-uniform to one class far ahead, and thresholds across their range."""
    spread = rng.uniform(*SPREAD, size=(rows, 1))
    logits = rng.standard_normal((rows, n_commands + 2)) * spread
    thresholds = np.stack([rng.integers(0, 1001, rows), rng.integers(0, 501, rows)], axis=1)
    return case(logits, n_commands, thresholds)


def edge_rows(n_commands: int) -> tuple[np.ndarray, np.ndarray]:
    """Rows where each rule decides: other wins, silence wins, two commands tie, logits too large for a naive exp,
    all equal, a winner exactly on the reject threshold and one exactly on the margin."""
    n = n_commands + 2
    rows = np.zeros((7, n), dtype=np.float32)
    rows[0, n_commands] = 6.0
    rows[1, n_commands + 1] = 6.0
    rows[2, [1, 4]] = 5.0
    rows[3, 0], rows[3, 2] = 90.0, -90.0
    rows[5, 2], rows[5, 3] = 3.0, 1.0
    rows[6, 7], rows[6, 0] = 2.5, 2.0
    thresholds = np.full((7, 2), (500, 100), dtype=np.int32)
    thresholds[4] = (0, 0)
    thresholds[5, 0] = decide(rows[5], n_commands, 0, 0)[1]
    thresholds[6] = (0, decide(rows[6], n_commands, 0, 0)[2])
    return rows, thresholds


def strict_threshold(logits: np.ndarray, n_commands: int, reject_permille: int, margin_permille: int) -> np.ndarray:
    """decide with the winner required to pass the reject threshold rather than reach it: the negative control."""
    out = decide(logits, n_commands, reject_permille, margin_permille)
    if out[0] != REJECTED and out[1] == reject_permille:
        out[0] = REJECTED
    return out


def emit(root: Path) -> list[Path]:
    """Random rows over the learned commands of the default set and over a small set, the edge rows, and a negative
    control that rejects a winner sitting exactly on the reject threshold."""
    rng, n_commands = np.random.default_rng(SEED), len(command.learned(load_yaml(command.CONFIG)))
    rows, thresholds = edge_rows(n_commands)
    cases = {
        "case_000": random_case(rng, n_commands, RANDOM_ROWS),
        "case_001": random_case(rng, SMALL_SET, RANDOM_ROWS),
        "case_002": case(rows, n_commands, thresholds),
        "case_neg_000": case(rows, n_commands, thresholds, strict_threshold),
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
