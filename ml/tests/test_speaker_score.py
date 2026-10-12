"""srpipe.tasks.speaker.postproc.score against float64 cosine scoring, and its golden cases (KEHOACH 3.17)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

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
    plain = (np.float32(0.0), np.float32(1.0))
    assert score.best(u, [other, u, u], [plain] * 3, plain) == (1000, 1, 1000)
    assert score.best(u, [-u], [plain], plain) == (-1000, 0, -1000)
    assert score.best(u, [], [], plain) == (-score.Z_LIMIT, 0, -score.SCORE_LIMIT)


def test_the_top_rows_are_the_highest_scores_a_tie_to_the_earlier_row_in_row_order() -> None:
    scores = [np.float32(x) for x in (1.0, 3.0, 3.0, 2.0)]
    assert score.top_rows(scores, 2) == [1, 2]
    assert score.top_rows(scores, 1) == [1]
    assert score.top_rows(scores, 3) == [1, 2, 3]
    assert score.top_rows([np.float32(0.0), np.float32(-0.0)], 1) == [0]
    assert score.top_rows([np.float32(-0.0), np.float32(0.0)], 1) == [0]


def test_norm_is_the_float64_mean_and_deviation_over_the_top_rows_and_one_without_spread() -> None:
    cohort = np.stack([score.unit(RNG.integers(-128, 128, score.GOLDEN_DIMS).astype(np.int8), -5) for _ in range(40)])
    print_ = score.unit(RNG.integers(-128, 128, score.GOLDEN_DIMS).astype(np.int8), -5)
    dots = cohort.astype(np.float64) @ print_.astype(np.float64)
    for top in (1, 10, 40):
        mean, std = score.norm(print_, cohort, top)
        highest = np.sort(dots)[::-1][:top]
        assert abs(mean - highest.mean()) < 1e-6
        assert abs(std - (highest.std() if top > 1 else 1.0)) < 1e-6
    assert score.norm(print_, cohort[:1], 1)[1] == 1.0
    for top in (0, 41):
        with pytest.raises(ValueError, match="top"):
            score.norm(print_, cohort, top)


def test_an_as_norm_score_is_the_thousandths_of_the_mean_of_both_z_within_int16() -> None:
    d, tight = np.float32(0.5), (np.float32(-1.0), np.float32(1e-6))
    assert score.milli_score(d, (np.float32(0.1), np.float32(0.2)), (np.float32(0.1), np.float32(0.2))) == 2000
    assert score.milli_score(d, (np.float32(0.1), np.float32(0.2)), (np.float32(0.3), np.float32(0.2))) == 1500
    assert score.milli_score(np.float32(1.0), tight, tight) == score.Z_LIMIT
    assert score.milli_score(np.float32(-1.0), (np.float32(1.0), np.float32(1e-6)), tight) == -score.Z_LIMIT


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
    np.testing.assert_array_equal(twins["decision"][:, [0, 1, 3]], single["decision"][:, [0, 1, 3]])


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
    fewer = read_gold(score.GOLDEN_ROOT / score.BLOCK / "case_neg_003.gold")
    np.testing.assert_array_equal(fewer["norms"][:, 0], right["norms"][:, 0])
    assert (fewer["norms"][:, 1] != right["norms"][:, 1]).all()
    cosine = read_gold(score.GOLDEN_ROOT / score.BLOCK / "case_neg_004.gold")
    np.testing.assert_array_equal(cosine["norms"], household["norms"])
    assert (cosine["decision"][:, 2] != household["decision"][:, 2]).any()
    z_only = read_gold(score.GOLDEN_ROOT / score.BLOCK / "case_neg_005.gold")
    np.testing.assert_array_equal(z_only["unit_norms"], household["unit_norms"])
    assert (z_only["decision"][:, 0] != household["decision"][:, 0]).all()
    every = read_gold(score.GOLDEN_ROOT / score.BLOCK / "case_neg_006.gold")
    np.testing.assert_array_equal(every["top"], right["top"])
    assert (every["norms"] != right["norms"]).all() and (every["unit_norms"] != right["unit_norms"]).all()


def test_a_cohort_leaning_towards_a_person_ranks_them_below_their_cosine() -> None:
    household = read_gold(score.GOLDEN_ROOT / score.BLOCK / "case_003.gold")
    assert household["norms"][0, 0] > household["norms"][1:, 0].max()


def test_a_cohort_of_one_row_keeps_a_deviation_of_one() -> None:
    lone = read_gold(score.GOLDEN_ROOT / score.BLOCK / "case_006.gold")
    assert lone["cohort"].shape[0] == 1 and lone["top"][0] == 1
    assert lone["norms"][0, 1] == 1.0 and (lone["unit_norms"][:, 1] == 1.0).all()


def test_the_cases_take_a_quarter_of_the_cohort_and_one_case_every_row() -> None:
    tops = {
        name: read_gold(score.GOLDEN_ROOT / score.BLOCK / f"{name}.gold")["top"][0]
        for name in ("case_000", "case_001", "case_004")
    }
    assert tops == {"case_000": score.GOLDEN_TOP, "case_001": score.COHORT_ROWS, "case_004": 2 * score.GOLDEN_TOP}


EDGES = [np.float32(e) for e in score.NOISE_EDGES]


def test_noise_energies_sum_each_part_exactly_in_int64() -> None:
    hop = score.grid.HOP_SAMPLES
    pcm = np.full(3 * hop, -32768, dtype=np.int16)
    pcm[:hop] = 3
    assert score.noise_energies(pcm, 1) == (9 * hop, hop, 2 * hop * 2**30, 2 * hop)
    assert score.noise_energies(pcm, 0) == (0, 0, 9 * hop + 2 * hop * 2**30, 3 * hop)


def test_a_ratio_on_an_edge_reaches_it_and_one_under_does_not() -> None:
    for k, e in enumerate(EDGES):
        on = int(np.float64(e) * 2**30)
        assert score.noise_bin(2**30, 256, on, 256, EDGES) == k + 1
        assert score.noise_bin(2**30, 256, on - 1, 256, EDGES) == k


def test_a_window_it_cannot_judge_is_quiet() -> None:
    quietest = len(EDGES)
    assert score.noise_bin(0, 0, 10**9, 2560, EDGES) == quietest
    assert score.noise_bin(0, 512, 10**9, 2560, EDGES) == quietest
    assert score.noise_bin(10**9, 512, 0, 0, EDGES) == quietest
    assert score.noise_bin(10**9, 512, 10**3, 2560, [np.float32(0.0)] * 3) == quietest


def test_the_threshold_of_a_bin_is_its_row_and_the_quiet_table_s_in_the_quietest() -> None:
    quiet, noisy = [10, 20], [[1, 2, 3], [4, 5, 6]]
    assert [score.threshold_of(quiet, noisy, 2, b) for b in range(4)] == [4, 5, 6, 20]
    assert score.threshold_of(quiet, noisy, 1, 3) == 10


def test_committed_noise_cases_match_a_fresh_emit(tmp_path: Path) -> None:
    for path in score.emit_noise(tmp_path):
        committed = score.GOLDEN_ROOT / path.relative_to(tmp_path)
        assert committed.read_bytes() == path.read_bytes(), f"{committed} is stale: rerun the emit"


def test_each_noise_negative_control_differs_from_the_reference() -> None:
    block = score.GOLDEN_ROOT / score.NOISE_BLOCK
    right = read_gold(block / "case_000.gold")
    assert set(right["bins"].tolist()) == {0, 1, 2, 3}
    assert set(right["decision"][:, 1].tolist()) == {0, 1}
    assert (read_gold(block / "case_001.gold")["bins"] == 3).all()
    for name in ("case_neg_000", "case_neg_001", "case_neg_002"):
        negative = read_gold(block / f"{name}.gold")
        assert (negative["bins"] != right["bins"]).any(), name
        np.testing.assert_array_equal(negative["decision"], right["decision"])
    quiet = read_gold(block / "case_neg_003.gold")
    np.testing.assert_array_equal(quiet["bins"], right["bins"])
    assert (quiet["decision"][:, 0] != right["decision"][:, 0]).any()
