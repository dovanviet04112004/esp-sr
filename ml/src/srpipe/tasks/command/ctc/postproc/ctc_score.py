"""The ctc decision that ai_engine/src/command_ctc/ mirrors (KEHOACH 3.12): each variant of each command scored by the
CTC forward pass in float32 over the window's log-probabilities, per frame; a command takes its best variant; the best
command is rejected when the free unit loop beats it by more than reject or the second best trails it by less than
margin, both in thousandths of a nat a frame as NVS keeps kws/cmd_reject and kws/cmd_margin.
"""

from __future__ import annotations

import numpy as np

from srpipe.generated import lang_vi
from srpipe.lang import g2p
from srpipe.lang.normalize import normalize
from srpipe.tasks.command.ctc.model.encoder import BLANK

REJECTED = -1
MILLI = np.float32(1000.0)
CAP = 65535  # the uint16 fields of ai_engine_command_result_t
DECISION = ("command", "score_permille", "margin_permille", "free_gap_permille")


def variants(text: str) -> list[np.ndarray]:
    """The lang_vi units of text as each dialect reads it, a reading the same as an earlier one left out."""
    out: list[np.ndarray] = []
    for dialect in range(len(lang_vi.DIALECTS)):
        units = np.array(g2p.g2p(normalize(text, dialect), dialect), dtype=np.uint8)
        if not any(np.array_equal(units, seen) for seen in out):
            out.append(units)
    return out


def logsumexp(*terms: np.float32) -> np.float32:
    top = max(terms)
    if top == -np.inf:
        return np.float32(-np.inf)
    return np.float32(top + np.log(sum(np.exp(np.float32(t - top)) for t in terms), dtype=np.float32))


def sequence_score(log_probs: np.ndarray, units: np.ndarray) -> np.float32:
    """log P(units | window) / frames by the CTC forward pass; log_probs is (classes, frames), a unit's class its id
    plus one past the blank; -inf when the window is too short for the units."""
    frames = log_probs.shape[1]
    labels = [BLANK]
    for u in units:
        labels += [int(u) + 1, BLANK]
    alpha = np.full(len(labels), -np.inf, dtype=np.float32)
    alpha[0] = log_probs[BLANK, 0]
    if len(labels) > 1:
        alpha[1] = log_probs[labels[1], 0]
    for t in range(1, frames):
        before = alpha.copy()
        for s, label in enumerate(labels):
            terms = [before[s]] + ([before[s - 1]] if s >= 1 else [])
            if s >= 2 and label != BLANK and label != labels[s - 2]:
                terms.append(before[s - 2])
            alpha[s] = np.float32(logsumexp(*terms) + log_probs[label, t])
    tail = [alpha[-1]] + ([alpha[-2]] if len(labels) > 1 else [])
    return np.float32(logsumexp(*tail) / np.float32(frames))


def free_score(log_probs: np.ndarray) -> np.float32:
    """The best path with no constraint, per frame: every frame's likeliest class."""
    return np.float32(log_probs.max(axis=0).astype(np.float32).sum(dtype=np.float32) / np.float32(log_probs.shape[1]))


def milli(x: np.float32) -> int:
    """x in thousandths, rounded half to even as lrintf does, held within the uint16 fields."""
    return int(min(CAP, max(0, np.rint(np.float32(x * MILLI)))))


def decide(log_probs: np.ndarray, lexicon: list[list[np.ndarray]], reject: int, margin: int) -> np.ndarray:
    """The decision of one window, int32 in the order of DECISION: a command takes its best variant's score; the
    score field is that per-frame probability in permille."""
    scores = np.array([max(sequence_score(log_probs, v) for v in forms) for forms in lexicon], dtype=np.float32)
    order = np.argsort(-scores, kind="stable")
    best = int(order[0])
    second = scores[order[1]] if len(scores) > 1 else np.float32(-np.inf)
    gap = milli(np.float32(free_score(log_probs) - scores[best])) if scores[best] > -np.inf else CAP
    lead = milli(np.float32(scores[best] - second)) if second > -np.inf else CAP
    accepted = scores[best] > -np.inf and gap <= reject and lead >= margin
    score = milli(np.exp(scores[best], dtype=np.float32)) if scores[best] > -np.inf else 0
    return np.array([best if accepted else REJECTED, score, lead, gap], dtype=np.int32)
