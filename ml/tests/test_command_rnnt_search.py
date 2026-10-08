"""The rnnt decision: the tree holds every variant and part once, breadth first, depth by depth; each node's score is
the sum over every alignment of the RNN-T lattice, many units a frame included; nodes far behind are dropped without
their contexts being asked; a window scored frame by frame in any chunks decides as at once; the winner is taken when
said whole, slow or fast, and turned down when a part of it or the free path fits the window better."""

from __future__ import annotations

import math

import numpy as np

from srpipe.tasks.command.ctc.postproc import ctc_score
from srpipe.tasks.command.rnnt.postproc import rnnt_search as rs

CLASSES, SIZE = ctc_score.n_classes(), 2
PAD = CLASSES
EVERY_NODE = rs.EVERY_NODE


def toy(rng: np.random.Generator, said: list[list[int]], frames: int, strength: float = 6.0) -> rs.LogProbs:
    """Log-probabilities of a window saying the units of said[t] on frame t, as a joiner that has heard them: the next
    unit of the frame not yet emitted, by the context's last class, is likely, then blank; blanks after said; nudged
    by the context's last class so the predictor matters."""
    base = rng.normal(size=(frames, CLASSES)).astype(np.float32)
    nudge = rng.normal(scale=0.3, size=(CLASSES + 1, CLASSES)).astype(np.float32)

    def one(t: int, context: tuple[int, ...]) -> np.ndarray:
        x = base[t] + nudge[context[-1]]
        targets = said[t] if t < len(said) else []
        done = targets.index(context[-1] - 1) + 1 if context[-1] - 1 in targets else 0
        x[targets[done] + 1 if done < len(targets) else ctc_score.BLANK] += strength
        return (x - np.float32(np.log(np.exp(x.astype(np.float64)).sum()))).astype(np.float32)

    def log_probs(t: int, contexts: list[tuple[int, ...]]) -> np.ndarray:
        return np.stack([one(t, c) for c in contexts])

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
    depths = [len(u) for u in tree.units]
    for d, (lo, hi) in enumerate(zip(tree.levels[:-1], tree.levels[1:], strict=True)):
        assert lo < hi and set(depths[lo:hi]) == {d}
    assert tree.levels[-1] == len(tree.units)


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
        contexts = [rs.context_of(units[:u], SIZE, PAD) for u in range(len(units) + 1)]
        rows = log_probs(t, contexts).astype(np.float64)
        for u in range(1, len(units) + 1):
            alpha[u] = float(np.logaddexp(alpha[u], alpha[u - 1] + rows[u - 1][units[u - 1] + 1]))
        alpha = [a + rows[u][ctc_score.BLANK] for u, a in enumerate(alpha)]
    return alpha[-1]


def test_each_node_scores_the_sum_over_every_alignment_many_units_a_frame() -> None:
    lex = lexicon()
    log_probs = toy(np.random.default_rng(1), [[0, sorted(ctc_score.TONE_UNITS)[0]]], 5, strength=1.0)
    tree = rs.command_tree(lex)
    alpha = rs.forward(log_probs, 5, tree, SIZE, PAD, beam=EVERY_NODE)
    for n, units in enumerate(tree.units):
        assert math.isclose(float(alpha[n]), lattice(log_probs, 5, units), abs_tol=5e-4)
    one = rs.forward(log_probs, 5, tree, SIZE, PAD, chain=False, beam=EVERY_NODE)
    merged = rs.forward(log_probs, 5, tree, SIZE, PAD, add=False, beam=EVERY_NODE)
    assert all(one[n] < alpha[n] for n in tree.variants[0]) and np.all(merged <= alpha + 1e-6)


def test_nodes_far_behind_are_dropped_and_their_contexts_never_asked() -> None:
    whole = [int(u) for u in lexicon()[1][0]]
    log_probs, tree = toy(np.random.default_rng(4), one_a_frame(whole), 12), rs.command_tree(lexicon())
    asked: dict[str, int] = {}

    def counted(name: str):
        def ask(t: int, contexts: list[tuple[int, ...]]) -> np.ndarray:
            asked[name] = asked.get(name, 0) + len(contexts)
            return log_probs(t, contexts)

        return ask

    exact = rs.decide(counted("exact"), 12, tree, ctc_score.CAP, 50, SIZE, PAD, 12, beam=EVERY_NODE)
    pruned = rs.decide(counted("pruned"), 12, tree, ctc_score.CAP, 50, SIZE, PAD, 12)
    assert asked["pruned"] < asked["exact"]
    # The winner, its score and its gap to the free path stay; the lead reads CAP when the runner-up drops.
    assert pruned[0][[0, 1, 3]].tolist() == exact[0][[0, 1, 3]].tolist() and pruned[0][2] == ctc_score.CAP
    assert (pruned[1] == -np.inf).sum() > (exact[1] == -np.inf).sum()


