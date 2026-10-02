"""The rnnt decision that ai_engine/src/command_rnnt/ mirrors (KEHOACH 3.12, ADR-0016): every variant and part of every
command scored exactly, the RNN-T forward over a prefix tree of them with as many units a frame as the lattice takes,
then the ctc track's rules: the greedy path ahead by more than reject, another command within margin, or a part of the
winner no lower than it, in thousandths of a nat a frame. Every score is float32 in the order the C takes; two paths
into one node add in probability through ctc_score's exp and a log in double rounded to float. emit writes
contracts/golden/command_rnnt/. Run: python -m srpipe.tasks.command.rnnt.postproc.rnnt_search [--out <golden root>]
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from srpipe.golden.gold import write_gold
from srpipe.tasks.command.ctc.postproc import ctc_score
from srpipe.tasks.command.ctc.postproc.ctc_score import BLANK, CAP, REJECTED, milli

BLOCK = "command_rnnt"
SEED = 20261003
CONTEXT = 2  # the predictor context the golden tables are drawn for
WINDOWS, FRAMES = 12, 40  # a case's windows and their longest, in frames
FREE_UNITS = 4  # the greedy path's most units a frame, then blank
JUST_SAID_BLANK, JUST_SAID_AGAIN = 20, 60  # golden pulls after a unit: blank up, it down

LogProbs = Callable[[int, tuple[int, ...]], np.ndarray]  # frame, predictor context → (classes,) float32


@dataclass(frozen=True)
class Tree:
    """Every variant and part of every command as a prefix tree, the root node 0 and the rest numbered breadth first
    with each node's children in unit order: units[n] the units into node n, children[n] its (unit, child) pairs;
    variants[c] and parts[c] the nodes command c's variants and parts end on."""

    units: list[tuple[int, ...]]
    children: list[list[tuple[int, int]]]
    variants: list[list[int]]
    parts: list[list[int]]


def command_tree(lexicon: list[list[np.ndarray]]) -> Tree:
    """The prefix tree of every variant of every command and every part of them (ctc_score.parts), unminimised: the
    predictor reads the last classes of a node's own units, so two prefixes never share a node."""
    sequences = [[tuple(int(u) for u in s) for s in (*forms, *ctc_score.parts(forms))] for forms in lexicon]
    nested: dict = {}
    for said in sequences:
        for units in said:
            node = nested
            for u in units:
                node = node.setdefault(u, {})
    units, children, order = [()], [], [nested]
    for k, node in enumerate(order):
        kids = []
        for u in sorted(node):
            units.append((*units[k], u))
            order.append(node[u])
            kids.append((u, len(units) - 1))
        children.append(kids)
    index = {u: n for n, u in enumerate(units)}
    n_variants = [len(forms) for forms in lexicon]
    return Tree(
        units,
        children,
        [[index[u] for u in said[:n]] for said, n in zip(sequences, n_variants, strict=True)],
        [[index[u] for u in said[n:]] for said, n in zip(sequences, n_variants, strict=True)],
    )


def context_of(units: tuple[int, ...], size: int, pad: int) -> tuple[int, ...]:
    """The predictor's last size classes of a node: pad, then blank, then each unit's class."""
    classes = (pad,) * size + (BLANK,) + tuple(u + 1 for u in units)
    return classes[-size:]


def log_add(a: np.float32, b: np.float32) -> np.float32:
    """log(e^a + e^b) in float32: the larger plus log(1 + e^(smaller - larger)), e^ by ctc_score's exp, the log in
    double rounded to float."""
    top, low = (a, b) if a >= b else (b, a)
    m, e = ctc_score.exp_wide(np.array([low - top], dtype=np.float32))
    tail = np.float32(np.ldexp(np.float64(m[0]), int(e[0])))
    return np.float32(top + np.float32(math.log(1.0 + float(tail))))


def forward(
    log_probs: LogProbs, frames: int, tree: Tree, size: int, pad: int, add: bool = True, chain: bool = True
) -> np.ndarray:
    """log P of each node's units over every alignment of the RNN-T lattice, the last frame's blank included. Each
    frame the log-probabilities are taken once a distinct context, in the order nodes first need them; nodes in their
    order send their probability to their children, so a child passes on what it got in the same frame; then every
    node takes blank. add off keeps the larger of two paths into a node, chain off lets a frame emit one unit only:
    negative controls of the golden set."""
    contexts = [context_of(u, size, pad) for u in tree.units]
    distinct = list(dict.fromkeys(contexts))
    which = [distinct.index(c) for c in contexts]
    alpha = np.full(len(tree.units), -np.inf, dtype=np.float32)
    alpha[0] = 0.0
    for t in range(frames):
        rows = [log_probs(t, c) for c in distinct]
        before = alpha.copy()
        for n, kids in enumerate(tree.children):
            source = alpha[n] if chain else before[n]
            if source == -np.inf:
                continue
            row = rows[which[n]]
            for u, child in kids:
                score = np.float32(source + row[u + 1])
                alpha[child] = log_add(alpha[child], score) if add else max(alpha[child], score)
        for n in range(len(alpha)):
            alpha[n] = np.float32(alpha[n] + rows[which[n]][BLANK])
    return alpha


