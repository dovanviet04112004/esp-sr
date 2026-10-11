"""The speaker scoring that ai_engine/src/speaker/ mirrors (KEHOACH 3.17, ADR-0022): b0's int8 embedding brought to unit
length in float32, a voiceprint from each person's enrolment, its mean and deviation over a cohort of strangers, the
score of a voiceprint as the thousandths of its normalised dot product, the best over every voiceprint, and the decision
against the deployed threshold. emit writes contracts/golden/speaker/, which the C matches exactly.
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
Z_LIMIT = 32767  # thousandths of z held in int16
SEED = 20261011
GOLDEN_DIMS = 192  # b0's embed_dim, so the cases look like its output
OWNER_SPREAD = 0.45  # noise around the owner's direction, per unit length
TESTS = 12
COHORT_ROWS = 48
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


def dot(a: np.ndarray, b: np.ndarray) -> np.float32:
    """Their dot product summed in order in float32, each product rounded on its own."""
    total = np.float32(0.0)
    for x, y in zip(np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32), strict=True):
        total = np.float32(total + x * y)
    return total


def permille_of(d: np.float32) -> int:
    """A dot product in thousandths rounded half to even as lrintf, within +-1000."""
    return int(np.clip(np.rint(np.float32(d * PERMILLE)), -SCORE_LIMIT, SCORE_LIMIT))


def score_permille(u: np.ndarray, print_: np.ndarray) -> int:
    """The cosine of two unit vectors in thousandths."""
    return permille_of(dot(u, print_))


def norm(print_: np.ndarray, cohort: np.ndarray, rows_less: int = 0) -> tuple[np.float32, np.float32]:
    """The mean and deviation of the voiceprint's dot products with each cohort row: float32 sums in order over the
    rows, the deviation the root of the mean squared distance from the mean, 1 when that is 0. rows_less divides the
    squares by fewer rows than there are, a negative control."""
    scores = [dot(row, print_) for row in cohort]
    total = np.float32(0.0)
    for x in scores:
        total = np.float32(total + x)
    mean = np.float32(total / np.float32(len(scores)))
    squares = np.float32(0.0)
    for x in scores:
        gap = np.float32(x - mean)
        squares = np.float32(squares + gap * gap)
    std = np.float32(np.sqrt(np.float32(squares / np.float32(len(scores) - rows_less))))
    return mean, std if std != 0 else np.float32(1.0)


def milli_z(d: np.float32, mean: np.float32, std: np.float32) -> int:
    """(d - mean) / std in float32, in thousandths rounded half to even as lrintf, within +-Z_LIMIT."""
    z = np.float32(np.float32(d - mean) / std)
    return int(np.clip(np.rint(np.float32(z * PERMILLE)), -Z_LIMIT, Z_LIMIT))


def best(
    u: np.ndarray, prints: Sequence[np.ndarray], norms: Sequence[tuple[np.float32, np.float32]]
) -> tuple[int, int, int]:
    """The highest normalised score of u over the voiceprints, the index of the one that gave it, the lowest on a tie,
    and its cosine in thousandths; with no voiceprint, -Z_LIMIT, index 0 and -SCORE_LIMIT."""
    top, index, cosine = -Z_LIMIT, 0, -SCORE_LIMIT
    for k, (print_, (mean, std)) in enumerate(zip(prints, norms, strict=True)):
        d = dot(u, print_)
        z = milli_z(d, mean, std)
        if k == 0 or z > top:
            top, index, cosine = z, k, permille_of(d)
    return top, index, cosine


def first_only(
    u: np.ndarray, prints: Sequence[np.ndarray], norms: Sequence[tuple[np.float32, np.float32]]
) -> tuple[int, int, int]:
    """best with the first voiceprint alone: a negative control."""
    return best(u, prints[:1], norms[:1])


def by_cosine(
    u: np.ndarray, prints: Sequence[np.ndarray], norms: Sequence[tuple[np.float32, np.float32]]
) -> tuple[int, int, int]:
    """The voiceprint of the highest raw cosine with its normalised score: a negative control."""
    cosines = [dot(u, p) for p in prints]
    k = int(np.argmax(cosines))
    return milli_z(cosines[k], *norms[k]), k, permille_of(cosines[k])


def accepts(score: int, threshold_permille: int) -> bool:
    """A command passes when its score reaches the threshold."""
    return score >= threshold_permille


def case(
    enrol_raw: np.ndarray,
    test_raw: np.ndarray,
    exponent: int,
    threshold: int,
    cohort: np.ndarray,
    people: int = 1,
    raw_print: bool = False,
    rule=accepts,
    pick=best,
    rows_less: int = 0,
) -> dict[str, np.ndarray]:
    """One golden case: the enrolment of each of people, their rows in turn, the tests' int8 embeddings, their exponent,
    the threshold in thousandths of z and the cohort; the tests' unit embeddings, the voiceprints, each one's mean and
    deviation over the cohort, and per test its best normalised score, decision, the voiceprint that gave it and that
    one's cosine. raw_print sums the embeddings ahead of their unit length, rows_less divides the deviation by fewer
    rows: negative controls, as rule and pick may be."""
    rows = len(enrol_raw) // people
    prints = []
    for k in range(people):
        part = enrol_raw[k * rows : (k + 1) * rows]
        if raw_print:
            prints.append(to_unit(sum(np.asarray(r, dtype=np.float32) * np.float32(2.0**exponent) for r in part)))
        else:
            prints.append(voiceprint([unit(r, exponent) for r in part]))
    norms = [norm(p, cohort, rows_less) for p in prints]
    units = np.stack([unit(r, exponent) for r in test_raw])
    picked = [pick(u, prints, norms) for u in units]
    return {
        "enrol_raw": np.asarray(enrol_raw, dtype=np.int8),
        "test_raw": np.asarray(test_raw, dtype=np.int8),
        "exponent": np.array([exponent], dtype=np.int32),
        "threshold": np.array([threshold], dtype=np.int32),
        "people": np.array([people], dtype=np.int32),
        "cohort": np.asarray(cohort, dtype=np.float32),
        "units": units.astype(np.float32),
        "voiceprint": np.stack(prints).astype(np.float32),
        "norms": np.array(norms, dtype=np.float32),
        "decision": np.array([[z, int(rule(z, threshold)), k, c] for z, k, c in picked], dtype=np.int32),
    }


def quantised(rng: np.random.Generator, direction: np.ndarray, spread: float, rows: int) -> np.ndarray:
    """Rows of int8 around direction, each with its own noise and gain, spanning most of the int8 range."""
    noisy = direction[None] + spread * rng.standard_normal((rows, len(direction)))
    scaled = noisy / np.abs(noisy).max(axis=1, keepdims=True) * rng.uniform(60.0, 127.0, (rows, 1))
    return np.clip(np.rint(scaled), INT8_MIN, INT8_MAX).astype(np.int8)


def cohort_of(rng: np.random.Generator, exponent: int, toward: np.ndarray | None = None, near: int = 0) -> np.ndarray:
    """COHORT_ROWS strangers' unit embeddings, each around its own direction but near of them around toward: a
    voiceprint there gets a higher mean, so its normalised score ranks lower than its cosine."""
    rows = [quantised(rng, rng.standard_normal(GOLDEN_DIMS), OWNER_SPREAD, 1)[0] for _ in range(COHORT_ROWS - near)]
    if near:
        rows += list(quantised(rng, toward, OWNER_SPREAD, near))
    return np.stack([unit(r, exponent) for r in rows])


def owner_case(rng: np.random.Generator, exponent: int, cohort: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """Enrolment around one direction; half the tests around it, half around others; the threshold set exactly on
    one test's normalised score, so the comparison at equality is in the case."""
    direction = rng.standard_normal(GOLDEN_DIMS)
    enrol = quantised(rng, direction, OWNER_SPREAD, speaker.ENROL_COMMANDS)
    owners = quantised(rng, direction, OWNER_SPREAD, TESTS // 2)
    others = np.concatenate(
        [quantised(rng, rng.standard_normal(GOLDEN_DIMS), OWNER_SPREAD, 1) for _ in range(TESTS // 2)]
    )
    tests = np.concatenate([owners, others])
    print_ = voiceprint([unit(r, exponent) for r in enrol])
    threshold = best(unit(tests[TESTS // 2 - 1], exponent), [print_], [norm(print_, cohort)])[0]
    return enrol, tests, threshold


def household_case(
    rng: np.random.Generator, exponent: int, people: int
) -> tuple[np.ndarray, np.ndarray, int, np.ndarray]:
    """people enrolled around their own directions, two tests around each and two strangers, and a cohort leaning
    towards the first person; the threshold on the normalised score of one person's test, so the comparison at
    equality is in the case."""
    directions = [rng.standard_normal(GOLDEN_DIMS) for _ in range(people)]
    enrol = np.concatenate([quantised(rng, d, OWNER_SPREAD, speaker.ENROL_COMMANDS) for d in directions])
    tests = np.concatenate(
        [quantised(rng, d, OWNER_SPREAD, 2) for d in directions]
        + [quantised(rng, rng.standard_normal(GOLDEN_DIMS), OWNER_SPREAD, 1) for _ in range(2)]
    )
    cohort = cohort_of(rng, exponent, directions[0], COHORT_ROWS // 4)
    rows = speaker.ENROL_COMMANDS
    prints = [voiceprint([unit(r, exponent) for r in enrol[k * rows : (k + 1) * rows]]) for k in range(people)]
    threshold = best(unit(tests[1], exponent), prints, [norm(p, cohort) for p in prints])[0]
    return enrol, tests, threshold, cohort


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
    the roster holds over cohorts leaning towards one of them, two people enrolled alike so every test ties, a cohort
    of one row whose deviation is 0; and five negative controls: the voiceprint summed ahead of each embedding's unit
    length, a threshold the score must pass, not reach, the first voiceprint alone in place of the best, the deviation
    over one row fewer, and the voiceprint of the highest cosine in place of the highest normalised score."""
    rng = np.random.default_rng(SEED)
    cohort_a = cohort_of(rng, -5)
    cohort_b = cohort_of(rng, -9)
    enrol, tests, threshold = owner_case(rng, -5, cohort_a)
    enrol_b, tests_b, threshold_b = owner_case(rng, -9, cohort_b)
    enrol_3, tests_3, threshold_3, cohort_3 = household_case(rng, -5, 3)
    most = json.loads(SPEAKER_SET.read_text(encoding="utf-8"))["properties"]["speakers"]["maxItems"]
    enrol_8, tests_8, threshold_8, cohort_8 = household_case(rng, -7, most)
    twins = np.concatenate([enrol, enrol])
    lone = cohort_a[:1]
    cases = {
        "case_000": case(enrol, tests, -5, threshold, cohort_a),
        "case_001": case(enrol_b, tests_b, -9, threshold_b, cohort_b),
        "case_002": case(enrol, edge_tests(), -5, 0, cohort_a),
        "case_003": case(enrol_3, tests_3, -5, threshold_3, cohort_3, people=3),
        "case_004": case(enrol_8, tests_8, -7, threshold_8, cohort_8, people=most),
        "case_005": case(twins, tests, -5, threshold, cohort_a, people=2),
        "case_006": case(enrol, tests, -5, threshold, lone),
        "case_neg_000": case(enrol, tests, -5, threshold, cohort_a, raw_print=True),
        "case_neg_001": case(enrol, tests, -5, threshold, cohort_a, rule=strictly_above),
        "case_neg_002": case(enrol_3, tests_3, -5, threshold_3, cohort_3, people=3, pick=first_only),
        "case_neg_003": case(enrol, tests, -5, threshold, cohort_a, rows_less=1),
        "case_neg_004": case(enrol_3, tests_3, -5, threshold_3, cohort_3, people=3, pick=by_cosine),
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
