"""The ctc decision: the forward pass equals the sum over every alignment, also when alignments drift apart past the
range of float32, the best command wins, a window the free loop explains better, two commands too close or a part of
the winner said alone are rejected, and every score is divided by the frames the decision is told, not the window's."""

from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np

from srpipe.golden.gold import read_gold
from srpipe.tasks.command.ctc.postproc import ctc_score


def collapse(path: tuple[int, ...]) -> list[int]:
    return [c for k, c in enumerate(path) if c != 0 and (k == 0 or c != path[k - 1])]


def log_probs_of(rng: np.random.Generator, classes: int, frames: int) -> np.ndarray:
    x = rng.normal(size=(classes, frames)).astype(np.float32)
    return (x - np.log(np.exp(x).sum(axis=0))).astype(np.float32)


def test_the_forward_pass_sums_every_alignment_of_the_units() -> None:
    log_probs = log_probs_of(np.random.default_rng(0), 4, 5)
    for units in ([0], [0, 1], [1, 1], [2, 0, 2]):
        total = sum(
            np.exp(sum((np.float64(log_probs[c, t]) for t, c in enumerate(path)), np.float64(0.0)))
            for path in itertools.product(range(4), repeat=5)
            if collapse(path) == [u + 1 for u in units]
        )
        score = ctc_score.sequence_score(ctc_score.exp_wide(log_probs), np.array(units, dtype=np.uint8), 5)
        assert np.isclose(score, np.log(total) / 5, atol=1e-6)
    too_short = ctc_score.exp_wide(log_probs[:, :2])
    assert ctc_score.sequence_score(too_short, np.array([0, 0, 0], dtype=np.uint8), 2) == -np.inf


def test_exp_holds_a_few_ulp_and_its_range_ends() -> None:
    x = np.linspace(-9000.0, 30.0, 200001, dtype=np.float32)
    m, e = ctc_score.exp_wide(x)
    assert ((m >= 1.0) & (m < 2.0)).all()
    got = np.log(m.astype(np.float64)) + e * np.log(2.0)
    assert np.abs(got - x.astype(np.float64)).max() < 4 * 2.0**-24
    zeros = ctc_score.exp_wide(np.array([-np.inf, np.nan, -10001.0], dtype=np.float32))
    assert (zeros[0] == 0).all() and (zeros[1] == ctc_score.ZERO_EXP).all()


def reference(log_probs: np.ndarray, units: list[int]) -> float:
    """The forward pass in float64 log space."""
    labels = [0] + [c for u in units for c in (u + 1, 0)]
    alpha = np.full(len(labels), -np.inf)
    alpha[:2] = log_probs[labels[:2], 0]
    for t in range(1, log_probs.shape[1]):
        before = alpha.copy()
        for s, label in enumerate(labels):
            jump = s >= 2 and label != 0 and label != labels[s - 2]
            alpha[s] = np.logaddexp.reduce(before[max(0, s - (2 if jump else 1)) : s + 1]) + log_probs[label, t]
    return float(np.logaddexp(alpha[-1], alpha[-2]) / log_probs.shape[1])


def test_an_alignment_hundreds_of_nats_behind_still_counts_when_it_catches_up() -> None:
    frames = ["a"] * 5 + ["_"] * 5 + ["a"] * 5 + ["_"] * 3
    logits = np.zeros((3, len(frames)), dtype=np.float32)
    for t, said_now in enumerate(frames):
        logits[1 if said_now == "a" else 0, t] = 60.0
    log_probs = log_softmax(logits)
    score = ctc_score.sequence_score(ctc_score.exp_wide(log_probs), np.array([0], dtype=np.uint8), len(frames))
    assert np.isclose(score, reference(log_probs, [0]), rtol=1e-6, atol=0)


def log_softmax(logits: np.ndarray) -> np.ndarray:
    return (logits - np.log(np.exp(logits - logits.max(axis=0)).sum(axis=0)) - logits.max(axis=0)).astype(np.float32)


