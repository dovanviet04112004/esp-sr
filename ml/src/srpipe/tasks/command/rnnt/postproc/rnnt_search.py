"""The rnnt decision that ai_engine/src/command_rnnt/ mirrors (KEHOACH 3.12, ADR-0016): a modified beam search, one
symbol a frame, over MultiNet7's kind of minimal FST of every command's variants and parts, then the ctc track's rules:
the greedy path ahead by more than reject, another command within margin, or a part of the winner no lower than it,
both in thousandths of a nat a frame. Every score is float32 in the order the C takes; two hypotheses of the same units
add in probability through ctc_score's exp and a log in double rounded to float. emit writes contracts/golden/
command_rnnt/. Run: python -m srpipe.tasks.command.rnnt.postproc.rnnt_search [--out <golden root>]
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


def search(
    log_probs: LogProbs, frames: int, fst: Fst, beam: int, size: int, pad: int, add: bool = True
) -> list[Hypothesis]:
    """The beam after the last frame: each frame every hypothesis takes blank, keeping its units, or one unit an arc of
    its state allows; hypotheses of the same units add; the beam best first, ties by the order they arose. add off
    keeps the larger of two such hypotheses instead, a negative control of the golden set."""
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
                    held = found[units].score
                    found[units] = Hypothesis(units, log_add(held, score) if add else max(held, score), state)
                else:
                    found[units] = Hypothesis(units, score, state)
        order = list(found.values())
        hyps = [order[k] for k in sorted(range(len(order)), key=lambda k: (-order[k].score, k))][:beam]
    return hyps


def greedy(log_probs: LogProbs, frames: int, size: int, pad: int) -> tuple[tuple[int, ...], np.float32]:
    """The free path: each frame the likeliest class for the units so far, a unit appended; its units and its summed
    log-prob."""
    units: tuple[int, ...] = ()
    total = np.float32(0.0)
    for t in range(frames):
        lp = log_probs(t, context_of(units, size, pad))
        best = int(np.argmax(lp))
        total = np.float32(total + lp[best])
        if best != BLANK:
            units = (*units, best - 1)
    return units, total


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
    add: bool = True,
):
    """The decision of one window, int32 in the order of ctc_score.DECISION, and each command's best score a frame in
    the final beam (-inf when none ends there). The winner is the command a hypothesis ends best on, the first of equal
    ones; own_parts off ignores its parts and add off merges by the larger, negative controls of the golden set."""
    scores = np.full(n_commands, -np.inf, dtype=np.float32)
    part = np.full(n_commands, -np.inf, dtype=np.float32)
    if frames > 0:
        for h in search(log_probs, frames, fst, beam, size, pad, add):
            for c, is_part in fst.ends.get(h.units, []) if fst.final[h.state] else []:
                held = part if is_part else scores
                held[c] = max(held[c], np.float32(h.score / np.float32(frames)))
    best = int(np.argmax(scores))
    reached = bool(scores[best] > -np.inf)
    if not reached:
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


def said(rng: np.random.Generator, units: np.ndarray, frames: int, exponent: int) -> np.ndarray:
    """int8 logits (frames, classes) of a window saying units, each on its own frame with a blank after it."""
    logits = rng.standard_normal((frames, ctc_score.n_classes())).astype(np.float32)
    for k, u in enumerate(units):
        logits[2 * k, int(u) + 1] += 6.0
        logits[2 * k + 1, BLANK] += 6.0
    logits[2 * len(units) :, BLANK] += 6.0
    return ctc_score.quantised(logits, exponent)


def case(
    windows: list[tuple[np.ndarray, int]],
    lexicon: list[list[np.ndarray]],
    tables: tuple[np.ndarray, np.ndarray],
    limits: np.ndarray,
    own_parts: bool = True,
    add: bool = True,
) -> dict[str, np.ndarray]:
    """One golden case: int8 frames (frames, classes) and their exponents padded to FRAMES, the lexicon padded, the
    context tables, each window's reject, margin and beam, and the command scores and decision of each."""
    longest = max(len(u) for forms in lexicon for u in forms)
    most = max(len(forms) for forms in lexicon)
    units = np.zeros((len(lexicon), most, longest), dtype=np.int32)
    n_units = np.zeros((len(lexicon), most), dtype=np.int32)
    for c, forms in enumerate(lexicon):
        for v, u in enumerate(forms):
            units[c, v, : len(u)], n_units[c, v] = u, len(u)
    frames = np.zeros((len(windows), FRAMES, ctc_score.n_classes()), dtype=np.int8)
    fst, pad = command_fst(lexicon), ctc_score.n_classes()
    decisions, scores = [], []
    for w, ((q, exponent), (reject, margin, beam)) in enumerate(zip(windows, limits, strict=True)):
        frames[w, : len(q)] = q
        lp = table_log_probs(q, *tables, exponent)
        decision, row = decide(lp, len(q), fst, len(lexicon), reject, margin, beam, CONTEXT, pad, own_parts, add)
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
    """Small int8 pulls of the two context classes, the pad id's row included, so the context changes the logits."""
    shape = (ctc_score.n_classes() + 1, ctc_score.n_classes())
    return tuple(np.clip(np.rint(rng.normal(0, 3, shape)), -16, 16).astype(np.int8) for _ in range(2))