def test_a_window_scored_frame_by_frame_in_any_chunks_decides_as_at_once() -> None:
    whole = [int(u) for u in lexicon()[1][0]]
    log_probs, tree = toy(np.random.default_rng(6), one_a_frame(whole), 12), rs.command_tree(lexicon())
    at_once = rs.decide(log_probs, 12, tree, ctc_score.CAP, 50, SIZE, PAD, 12)
    for cut in (0, 5, 11):
        s = rs.begin(tree, SIZE, PAD)
        for _ in range(cut):
            rs.frame(s, tree, log_probs)
        for _ in range(12 - cut):
            rs.frame(s, tree, log_probs)
        decision, scores = rs.finish(s, tree, ctc_score.CAP, 50, 12)
        assert np.array_equal(decision, at_once[0]) and np.array_equal(scores, at_once[1])


def decided(said: list[list[int]], frames: int, reject: int = ctc_score.CAP, own_parts: bool = True):
    lex = lexicon()
    log_probs = toy(np.random.default_rng(2), said, frames)
    return rs.decide(log_probs, frames, rs.command_tree(lex), reject, 50, SIZE, PAD, max(frames, 1), own_parts)[0]


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
    assert decided([], 0).tolist() == [ctc_score.REJECTED, 0, ctc_score.CAP, ctc_score.CAP, 0]


def test_the_greedy_path_moves_on_through_blank_after_its_last_unit_of_a_frame() -> None:
    log_probs = toy(np.random.default_rng(3), [[7, 8, 9, 10, 11, 12]], 2, strength=40.0)
    units, total = rs.greedy(log_probs, 2, SIZE, PAD)
    assert len(units) <= 2 * rs.FREE_UNITS and np.isfinite(total)


def test_the_golden_set_holds_its_edges_and_each_negative_control_differs(tmp_path) -> None:
    from srpipe.golden.gold import read_gold

    written = rs.emit(tmp_path)
    names = ("case_000", "case_001", "case_002", "case_003", "case_neg_000", "case_neg_001", "case_neg_002")
    names += ("case_neg_003", "case_neg_004")
    assert [p.name for p in written] == [f"{n}.gold" for n in names]
    cases = {p.stem: read_gold(p) for p in written}
    edge = cases["case_002"]["decision"]
    assert edge[0].tolist() == [ctc_score.REJECTED, 0, ctc_score.CAP, ctc_score.CAP, 0]
    assert (cases["case_000"]["decision"][:, 4] == 0).all()
    assert edge[2, 0] == 0 and edge[3, 0] == ctc_score.REJECTED
    assert cases["case_neg_000"]["decision"][3, 0] == 0
    assert not np.array_equal(cases["case_002"]["scores"], cases["case_neg_001"]["scores"])
    assert np.isfinite(cases["case_002"]["scores"][4, 0]) and cases["case_neg_002"]["scores"][4, 0] == -np.inf
    narrow, product = cases["case_neg_003"], cases["case_003"]
    assert product["beam"][0] == narrow["beam"][0] == rs.BEAM_NATS and cases["case_002"]["beam"][0] == np.inf
    assert (narrow["scores"] == -np.inf).sum() > (product["scores"] == -np.inf).sum()
    assert (product["decision"][:, 0] == edge[:, 0]).all()
    assert cases["case_002"]["limits"].shape == (5, 2) and cases["case_002"]["frames"].dtype == np.int8
    assert (cases["case_000"]["decision"][:, 0] != ctc_score.REJECTED).any()
    own, divided = cases["case_neg_004"], cases["case_000"]
    assert (own["per_frames"] == rs.FRAMES).all() and (divided["per_frames"] == rs.FRAMES).all()
    shorter = divided["n_frames"] < rs.FRAMES
    assert shorter.any() and not np.array_equal(divided["scores"][shorter], own["scores"][shorter])