def window(units: list[int], classes: int, frames_each: int = 3) -> np.ndarray:
    """Log-probabilities of a window saying units, each held frames_each frames then a blank."""
    path = [c for u in units for c in [u + 1] * frames_each + [0]]
    probs = np.full((classes, len(path)), 0.02, dtype=np.float32)
    probs[path, np.arange(len(path))] = 1.0
    return np.log(probs / probs.sum(axis=0)).astype(np.float32)


def decide_own(log_probs: np.ndarray, lexicon: list, reject: int, margin: int, **kw) -> np.ndarray:
    return ctc_score.decide(log_probs, lexicon, reject, margin, log_probs.shape[1], **kw)[0]


def test_the_said_command_wins_and_the_rest_are_rejected_by_their_rules() -> None:
    lexicon = [[np.array([0, 1], np.uint8)], [np.array([2, 3], np.uint8), np.array([2, 4], np.uint8)]]
    said = decide_own(window([2, 4], 6), lexicon, reject=500, margin=50)
    assert said[0] == 1 and said[1] > 500 and said[2] >= 50 and said[3] <= 500
    other = decide_own(window([3, 0], 6), lexicon, reject=100, margin=50)
    assert other[0] == ctc_score.REJECTED and other[3] > 100
    close = [[np.array([0, 1], np.uint8)], [np.array([0, 1], np.uint8)]]
    tie = decide_own(window([0, 1], 6), close, reject=1000, margin=1)
    assert tie[0] == ctc_score.REJECTED and tie[2] == 0
    short = decide_own(window([0], 6, 1), [[np.array([0, 1, 2, 3], np.uint8)]], reject=1000, margin=0)
    assert short.tolist() == [ctc_score.REJECTED, 0, ctc_score.CAP, ctc_score.CAP]


