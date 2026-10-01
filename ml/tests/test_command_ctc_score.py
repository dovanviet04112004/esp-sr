"""The ctc decision: the forward pass equals the sum over every alignment, the best command wins, and a window the free
loop explains better or two commands too close are rejected."""

from __future__ import annotations

import itertools

import numpy as np

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
        score = ctc_score.sequence_score(log_probs, np.array(units, dtype=np.uint8))
        assert np.isclose(score, np.log(total) / 5, atol=1e-5)
    assert ctc_score.sequence_score(log_probs[:, :2], np.array([0, 0, 0], dtype=np.uint8)) == -np.inf


def window(units: list[int], classes: int, frames_each: int = 3) -> np.ndarray:
    """Log-probabilities of a window saying units, each held frames_each frames then a blank."""
    path = [c for u in units for c in [u + 1] * frames_each + [0]]
    probs = np.full((classes, len(path)), 0.02, dtype=np.float32)
    probs[path, np.arange(len(path))] = 1.0
    return np.log(probs / probs.sum(axis=0)).astype(np.float32)


def test_the_said_command_wins_and_the_rest_are_rejected_by_their_rules() -> None:
    lexicon = [[np.array([0, 1], np.uint8)], [np.array([2, 3], np.uint8), np.array([2, 4], np.uint8)]]
    said = ctc_score.decide(window([2, 4], 6), lexicon, reject=500, margin=50)
    assert said[0] == 1 and said[1] > 500 and said[2] >= 50 and said[3] <= 500
    other = ctc_score.decide(window([3, 0], 6), lexicon, reject=100, margin=50)
    assert other[0] == ctc_score.REJECTED and other[3] > 100
    close = [[np.array([0, 1], np.uint8)], [np.array([0, 1], np.uint8)]]
    tie = ctc_score.decide(window([0, 1], 6), close, reject=1000, margin=1)
    assert tie[0] == ctc_score.REJECTED and tie[2] == 0
    short = ctc_score.decide(window([0], 6, 1), [[np.array([0, 1, 2, 3], np.uint8)]], reject=1000, margin=0)
    assert short.tolist() == [ctc_score.REJECTED, 0, ctc_score.CAP, ctc_score.CAP]


def test_a_command_reads_once_per_distinct_dialect_form() -> None:
    forms = ctc_score.variants("bật đèn")
    assert 1 <= len(forms) <= 3 and all(f.dtype == np.uint8 for f in forms)
    assert len({tuple(f.tolist()) for f in forms}) == len(forms)
