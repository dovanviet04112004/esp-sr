"""The speaker scoring that ai_engine/src/speaker/ mirrors (KEHOACH 3.17): b0's int8 embedding brought to unit length in
float32, a voiceprint from the enrolment's unit embeddings, the score as the permille of a dot product, and the decision
against the deployed threshold. emit writes contracts/golden/speaker/, which the C matches exactly.
Run: python -m srpipe.tasks.speaker.postproc.score [--out <golden root>]
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from srpipe.core.config import ML_ROOT
from srpipe.generated import speaker
from srpipe.golden.gold import write_gold

GOLDEN_ROOT = ML_ROOT.parent / "contracts" / "golden"
BLOCK = "speaker"
PERMILLE = np.float32(1000.0)
SCORE_LIMIT = 1000
SEED = 20261011
GOLDEN_DIMS = 192  # b0's embed_dim, so the cases look like its output
OWNER_SPREAD = 0.45  # noise around the owner's direction, per unit length
TESTS = 12
INT8_MIN, INT8_MAX = -128, 127


def to_unit(v: np.ndarray) -> np.ndarray:
    """v over its L2 length in float32: squares summed in order, sqrtf, one division each; zeros stay zeros."""
    v = np.asarray(v, dtype=np.float32)
    total = np.float32(0.0)
    for x in v:
        total = np.float32(total + x * x)
    norm = np.sqrt(total)
    return np.zeros_like(v) if norm == 0 else v / norm


def unit(raw: np.ndarray, exponent: int) -> np.ndarray:
    """The network's int8 embedding times 2^exponent, exact in float32, at unit length."""
    return to_unit(np.asarray(raw, dtype=np.int8).astype(np.float32) * np.float32(2.0**exponent))


def voiceprint(units: Sequence[np.ndarray]) -> np.ndarray:
    """The enrolment's unit embeddings summed in order in float32, at unit length."""
    total = np.zeros(len(units[0]), dtype=np.float32)
    for u in units:
        total = total + np.asarray(u, dtype=np.float32)
    return to_unit(total)


def score_permille(u: np.ndarray, print_: np.ndarray) -> int:
    """Their dot product summed in order in float32, in thousandths rounded half to even as lrintf, within +-1000."""
    dot = np.float32(0.0)
    for a, b in zip(np.asarray(u, dtype=np.float32), np.asarray(print_, dtype=np.float32), strict=True):
        dot = np.float32(dot + a * b)
    return int(np.clip(np.rint(np.float32(dot * PERMILLE)), -SCORE_LIMIT, SCORE_LIMIT))


def accepts(score: int, threshold_permille: int) -> bool:
    """A command passes when its score reaches the threshold."""
    return score >= threshold_permille


def case(
    enrol_raw: np.ndarray, test_raw: np.ndarray, exponent: int, threshold: int, raw_print: bool = False, rule=accepts
) -> dict[str, np.ndarray]:
    """One golden case: the enrolment's and the tests' int8 embeddings with their exponent and the threshold, the
    tests' unit embeddings, the voiceprint, and per test its score and decision. raw_print sums the embeddings ahead of
    their unit length, a negative control."""
    if raw_print:
        print_ = to_unit(sum(np.asarray(r, dtype=np.float32) * np.float32(2.0**exponent) for r in enrol_raw))
    else:
        print_ = voiceprint([unit(r, exponent) for r in enrol_raw])
    units = np.stack([unit(r, exponent) for r in test_raw])
    scores = [score_permille(u, print_) for u in units]
    return {
        "enrol_raw": np.asarray(enrol_raw, dtype=np.int8),
        "test_raw": np.asarray(test_raw, dtype=np.int8),
        "exponent": np.array([exponent], dtype=np.int32),
        "threshold": np.array([threshold], dtype=np.int32),
        "units": units.astype(np.float32),
        "voiceprint": print_.astype(np.float32),
        "decision": np.array([[s, int(rule(s, threshold))] for s in scores], dtype=np.int32),
    }


def quantised(rng: np.random.Generator, direction: np.ndarray, spread: float, rows: int) -> np.ndarray:
    """Rows of int8 around direction, each with its own noise and gain, spanning most of the int8 range."""
    noisy = direction[None] + spread * rng.standard_normal((rows, len(direction)))
    scaled = noisy / np.abs(noisy).max(axis=1, keepdims=True) * rng.uniform(60.0, 127.0, (rows, 1))
    return np.clip(np.rint(scaled), INT8_MIN, INT8_MAX).astype(np.int8)


def owner_case(rng: np.random.Generator, exponent: int) -> tuple[np.ndarray, np.ndarray, int]:
    """Enrolment around one direction; half the tests around it, half around others; the threshold set exactly on
    one test's score, so the comparison at equality is in the case."""
    direction = rng.standard_normal(GOLDEN_DIMS)
    enrol = quantised(rng, direction, OWNER_SPREAD, speaker.ENROL_COMMANDS)
    owners = quantised(rng, direction, OWNER_SPREAD, TESTS // 2)
    others = np.concatenate(
        [quantised(rng, rng.standard_normal(GOLDEN_DIMS), OWNER_SPREAD, 1) for _ in range(TESTS // 2)]
    )
    tests = np.concatenate([owners, others])
    print_ = voiceprint([unit(r, exponent) for r in enrol])
    threshold = score_permille(unit(tests[TESTS // 2 - 1], exponent), print_)
    return enrol, tests, threshold


def edge_tests() -> np.ndarray:
    """A zero embedding, every value at the int8 floor, every value at its ceiling, one value alone."""
    tests = np.zeros((4, GOLDEN_DIMS), dtype=np.int8)
    tests[1], tests[2] = INT8_MIN, INT8_MAX
    tests[3, 7] = 1
    return tests


def strictly_above(score: int, threshold_permille: int) -> bool:
    """accepts with the score required to pass the threshold rather than reach it: a negative control."""
    return score > threshold_permille


def emit(root: Path) -> list[Path]:
    """An owner's enrolment against owners and others at two exponents, the edge embeddings, and two negative controls:
    the voiceprint summed ahead of each embedding's unit length, and a threshold the score must pass, not reach."""
    rng = np.random.default_rng(SEED)
    enrol, tests, threshold = owner_case(rng, -5)
    enrol_b, tests_b, threshold_b = owner_case(rng, -9)
    cases = {
        "case_000": case(enrol, tests, -5, threshold),
        "case_001": case(enrol_b, tests_b, -9, threshold_b),
        "case_002": case(enrol, edge_tests(), -5, 0),
        "case_neg_000": case(enrol, tests, -5, threshold, raw_print=True),
        "case_neg_001": case(enrol, tests, -5, threshold, rule=strictly_above),
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
