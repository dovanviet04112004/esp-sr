"""The rnnt decision: the FST accepts every variant and part and nothing else and is minimal, a wide enough beam scores
each unit sequence as the sum over every one-symbol-a-frame path, and the winner is taken when said whole, turned down
when a part of it, the free path or no command fits the window better."""

from __future__ import annotations

import itertools
import math

import numpy as np

from srpipe.tasks.command.ctc.postproc import ctc_score
from srpipe.tasks.command.rnnt.postproc import rnnt_search as rs

CLASSES, SIZE = ctc_score.n_classes(), 2
PAD = CLASSES


def toy(rng: np.random.Generator, said: list[int], frames: int, strength: float = 6.0) -> rs.LogProbs:
    """Log-probabilities of a window saying said one unit a frame from the start, blanks after, nudged by the
    context's last class so the predictor matters."""
    base = rng.normal(size=(frames, CLASSES)).astype(np.float32)
    for t in range(frames):
        base[t, said[t] + 1 if t < len(said) else ctc_score.BLANK] += strength
    nudge = rng.normal(scale=0.3, size=(CLASSES + 1, CLASSES)).astype(np.float32)

    def log_probs(t: int, context: tuple[int, ...]) -> np.ndarray:
        x = base[t] + nudge[context[-1]]
        return (x - np.float32(np.log(np.exp(x.astype(np.float64)).sum()))).astype(np.float32)

    return log_probs


def lexicon() -> list[list[np.ndarray]]:
    """Two commands of two syllables sharing none, and one of one syllable; tones are the ctc_score tone units."""
    t1, t2 = sorted(ctc_score.TONE_UNITS)[:2]
    return [
        [np.array([0, t1, 1, t2], np.uint8)],
        [np.array([2, t1, 3, t1], np.uint8)],
        [np.array([4, t2], np.uint8)],
    ]


def walked(fst: rs.Fst) -> set[tuple[int, ...]]:
    """Every sequence the acceptor ends on, by a walk over its arcs."""
    found, stack = set(), [(0, ())]
    while stack:
        state, units = stack.pop()
        if fst.final[state]:
            found.add(units)
        stack += [(after, (*units, u)) for u, after in fst.arcs[state].items()]
    return found


def test_the_fst_accepts_every_variant_and_part_and_nothing_else_and_is_minimal() -> None:
    t1 = sorted(ctc_score.TONE_UNITS)[0]
    lex = [*lexicon(), [np.array([5, t1, 3, t1], np.uint8)]]  # shares its last syllable with the second command
    fst = rs.command_fst(lex)
    assert walked(fst) == set(fst.ends)
    for c, forms in enumerate(lex):
        assert all((c, False) in fst.ends[tuple(int(u) for u in f)] for f in forms)
        assert all((c, True) in fst.ends[tuple(int(u) for u in p)] for p in ctc_score.parts(forms))
    alike = [(fst.final[s], tuple(sorted(fst.arcs[s].items()))) for s in range(len(fst.arcs))]
    prefixes = {units[:k] for units in fst.ends for k in range(len(units) + 1)}
    assert len(set(alike)) == len(alike) and len(fst.arcs) < len(prefixes)
    assert rs.context_of((), SIZE, PAD) == (PAD, ctc_score.BLANK) and rs.context_of((3, 5), SIZE, PAD) == (4, 6)


def test_log_add_is_the_log_of_the_summed_probabilities() -> None:
    rng = np.random.default_rng(0)
    for a, b in rng.normal(scale=30.0, size=(2000, 2)).astype(np.float32):
        want = np.logaddexp(np.float64(a), np.float64(b))
        assert abs(rs.log_add(a, b) - want) <= 4 * abs(np.spacing(np.float32(want)))
        assert rs.log_add(a, b) == rs.log_add(b, a)
    assert rs.log_add(np.float32(-3.0), np.float32(-np.inf)) == np.float32(-3.0)


def every_path(log_probs: rs.LogProbs, frames: int, units: tuple[int, ...]) -> float:
    """log P(units) over every path taking one symbol a frame, blank or the next unit, in float64."""
    total = []
    for at in itertools.combinations(range(frames), len(units)):
        path, said = 0.0, ()
        for t in range(frames):
            lp = log_probs(t, rs.context_of(said, SIZE, PAD))
            if t in at:
                path += float(lp[units[len(said)] + 1])
                said = (*said, units[len(said)])
            else:
                path += float(lp[ctc_score.BLANK])
        total.append(path)
    return float(np.logaddexp.reduce(total))


def test_a_wide_beam_scores_each_sequence_as_the_sum_over_its_paths() -> None:
    lex = lexicon()
    log_probs = toy(np.random.default_rng(1), list(lex[0][0]), 6, strength=1.0)
    hyps = rs.search(log_probs, 6, rs.command_fst(lex), 10_000, SIZE, PAD)
    assert len(hyps) >= 10
    for h in hyps:
        assert math.isclose(float(h.score), every_path(log_probs, 6, h.units), abs_tol=2e-4)


def decided(said: list[int], frames: int, reject: int = ctc_score.CAP, own_parts: bool = True, beam: int = 4):
    lex = lexicon()
    log_probs = toy(np.random.default_rng(2), said, frames)
    fst = rs.command_fst(lex)
    return rs.decide(log_probs, frames, fst, len(lex), reject, 50, beam, SIZE, PAD, own_parts)[0]


def test_a_command_said_whole_is_taken_and_a_part_of_it_turned_down() -> None:
    whole = list(lexicon()[1][0])
    taken = decided(whole, 10)
    assert taken[0] == 1 and taken[2] >= 50
    part = decided(whole[:2], 10, beam=16)
    assert part[0] == ctc_score.REJECTED and part[2] >= 50
    assert decided(whole[:2], 10, own_parts=False, beam=16)[0] == 1


def test_the_free_path_ahead_or_no_command_reached_turns_the_window_down() -> None:
    said = [5, 6, 5, 6, 5, 6]
    off = decided(said, 8, reject=100)
    assert off[0] == ctc_score.REJECTED and off[3] > 100
    assert decided([], 0).tolist() == [ctc_score.REJECTED, 0, ctc_score.CAP, ctc_score.CAP]
