"""The rnnt decision that ai_engine/src/command_rnnt/ mirrors (KEHOACH 3.12, ADR-0016): a modified beam search, one
symbol a frame, over a tree of every command's lang_vi variants and of their parts, then the rules of the ctc track:
the greedy path ahead by more than reject, another command within margin, or a part of the winner no lower than it,
both in thousandths of a nat a frame. Every score is float32 in the order the C takes; two hypotheses of the same units
add in probability through ctc_score's exp and a log in double rounded to float.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from srpipe.tasks.command.ctc.postproc import ctc_score
from srpipe.tasks.command.ctc.postproc.ctc_score import BLANK, CAP, REJECTED, milli

LogProbs = Callable[[int, tuple[int, ...]], np.ndarray]  # frame, predictor context → (classes,) float32


@dataclass
class Tree:
    """A prefix tree of units: node 0 the root; children[node] maps a unit to its node; ends[node] lists the
    (command, is_part) whose units end there, in insertion order."""

    children: list[dict[int, int]] = field(default_factory=lambda: [{}])
    ends: list[list[tuple[int, bool]]] = field(default_factory=lambda: [[]])

    def insert(self, units: np.ndarray, end: tuple[int, bool]) -> None:
        node = 0
        for u in units:
            if int(u) not in self.children[node]:
                self.children[node][int(u)] = len(self.children)
                self.children.append({})
                self.ends.append([])
            node = self.children[node][int(u)]
        if end not in self.ends[node]:
            self.ends[node].append(end)


def command_tree(lexicon: list[list[np.ndarray]]) -> Tree:
    """Every variant of every command, then every part of each command's variants, marked a part of that command."""
    tree = Tree()
    for c, forms in enumerate(lexicon):
        for units in forms:
            tree.insert(units, (c, False))
        for units in ctc_score.parts(forms):
            tree.insert(units, (c, True))
    return tree


@dataclass(frozen=True)
class Hypothesis:
    units: tuple[int, ...]
    score: np.float32
    node: int


def context_of(units: tuple[int, ...], size: int, pad: int) -> tuple[int, ...]:
    """The predictor's last size classes of a hypothesis: pad, then blank, then each unit's class."""
    classes = (pad,) * size + (BLANK,) + tuple(u + 1 for u in units)
    return classes[-size:]


def log_add(a: np.float32, b: np.float32) -> np.float32:
    """log(e^a + e^b) in float32: the larger plus log(1 + e^(smaller - larger)), e^ by ctc_score's exp, the log in
    double rounded to float."""
    top, low = (a, b) if a >= b else (b, a)
    m, e = ctc_score.exp_wide(np.array([low - top], dtype=np.float32))
    tail = np.float32(np.ldexp(np.float64(m[0]), int(e[0])))
    return np.float32(top + np.float32(math.log(1.0 + float(tail))))


def search(log_probs: LogProbs, frames: int, tree: Tree, beam: int, size: int, pad: int) -> list[Hypothesis]:
    """The beam after the last frame: each frame every hypothesis takes blank, keeping its units, or one unit its node
    allows; hypotheses of the same units add; the beam best first, ties by the order they arose."""
    hyps = [Hypothesis((), np.float32(0.0), 0)]
    for t in range(frames):
        found: dict[tuple[int, ...], Hypothesis] = {}
        for h in hyps:
            lp = log_probs(t, context_of(h.units, size, pad))
            moves = [(h.units, np.float32(h.score + lp[BLANK]), h.node)]
            for unit, child in sorted(tree.children[h.node].items()):
                moves.append(((*h.units, unit), np.float32(h.score + lp[unit + 1]), child))
            for units, score, node in moves:
                if units in found:
                    found[units] = Hypothesis(units, log_add(found[units].score, score), node)
                else:
                    found[units] = Hypothesis(units, score, node)
        order = list(found.values())
        hyps = [order[k] for k in sorted(range(len(order)), key=lambda k: (-order[k].score, k))][:beam]
    return hyps


def greedy_score(log_probs: LogProbs, frames: int, size: int, pad: int) -> np.float32:
    """The free path: each frame the likeliest class for the units so far, a unit appended; its summed log-prob."""
    units: tuple[int, ...] = ()
    total = np.float32(0.0)
    for t in range(frames):
        lp = log_probs(t, context_of(units, size, pad))
        best = int(np.argmax(lp))
        total = np.float32(total + lp[best])
        if best != BLANK:
            units = (*units, best - 1)
    return total


def decide(
    log_probs: LogProbs,
    frames: int,
    tree: Tree,
    n_commands: int,
    reject: int,
    margin: int,
    beam: int,
    size: int,
    pad: int,
    own_parts: bool = True,
):
    """The decision of one window, int32 in the order of ctc_score.DECISION, and each command's best score a frame in
    the final beam (-inf when none ends there). The winner is the command a hypothesis ends best on, the first of equal
    ones; own_parts off ignores its parts, a negative control of the golden set."""
    scores = np.full(n_commands, -np.inf, dtype=np.float32)
    part = np.full(n_commands, -np.inf, dtype=np.float32)
    if frames > 0:
        for h in search(log_probs, frames, tree, beam, size, pad):
            for c, is_part in tree.ends[h.node]:
                held = part if is_part else scores
                held[c] = max(held[c], np.float32(h.score / np.float32(frames)))
    best = int(np.argmax(scores))
    reached = bool(scores[best] > -np.inf)
    if not reached:
        return np.array([REJECTED, 0, CAP, CAP], dtype=np.int32), scores
    second = max((s for k, s in enumerate(scores) if k != best), default=np.float32(-np.inf))
    free = np.float32(greedy_score(log_probs, frames, size, pad) / np.float32(frames))
    gap = milli(np.float32(free - scores[best]))
    lead = milli(np.float32(scores[best] - second)) if second > -np.inf else CAP
    whole = not own_parts or bool(part[best] < scores[best])
    accepted = gap <= reject and lead >= margin and whole
    score = milli(ctc_score.exp32(scores[best]))
    return np.array([best if accepted else REJECTED, score, lead, gap], dtype=np.int32), scores
