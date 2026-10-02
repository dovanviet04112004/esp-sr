"""The rnnt decision: the tree holds every variant and part once, breadth first; each node's score is the sum over every
alignment of the RNN-T lattice, many units a frame included; the winner is taken when said whole, slow or fast, and
turned down when a part of it or the free path fits the window better."""

from __future__ import annotations

import math

import numpy as np

from srpipe.tasks.command.ctc.postproc import ctc_score
from srpipe.tasks.command.rnnt.postproc import rnnt_search as rs

CLASSES, SIZE = ctc_score.n_classes(), 2
PAD = CLASSES


def toy(rng: np.random.Generator, said: list[list[int]], frames: int, strength: float = 6.0) -> rs.LogProbs:
    """Log-probabilities of a window saying the units of said[t] on frame t, as a joiner that has heard them: the next
    unit of the frame not yet emitted, by the context's last class, is likely, then blank; blanks after said; nudged
    by the context's last class so the predictor matters."""
    base = rng.normal(size=(frames, CLASSES)).astype(np.float32)
    nudge = rng.normal(scale=0.3, size=(CLASSES + 1, CLASSES)).astype(np.float32)

    def log_probs(t: int, context: tuple[int, ...]) -> np.ndarray:
        x = base[t] + nudge[context[-1]]
        targets = said[t] if t < len(said) else []
        done = targets.index(context[-1] - 1) + 1 if context[-1] - 1 in targets else 0
        x[targets[done] + 1 if done < len(targets) else ctc_score.BLANK] += strength
        return (x - np.float32(np.log(np.exp(x.astype(np.float64)).sum()))).astype(np.float32)

    return log_probs


def one_a_frame(units: list[int]) -> list[list[int]]:
    return [[u] for u in units]


def lexicon() -> list[list[np.ndarray]]:
    """Two commands of two syllables sharing none, and one of one syllable; tones are the ctc_score tone units."""
    t1, t2 = sorted(ctc_score.TONE_UNITS)[:2]
    return [
        [np.array([0, t1, 1, t2], np.uint8)],
        [np.array([2, t1, 3, t1], np.uint8)],
        [np.array([4, t2], np.uint8)],
    ]


def test_the_tree_holds_every_variant_and_part_once_breadth_first() -> None:
    t1 = sorted(ctc_score.TONE_UNITS)[0]
    lex = [*lexicon(), [np.array([5, t1, 3, t1], np.uint8)]]
    tree = rs.command_tree(lex)
    wanted = {tuple(int(u) for u in s) for forms in lex for s in (*forms, *ctc_score.parts(forms))}
    prefixes = {s[:k] for s in wanted for k in range(len(s) + 1)}
    assert set(tree.units) == prefixes and len(tree.units) == len(prefixes)
    assert [len(u) for u in tree.units] == sorted(len(u) for u in tree.units)
    for n, kids in enumerate(tree.children):
        assert [u for u, _ in kids] == sorted(u for u, _ in kids)
        assert all(child > n and tree.units[child] == (*tree.units[n], u) for u, child in kids)
    for c, forms in enumerate(lex):
        assert [tree.units[n] for n in tree.variants[c]] == [tuple(int(u) for u in f) for f in forms]
        assert [tree.units[n] for n in tree.parts[c]] == [tuple(int(u) for u in p) for p in ctc_score.parts(forms)]
    assert rs.context_of((), SIZE, PAD) == (PAD, ctc_score.BLANK) and rs.context_of((3, 5), SIZE, PAD) == (4, 6)


def test_log_add_is_the_log_of_the_summed_probabilities() -> None:
    rng = np.random.default_rng(0)
    for a, b in rng.normal(scale=30.0, size=(2000, 2)).astype(np.float32):
        want = np.logaddexp(np.float64(a), np.float64(b))
        assert abs(rs.log_add(a, b) - want) <= 4 * abs(np.spacing(np.float32(want)))
        assert rs.log_add(a, b) == rs.log_add(b, a)
    assert rs.log_add(np.float32(-3.0), np.float32(-np.inf)) == np.float32(-3.0)