def test_silence_around_a_command_leaves_its_gaps_alone_when_scores_divide_by_the_longest_window() -> None:
    lexicon = [[np.array([0, 1], np.uint8)], [np.array([2, 3], np.uint8)]]
    said = window([0, 1], 6)
    padded = np.concatenate([said, np.repeat(said[:, -1:], 24, axis=1)], axis=1)
    longest = padded.shape[1]
    short = ctc_score.decide(said, lexicon, ctc_score.CAP, 0, longest)[0]
    long = ctc_score.decide(padded, lexicon, ctc_score.CAP, 0, longest)[0]
    assert short[0] == long[0] == 0 and short[3] == long[3]
    assert abs(int(short[2]) - int(long[2])) <= long[2] // 100
    assert decide_own(said, lexicon, ctc_score.CAP, 0)[2] > 3 * long[2]
    assert ctc_score.window_frames(2) == -(-ctc_score.listen.WINDOW_HOPS // 2)


def test_parts_are_the_runs_of_whole_syllables_short_of_the_whole_command() -> None:
    tone = sorted(ctc_score.TONE_UNITS)
    units = np.array([0, 1, tone[0], 2, tone[1], 3, 4, tone[2]], np.uint8)
    assert [u.tolist() for u in ctc_score.syllables(units)] == [[0, 1, tone[0]], [2, tone[1]], [3, 4, tone[2]]]
    runs = [u.tolist() for u in ctc_score.parts([units])]
    assert runs == [
        [0, 1, tone[0]],
        [0, 1, tone[0], 2, tone[1]],
        [2, tone[1]],
        [2, tone[1], 3, 4, tone[2]],
        [3, 4, tone[2]],
    ]
    assert ctc_score.parts([units[:3]]) == []
    chup_anh = ctc_score.variants("chụp ảnh")
    assert {tuple(u.tolist()) for u in ctc_score.parts(chup_anh)} == {
        tuple(u.tolist()) for u in ctc_score.variants("chụp") + ctc_score.variants("ảnh")
    }


def test_a_part_of_the_winner_said_alone_is_rejected_and_the_whole_command_taken() -> None:
    tone = sorted(ctc_score.TONE_UNITS)
    whole = np.array([0, 1, tone[0], 2, tone[1]], np.uint8)
    lexicon = [[whole], [np.array([3, tone[2]], np.uint8)]]
    classes = ctc_score.n_classes()
    taken = decide_own(window(whole.tolist(), classes), lexicon, reject=ctc_score.CAP, margin=50)
    assert taken[0] == 0 and taken[2] >= 50
    for alone in ([0, 1, tone[0]], [2, tone[1]]):
        part = decide_own(window(alone, classes), lexicon, reject=ctc_score.CAP, margin=50)
        assert part[0] == ctc_score.REJECTED and part[2] >= 50
        blind = decide_own(window(alone, classes), lexicon, ctc_score.CAP, 50, own_parts=False)
        assert blind[0] == 0


def test_a_command_reads_once_per_distinct_dialect_form() -> None:
    forms = ctc_score.variants("bật đèn")
    assert 1 <= len(forms) <= 3 and all(f.dtype == np.uint8 for f in forms)
    assert len({tuple(f.tolist()) for f in forms}) == len(forms)


def test_the_golden_set_holds_its_edges_and_its_negative_control_differs(tmp_path: Path) -> None:
    written = ctc_score.emit(tmp_path)
    names = ["case_000", "case_001", "case_002", "case_003"] + [f"case_neg_00{k}" for k in range(4)]
    names = [f"{name}.gold" for name in names]
    assert [p.name for p in written] == names
    cases = {p.stem: read_gold(p) for p in written}
    edge = cases["case_002"]["decision"]
    assert edge[0, 0] == 0 and edge[2, 0] == ctc_score.REJECTED and edge[2, 2] == 0
    assert edge[3].tolist() == [ctc_score.REJECTED, 0, ctc_score.CAP, ctc_score.CAP]
    assert edge[4, 0] == 0 and edge[4, 3] == cases["case_002"]["thresholds"][4, 0]
    assert edge[5, 0] == 0 and edge[5, 2] == cases["case_002"]["thresholds"][5, 1]
    assert (cases["case_000"]["decision"][:, 0] != ctc_score.REJECTED).any()
    assert not np.array_equal(cases["case_000"]["scores"], cases["case_neg_000"]["scores"])
    assert np.array_equal(cases["case_000"]["log_probs"], cases["case_neg_000"]["log_probs"])
    assert not np.array_equal(cases["case_000"]["log_probs"], cases["case_neg_001"]["log_probs"])
    partial, blind = cases["case_003"]["decision"], cases["case_neg_002"]["decision"]
    assert len(partial) % 3 == 0 and (partial[0::3, 0] == ctc_score.REJECTED).all()
    assert (partial[1::3, 0] == ctc_score.REJECTED).all() and (partial[2::3, 0] != ctc_score.REJECTED).all()
    assert (blind[0::3, 0] != ctc_score.REJECTED).any() and np.array_equal(partial[2::3], blind[2::3])
    own = cases["case_neg_003"]
    assert (own["per_frames"] == ctc_score.FRAMES).all() and (cases["case_000"]["per_frames"] == ctc_score.FRAMES).all()
    shorter = cases["case_000"]["frames"] < ctc_score.FRAMES
    assert shorter.any() and not np.array_equal(cases["case_000"]["scores"][shorter], own["scores"][shorter])


def test_frame_log_probs_are_a_softmax_of_the_int8_logits_and_the_largest_comes_off_first() -> None:
    rng = np.random.default_rng(5)
    q = rng.integers(-128, 128, size=(45, 30)).astype(np.int8)
    for exponent in ctc_score.LOGIT_EXPONENTS:
        x = np.ldexp(q.astype(np.float64), exponent)
        want = x - x.max(axis=0) - np.log(np.exp(x - x.max(axis=0)).sum(axis=0))
        got = ctc_score.frame_log_probs(q, exponent)
        assert got.dtype == np.float32 and np.abs(got - want).max() < 1e-5
        assert not np.array_equal(got, ctc_score.frame_log_probs(q, exponent, center=False))
    on_grid = ctc_score.quantised(np.array([[0.06], [-40.0], [3.0]], np.float32), -3)
    assert on_grid.ravel().tolist() == [0, -128, 24]
