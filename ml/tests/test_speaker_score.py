"""srpipe.tasks.speaker.postproc.score against float64 cosine scoring, and its golden cases (KEHOACH 3.17)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from srpipe.golden.gold import read_gold
from srpipe.tasks.speaker.postproc import score

RNG = np.random.default_rng(20261011)


def test_unit_matches_a_float64_normalisation_and_keeps_zeros() -> None:
    raw = RNG.integers(-128, 128, score.GOLDEN_DIMS).astype(np.int8)
    got = score.unit(raw, -6)
    want = raw.astype(np.float64) / np.linalg.norm(raw.astype(np.float64))
    assert got.dtype == np.float32
    assert np.max(np.abs(got - want)) < 1e-6
    assert not score.unit(np.zeros(score.GOLDEN_DIMS, dtype=np.int8), -6).any()


def test_scores_span_minus_to_plus_a_thousand() -> None:
    u = score.unit(RNG.integers(-128, 128, score.GOLDEN_DIMS).astype(np.int8), -5)
    assert score.score_permille(u, u) == 1000
    assert score.score_permille(u, -u) == -1000
    other = np.zeros(score.GOLDEN_DIMS, dtype=np.float32)
    other[0], other[1] = u[1], -u[0]
    assert score.score_permille(u, score.to_unit(other)) == 0


def test_voiceprint_is_the_float64_mean_direction_of_unit_embeddings() -> None:
    raws = [RNG.integers(-128, 128, score.GOLDEN_DIMS).astype(np.int8) for _ in range(10)]
    units = [score.unit(r, -7) for r in raws]
    mean = np.mean([u.astype(np.float64) for u in units], axis=0)
    assert np.max(np.abs(score.voiceprint(units) - mean / np.linalg.norm(mean))) < 1e-6


def test_a_score_on_the_threshold_passes() -> None:
    assert score.accepts(420, 420) and not score.accepts(419, 420)


def test_best_takes_the_highest_score_and_the_lowest_voiceprint_on_a_tie() -> None:
    u = score.unit(RNG.integers(-128, 128, score.GOLDEN_DIMS).astype(np.int8), -5)
    other = score.unit(RNG.integers(-128, 128, score.GOLDEN_DIMS).astype(np.int8), -5)
    assert score.best(u, [other, u, u]) == (1000, 1)
    assert score.best(u, [-u]) == (-1000, 0)
    assert score.best(u, []) == (-score.SCORE_LIMIT, 0)


def test_each_person_of_a_household_case_matches_their_own_voiceprint_best() -> None:
    for name, people in (("case_003", 3), ("case_004", 8)):
        case = read_gold(score.GOLDEN_ROOT / score.BLOCK / f"{name}.gold")
        assert case["people"][0] == people and case["voiceprint"].shape == (people, score.GOLDEN_DIMS)
        np.testing.assert_array_equal(case["decision"][: 2 * people, 2], np.repeat(np.arange(people), 2))
        assert (case["decision"][:, 0] == case["threshold"][0]).any()


def test_two_people_enrolled_alike_tie_on_every_test_to_the_first() -> None:
    twins = read_gold(score.GOLDEN_ROOT / score.BLOCK / "case_005.gold")
    single = read_gold(score.GOLDEN_ROOT / score.BLOCK / "case_000.gold")
    np.testing.assert_array_equal(twins["voiceprint"][0], twins["voiceprint"][1])
    assert not twins["decision"][:, 2].any()
    np.testing.assert_array_equal(twins["decision"][:, :2], single["decision"][:, :2])


def test_committed_cases_match_a_fresh_emit(tmp_path: Path) -> None:
    for path in score.emit(tmp_path):
        committed = score.GOLDEN_ROOT / path.relative_to(tmp_path)
        assert committed.read_bytes() == path.read_bytes(), f"{committed} is stale: rerun the emit"


def test_the_owner_case_has_a_test_exactly_on_its_threshold_and_both_decisions() -> None:
    case = read_gold(score.GOLDEN_ROOT / score.BLOCK / "case_000.gold")
    decisions = case["decision"]
    assert (decisions[:, 0] == case["threshold"][0]).any()
    assert set(decisions[:, 1]) == {0, 1}


def test_each_negative_control_differs_from_its_reference_in_its_one_place() -> None:
    right = read_gold(score.GOLDEN_ROOT / score.BLOCK / "case_000.gold")
    raw_print = read_gold(score.GOLDEN_ROOT / score.BLOCK / "case_neg_000.gold")
    strict = read_gold(score.GOLDEN_ROOT / score.BLOCK / "case_neg_001.gold")
    assert np.max(np.abs(raw_print["voiceprint"] - right["voiceprint"])) > 1e-3
    np.testing.assert_array_equal(strict["voiceprint"], right["voiceprint"])
    np.testing.assert_array_equal(strict["decision"][:, 0], right["decision"][:, 0])
    assert (strict["decision"][:, 1] != right["decision"][:, 1]).sum() == 1
    household = read_gold(score.GOLDEN_ROOT / score.BLOCK / "case_003.gold")
    first = read_gold(score.GOLDEN_ROOT / score.BLOCK / "case_neg_002.gold")
    np.testing.assert_array_equal(first["voiceprint"], household["voiceprint"])
    assert (first["decision"][:, 2] != household["decision"][:, 2]).any()