def lattice(log_probs: rs.LogProbs, frames: int, units: tuple[int, ...]) -> float:
    """log P(units) over every RNN-T alignment, any units a frame and a blank closing each frame, in float64."""
    alpha = [0.0] + [-math.inf] * len(units)
    for t in range(frames):
        rows = [log_probs(t, rs.context_of(units[:u], SIZE, PAD)).astype(np.float64) for u in range(len(units) + 1)]
        for u in range(1, len(units) + 1):
            alpha[u] = float(np.logaddexp(alpha[u], alpha[u - 1] + rows[u - 1][units[u - 1] + 1]))
        alpha = [a + rows[u][ctc_score.BLANK] for u, a in enumerate(alpha)]
    return alpha[-1]


def test_each_node_scores_the_sum_over_every_alignment_many_units_a_frame() -> None:
    lex = lexicon()
    log_probs = toy(np.random.default_rng(1), [[0, sorted(ctc_score.TONE_UNITS)[0]]], 5, strength=1.0)
    tree = rs.command_tree(lex)
    alpha = rs.forward(log_probs, 5, tree, SIZE, PAD)
    for n, units in enumerate(tree.units):
        assert math.isclose(float(alpha[n]), lattice(log_probs, 5, units), abs_tol=5e-4)
    one = rs.forward(log_probs, 5, tree, SIZE, PAD, chain=False)
    merged = rs.forward(log_probs, 5, tree, SIZE, PAD, add=False)
    assert all(one[n] < alpha[n] for n in tree.variants[0]) and np.all(merged <= alpha + 1e-6)


def decided(said: list[list[int]], frames: int, reject: int = ctc_score.CAP, own_parts: bool = True):
    lex = lexicon()
    log_probs = toy(np.random.default_rng(2), said, frames)
    return rs.decide(log_probs, frames, rs.command_tree(lex), reject, 50, SIZE, PAD, own_parts)[0]


def test_a_command_said_whole_is_taken_slow_or_fast_and_a_part_of_it_turned_down() -> None:
    whole = [int(u) for u in lexicon()[1][0]]
    taken = decided(one_a_frame(whole), 10)
    assert taken[0] == 1 and taken[2] >= 50
    unique = [int(u) for u in lexicon()[0][0]]
    assert decided([unique[:2], unique[2:]], 3)[0] == 0
    part = decided(one_a_frame(whole[:2]), 10)
    assert part[0] == ctc_score.REJECTED and part[2] >= 50
    assert decided(one_a_frame(whole[:2]), 10, own_parts=False)[0] == 1


def test_the_free_path_ahead_or_no_frame_turns_the_window_down() -> None:
    off = decided(one_a_frame([5, 6, 5, 6, 5, 6]), 8, reject=100)
    assert off[0] == ctc_score.REJECTED and off[3] > 100
    assert decided([], 0).tolist() == [ctc_score.REJECTED, 0, ctc_score.CAP, ctc_score.CAP]


def test_the_greedy_path_moves_on_through_blank_after_its_last_unit_of_a_frame() -> None:
    log_probs = toy(np.random.default_rng(3), [[7, 8, 9, 10, 11, 12]], 2, strength=40.0)
    units, total = rs.greedy(log_probs, 2, SIZE, PAD)
    assert len(units) <= 2 * rs.FREE_UNITS and np.isfinite(total)


def test_the_golden_set_holds_its_edges_and_each_negative_control_differs(tmp_path) -> None:
    from srpipe.golden.gold import read_gold

    written = rs.emit(tmp_path)
    names = ("case_000", "case_001", "case_002", "case_neg_000", "case_neg_001", "case_neg_002")
    assert [p.name for p in written] == [f"{n}.gold" for n in names]
    cases = {p.stem: read_gold(p) for p in written}
    edge = cases["case_002"]["decision"]
    assert edge[0].tolist() == [ctc_score.REJECTED, 0, ctc_score.CAP, ctc_score.CAP]
    assert edge[2, 0] == 0 and edge[3, 0] == ctc_score.REJECTED
    assert cases["case_neg_000"]["decision"][3, 0] == 0
    assert not np.array_equal(cases["case_002"]["scores"], cases["case_neg_001"]["scores"])
    assert np.isfinite(cases["case_002"]["scores"][4, 0]) and cases["case_neg_002"]["scores"][4, 0] == -np.inf
    assert cases["case_002"]["limits"].shape == (5, 2) and cases["case_002"]["frames"].dtype == np.int8
    assert (cases["case_000"]["decision"][:, 0] != ctc_score.REJECTED).any()
