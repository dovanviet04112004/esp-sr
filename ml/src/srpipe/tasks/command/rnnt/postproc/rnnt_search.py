"""The rnnt decision that ai_engine/src/command_rnnt/ mirrors (KEHOACH 3.12, ADR-0016): a modified beam search, one
symbol a frame, over MultiNet7's kind of minimal FST of every command's variants and parts, then the ctc track's rules:
the greedy path ahead by more than reject, another command within margin, or a part of the winner no lower than it,
both in thousandths of a nat a frame. Every score is float32 in the order the C takes; two hypotheses of the same units
add in probability through ctc_score's exp and a log in double rounded to float.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from srpipe.tasks.command.ctc.postproc import ctc_score
from srpipe.tasks.command.ctc.postproc.ctc_score import BLANK, CAP, REJECTED, milli

LogProbs = Callable[[int, tuple[int, ...]], np.ndarray]  # frame, predictor context → (classes,) float32


@dataclass(frozen=True)
class Fst:
    """A minimal deterministic acceptor of unit sequences, state 0 the start: arcs[state] maps a unit to the next
    state, final[state] whether a sequence may end there. A minimal state ends sequences of several commands, so ends
    tells each accepted sequence's (command, is_part) by its units, as MultiNet7 reads a path's."""

    arcs: list[dict[int, int]]
    final: list[bool]
    ends: dict[tuple[int, ...], list[tuple[int, bool]]]


def accepted(lexicon: list[list[np.ndarray]]) -> dict[tuple[int, ...], list[tuple[int, bool]]]:
    """Every variant of every command, then every part of each command's variants, with what each sequence is."""
    ends: dict[tuple[int, ...], list[tuple[int, bool]]] = {}
    for c, forms in enumerate(lexicon):
        for units, is_part in [(f, False) for f in forms] + [(p, True) for p in ctc_score.parts(forms)]:
            held = ends.setdefault(tuple(int(u) for u in units), [])
            if (c, is_part) not in held:
                held.append((c, is_part))
    return ends


def command_fst(lexicon: list[list[np.ndarray]]) -> Fst:
    """The sequences of accepted() as a prefix tree, made minimal by the register algorithm for finite string sets
    (Daciuk et al., 2000): states of the same finality whose arcs reach the same states are one; states numbered by
    a breadth-first walk from the start over units in order."""
    ends = accepted(lexicon)
    arcs: list[dict[int, int]] = [{}]
    final = [False]
    for units in ends:
        state = 0
        for u in units:
            if u not in arcs[state]:
                arcs[state][u] = len(arcs)
                arcs.append({})
                final.append(False)
            state = arcs[state][u]
        final[state] = True
    register: dict[tuple, int] = {}
    canonical = [0] * len(arcs)
    stack, seen = [(0, False)], set()
    while stack:
        state, children_done = stack.pop()
        if not children_done:
            stack.append((state, True))
            stack += [(t, False) for t in arcs[state].values() if t not in seen]
            seen.update(arcs[state].values())
            continue
        signature = (final[state], tuple(sorted((u, canonical[t]) for u, t in arcs[state].items())))
        canonical[state] = register.setdefault(signature, len(register))
    merged_arcs = {canonical[s]: {u: canonical[t] for u, t in arcs[s].items()} for s in range(len(arcs))}
    merged_final = {canonical[s]: final[s] for s in range(len(arcs))}
    number, order = {canonical[0]: 0}, [canonical[0]]
    for state in order:
        for _, t in sorted(merged_arcs[state].items()):
            if t not in number:
                number[t] = len(order)
                order.append(t)
    return Fst(
        [{u: number[t] for u, t in sorted(merged_arcs[s].items())} for s in order],
        [merged_final[s] for s in order],
        ends,
    )


@dataclass(frozen=True)
class Hypothesis:
    units: tuple[int, ...]
    score: np.float32
    state: int


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


def search(log_probs: LogProbs, frames: int, fst: Fst, beam: int, size: int, pad: int) -> list[Hypothesis]:
    """The beam after the last frame: each frame every hypothesis takes blank, keeping its units, or one unit an arc of
    its state allows; hypotheses of the same units add; the beam best first, ties by the order they arose."""
    hyps = [Hypothesis((), np.float32(0.0), 0)]
    for t in range(frames):
        found: dict[tuple[int, ...], Hypothesis] = {}
        for h in hyps:
            lp = log_probs(t, context_of(h.units, size, pad))
            moves = [(h.units, np.float32(h.score + lp[BLANK]), h.state)]
            for unit, after in fst.arcs[h.state].items():
                moves.append(((*h.units, unit), np.float32(h.score + lp[unit + 1]), after))
            for units, score, state in moves:
                if units in found:
                    found[units] = Hypothesis(units, log_add(found[units].score, score), state)
                else:
                    found[units] = Hypothesis(units, score, state)
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
    fst: Fst,
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
        for h in search(log_probs, frames, fst, beam, size, pad):
            for c, is_part in fst.ends.get(h.units, []) if fst.final[h.state] else []:
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