def greedy(log_probs: LogProbs, frames: int, size: int, pad: int) -> tuple[tuple[int, ...], np.float32]:
    """The free path on the same lattice: each frame the likeliest class for the units so far, a unit appended and the
    frame kept, a blank or FREE_UNITS units moving it on through blank; its units and its summed log-prob."""
    units: tuple[int, ...] = ()
    total = np.float32(0.0)
    for t in range(frames):
        for emitted in range(FREE_UNITS + 1):
            lp = log_probs(t, context_of(units, size, pad))
            best = int(np.argmax(lp))
            if best == BLANK or emitted == FREE_UNITS:
                total = np.float32(total + lp[BLANK])
                break
            total = np.float32(total + lp[best])
            units = (*units, best - 1)
    return units, total


def decide(
    log_probs: LogProbs,
    frames: int,
    tree: Tree,
    reject: int,
    margin: int,
    size: int,
    pad: int,
    own_parts: bool = True,
    add: bool = True,
    chain: bool = True,
):
    """The decision of one window, int32 in the order of ctc_score.DECISION, and each command's best variant score a
    frame (-inf with no frame). The winner is the first of the best; own_parts off ignores its parts, add and chain
    off are forward's negative controls."""
    n_commands = len(tree.variants)
    scores = np.full(n_commands, -np.inf, dtype=np.float32)
    part = np.full(n_commands, -np.inf, dtype=np.float32)
    if frames > 0:
        alpha = forward(log_probs, frames, tree, size, pad, add, chain)
        for c in range(n_commands):
            for n in tree.variants[c]:
                scores[c] = max(scores[c], np.float32(alpha[n] / np.float32(frames)))
            for n in tree.parts[c]:
                part[c] = max(part[c], np.float32(alpha[n] / np.float32(frames)))
    best = int(np.argmax(scores))
    if not scores[best] > -np.inf:
        return np.array([REJECTED, 0, CAP, CAP], dtype=np.int32), scores
    second = max((s for k, s in enumerate(scores) if k != best), default=np.float32(-np.inf))
    free = np.float32(greedy(log_probs, frames, size, pad)[1] / np.float32(frames))
    gap = milli(np.float32(free - scores[best]))
    lead = milli(np.float32(scores[best] - second)) if second > -np.inf else CAP
    whole = not own_parts or bool(part[best] < scores[best])
    accepted = gap <= reject and lead >= margin and whole
    score = milli(ctc_score.exp32(scores[best]))
    return np.array([best if accepted else REJECTED, score, lead, gap], dtype=np.int32), scores


def table_log_probs(frames: np.ndarray, first: np.ndarray, last: np.ndarray, exponent: int) -> LogProbs:
    """The golden set's stand-in for the joiner: the int8 logits of frame t for a context (c1, c2) are frames[t] +
    first[c1] + last[c2], summed in int32 and held to int8, then log-probabilities by ctc_score.frame_log_probs."""

    def log_probs(t: int, context: tuple[int, ...]) -> np.ndarray:
        q = frames[t].astype(np.int32) + first[context[0]] + last[context[1]]
        return ctc_score.frame_log_probs(np.clip(q, -128, 127).astype(np.int8)[:, None], exponent)[:, 0]

    return log_probs


