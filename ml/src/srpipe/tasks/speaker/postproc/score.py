"""The speaker scoring that ai_engine/src/speaker/ mirrors (KEHOACH 3.17): b0's int8 embedding brought to unit length in
float32, a voiceprint from each person's enrolment, the score as the permille of a dot product, its best over every
voiceprint, and the decision against the deployed threshold. emit writes contracts/golden/speaker/, which the C matches
exactly.
Run: python -m srpipe.tasks.speaker.postproc.score [--out <golden root>]
"""

from __future__ import annotations

import argparse
import json
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
SPEAKER_SET = ML_ROOT.parent / "contracts" / "schema" / "speaker_set.schema.json"
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


def best(u: np.ndarray, prints: Sequence[np.ndarray]) -> tuple[int, int]:
    """The highest score of u over the voiceprints and the index of the one that gave it, the lowest on a tie; with no
    voiceprint, -SCORE_LIMIT and index 0."""
    top, index = -SCORE_LIMIT, 0
    for k, print_ in enumerate(prints):
        s = score_permille(u, print_)
        if k == 0 or s > top:
            top, index = s, k
    return top, index


def first_only(u: np.ndarray, prints: Sequence[np.ndarray]) -> tuple[int, int]:
    """best with the first voiceprint alone: a negative control."""
    return score_permille(u, prints[0]), 0


def accepts(score: int, threshold_permille: int) -> bool:
    """A command passes when its score reaches the threshold."""
    return score >= threshold_permille


def case(
    enrol_raw: np.ndarray,
    test_raw: np.ndarray,
    exponent: int,
    threshold: int,
    people: int = 1,
    raw_print: bool = False,
    rule=accepts,
    pick=best,
) -> dict[str, np.ndarray]:
    """One golden case: the enrolment of each of people, their rows in turn, and the tests' int8 embeddings, with their
    exponent and the threshold; the tests' unit embeddings, the voiceprints, and per test its best score, decision and
    the voiceprint that gave the score. raw_print sums the embeddings ahead of their unit length, a negative control."""
    rows = len(enrol_raw) // people
    prints = []
    for k in range(people):
        part = enrol_raw[k * rows : (k + 1) * rows]
        if raw_print:
            prints.append(to_unit(sum(np.asarray(r, dtype=np.float32) * np.float32(2.0**exponent) for r in part)))
        else:
            prints.append(voiceprint([unit(r, exponent) for r in part]))
    units = np.stack([unit(r, exponent) for r in test_raw])
    picked = [pick(u, prints) for u in units]
    return {
        "enrol_raw": np.asarray(enrol_raw, dtype=np.int8),
        "test_raw": np.asarray(test_raw, dtype=np.int8),
        "exponent": np.array([exponent], dtype=np.int32),
        "threshold": np.array([threshold], dtype=np.int32),
        "people": np.array([people], dtype=np.int32),
        "units": units.astype(np.float32),
        "voiceprint": np.stack(prints).astype(np.float32),
        "decision": np.array([[s, int(rule(s, threshold)), k] for s, k in picked], dtype=np.int32),
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


def household_case(rng: np.random.Generator, exponent: int, people: int) -> tuple[np.ndarray, np.ndarray, int]:
    """people enrolled around their own directions, two tests around each and two strangers; the threshold on the
    score of one person's test, so the comparison at equality is in the case."""
    directions = [rng.standard_normal(GOLDEN_DIMS) for _ in range(people)]
    enrol = np.concatenate([quantised(rng, d, OWNER_SPREAD, speaker.ENROL_COMMANDS) for d in directions])
    tests = np.concatenate(
        [quantised(rng, d, OWNER_SPREAD, 2) for d in directions]
        + [quantised(rng, rng.standard_normal(GOLDEN_DIMS), OWNER_SPREAD, 1) for _ in range(2)]
    )
    rows = speaker.ENROL_COMMANDS
    prints = [voiceprint([unit(r, exponent) for r in enrol[k * rows : (k + 1) * rows]]) for k in range(people)]
    threshold, _ = best(unit(tests[1], exponent), prints)
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
    """An owner's enrolment against owners and others at two exponents, the edge embeddings, three people and the most
    the roster holds, two people enrolled alike so every test ties; and three negative controls: the voiceprint summed
    ahead of each embedding's unit length, a threshold the score must pass, not reach, and the first voiceprint alone
    in place of the best."""
    rng = np.random.default_rng(SEED)
    enrol, tests, threshold = owner_case(rng, -5)
    enrol_b, tests_b, threshold_b = owner_case(rng, -9)
    enrol_3, tests_3, threshold_3 = household_case(rng, -5, 3)
    most = json.loads(SPEAKER_SET.read_text(encoding="utf-8"))["properties"]["speakers"]["maxItems"]
    enrol_8, tests_8, threshold_8 = household_case(rng, -7, most)
    twins = np.concatenate([enrol, enrol])
    cases = {
        "case_000": case(enrol, tests, -5, threshold),
        "case_001": case(enrol_b, tests_b, -9, threshold_b),
        "case_002": case(enrol, edge_tests(), -5, 0),
        "case_003": case(enrol_3, tests_3, -5, threshold_3, people=3),
        "case_004": case(enrol_8, tests_8, -7, threshold_8, people=most),
        "case_005": case(twins, tests, -5, threshold, people=2),
        "case_neg_000": case(enrol, tests, -5, threshold, raw_print=True),
        "case_neg_001": case(enrol, tests, -5, threshold, rule=strictly_above),
        "case_neg_002": case(enrol_3, tests_3, -5, threshold_3, people=3, pick=first_only),
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