def random_windows(rng: np.random.Generator, lexicon: list[list[np.ndarray]]) -> tuple[list, np.ndarray]:
    """Windows of every length, a third saying a whole command, a third a part of one, a third noise; reject,
    margin and beam across their range."""
    windows = []
    for k in range(WINDOWS):
        frames, exponent = int(rng.integers(8, FRAMES + 1)), int(rng.choice(ctc_score.LOGIT_EXPONENTS))
        forms = lexicon[int(rng.integers(len(lexicon)))]
        whole = forms[int(rng.integers(len(forms)))]
        pieces = ctc_score.parts(forms)
        units = whole if k % 3 == 0 else (pieces[int(rng.integers(len(pieces)))] if pieces and k % 3 == 1 else None)
        if units is not None and 2 * len(units) <= frames:
            windows.append((said(rng, units, frames, exponent), exponent))
        else:
            noise = rng.standard_normal((frames, ctc_score.n_classes())) * rng.uniform(0.5, 6.0)
            windows.append((ctc_score.quantised(noise.astype(np.float32), exponent), exponent))
    limits = np.stack([rng.integers(0, 3001, WINDOWS), rng.integers(0, 501, WINDOWS), rng.integers(1, 9, WINDOWS)], 1)
    return windows, limits


def edge_windows(rng: np.random.Generator, lexicon: list[list[np.ndarray]]) -> tuple[list, np.ndarray]:
    """No frame at all, a window too short for any command, the first command whole and its first syllable alone with
    every threshold open, the latter with a beam wide enough to keep the whole, and the whole again with a beam of
    one."""
    e = ctc_score.LOGIT_EXPONENTS[1]
    whole = lexicon[0][0]
    first = ctc_score.syllables(whole)[0]
    windows = [
        (np.zeros((0, ctc_score.n_classes()), np.int8), e),
        (said(rng, whole[:0], 1, e), e),
        (said(rng, whole, FRAMES, e), e),
        (said(rng, first, FRAMES, e), e),
        (said(rng, whole, FRAMES, e), e),
    ]
    limits = np.array([[CAP, 0, 4], [CAP, 0, 4], [CAP, 0, 4], [CAP, 0, 64], [CAP, 0, 1]], dtype=np.int32)
    return windows, limits


def emit(root: Path) -> list[Path]:
    """Random windows over the default commands and over a small set, the edge windows, and two negative controls on
    the edges: a winner whose parts are ignored, and hypotheses of the same units merged by the larger."""
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