def said(rng: np.random.Generator, units: np.ndarray, frames: int, exponent: int, fast: bool = False) -> np.ndarray:
    """int8 logits (frames, classes) of a window saying units, each on its own frame with a blank frame after it; fast,
    two of them a frame on frames in a row instead; blanks to the end."""
    logits = rng.standard_normal((frames, ctc_score.n_classes())).astype(np.float32)
    for k, u in enumerate(units):
        logits[k // 2 if fast else 2 * k, int(u) + 1] += 6.0
    if not fast:
        logits[1 : 2 * len(units) : 2, BLANK] += 6.0
    logits[-(-len(units) // 2) if fast else 2 * len(units) :, BLANK] += 6.0
    return ctc_score.quantised(logits, exponent)


def case(
    windows: list[tuple[np.ndarray, int]],
    lexicon: list[list[np.ndarray]],
    tables: tuple[np.ndarray, np.ndarray],
    limits: np.ndarray,
    own_parts: bool = True,
    add: bool = True,
    chain: bool = True,
) -> dict[str, np.ndarray]:
    """One golden case: int8 frames (frames, classes) and their exponents padded to FRAMES, the lexicon padded, the
    context tables, each window's reject and margin, and the command scores and decision of each."""
    longest = max(len(u) for forms in lexicon for u in forms)
    most = max(len(forms) for forms in lexicon)
    units = np.zeros((len(lexicon), most, longest), dtype=np.int32)
    n_units = np.zeros((len(lexicon), most), dtype=np.int32)
    for c, forms in enumerate(lexicon):
        for v, u in enumerate(forms):
            units[c, v, : len(u)], n_units[c, v] = u, len(u)
    frames = np.zeros((len(windows), FRAMES, ctc_score.n_classes()), dtype=np.int8)
    tree, pad = command_tree(lexicon), ctc_score.n_classes()
    decisions, scores = [], []
    for w, ((q, exponent), (reject, margin)) in enumerate(zip(windows, limits, strict=True)):
        frames[w, : len(q)] = q
        lp = table_log_probs(q, *tables, exponent)
        decision, row = decide(lp, len(q), tree, reject, margin, CONTEXT, pad, own_parts, add, chain)
        decisions.append(decision)
        scores.append(row)
    return {
        "frames": frames,
        "n_frames": np.array([len(q) for q, _ in windows], dtype=np.int32),
        "exponent": np.array([e for _, e in windows], dtype=np.int32),
        "first": tables[0],
        "last": tables[1],
        "units": units,
        "n_variants": np.array([len(forms) for forms in lexicon], dtype=np.int32),
        "n_units": n_units,
        "limits": np.asarray(limits, dtype=np.int32),
        "scores": np.stack(scores).astype(np.float32),
        "decision": np.stack(decisions),
    }


def context_tables(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Small int8 pulls of the two context classes, the pad id's row included, so the context changes the logits; a
    unit just emitted makes blank likelier and itself less, as a joiner that has heard it, so skipping a unit costs."""
    shape = (ctc_score.n_classes() + 1, ctc_score.n_classes())
    first, last = (np.clip(np.rint(rng.normal(0, 3, shape)), -16, 16).astype(np.int16) for _ in range(2))
    for c in range(1, ctc_score.n_classes()):
        last[c, BLANK] += JUST_SAID_BLANK
        last[c, c] -= JUST_SAID_AGAIN
    return first.astype(np.int8), np.clip(last, -128, 127).astype(np.int8)


def random_windows(rng: np.random.Generator, lexicon: list[list[np.ndarray]]) -> tuple[list, np.ndarray]:
    """Windows of every length, a third saying a whole command, slow or fast, a third a part of one, a third noise;
    reject and margin across their range."""
    windows = []
    for k in range(WINDOWS):
        frames, exponent = int(rng.integers(8, FRAMES + 1)), int(rng.choice(ctc_score.LOGIT_EXPONENTS))
        forms = lexicon[int(rng.integers(len(lexicon)))]
        whole = forms[int(rng.integers(len(forms)))]
        pieces = ctc_score.parts(forms)
        units = whole if k % 3 == 0 else (pieces[int(rng.integers(len(pieces)))] if pieces and k % 3 == 1 else None)
        fast = bool(rng.integers(2))
        if units is not None and (len(units) if fast else 2 * len(units)) <= frames:
            windows.append((said(rng, units, frames, exponent, fast), exponent))
        else:
            noise = rng.standard_normal((frames, ctc_score.n_classes())) * rng.uniform(0.5, 6.0)
            windows.append((ctc_score.quantised(noise.astype(np.float32), exponent), exponent))
    return windows, np.stack([rng.integers(0, 3001, WINDOWS), rng.integers(0, 501, WINDOWS)], 1)


def edge_windows(rng: np.random.Generator, lexicon: list[list[np.ndarray]]) -> tuple[list, np.ndarray]:
    """No frame at all, a window of one frame, the first command whole, its first syllable alone, and the whole again
    two units a frame in fewer frames than it has units, every threshold open."""
    e = ctc_score.LOGIT_EXPONENTS[1]
    whole = lexicon[0][0]
    first = ctc_score.syllables(whole)[0]
    windows = [
        (np.zeros((0, ctc_score.n_classes()), np.int8), e),
        (said(rng, whole[:0], 1, e), e),
        (said(rng, whole, FRAMES, e), e),
        (said(rng, first, FRAMES, e), e),
        (said(rng, whole, -(-len(whole) // 2) + 1, e, fast=True), e),
    ]
    return windows, np.array([[CAP, 0]] * len(windows), dtype=np.int32)


def emit(root: Path) -> list[Path]:
    """Random windows over the default commands and over a small set, the edge windows, and three negative controls on
    the edges: a winner whose parts are ignored, paths into a node merged by the larger, one unit a frame."""
    rng = np.random.default_rng(SEED)
    lexicon = ctc_score.default_lexicon()
    tables = context_tables(rng)
    edges, edge_limits = edge_windows(rng, lexicon)
    windows, limits = random_windows(rng, lexicon)
    small, small_limits = random_windows(rng, lexicon[:3])
    cases = {
        "case_000": case(windows, lexicon, tables, limits),
        "case_001": case(small, lexicon[:3], tables, small_limits),
        "case_002": case(edges, lexicon, tables, edge_limits),
        "case_neg_000": case(edges, lexicon, tables, edge_limits, own_parts=False),
        "case_neg_001": case(edges, lexicon, tables, edge_limits, add=False),
        "case_neg_002": case(edges, lexicon, tables, edge_limits, chain=False),
    }
    written = []
    for name, tensors in cases.items():
        path = root / BLOCK / f"{name}.gold"
        write_gold(path, tensors)
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ctc_score.GOLDEN_ROOT)
    for path in emit(parser.parse_args().out):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
