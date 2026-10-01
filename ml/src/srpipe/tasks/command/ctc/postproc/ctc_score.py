"""The ctc decision that ai_engine/src/command_ctc/ mirrors (KEHOACH 3.12): each variant of each command scored by the
CTC forward pass in float32 over the window's log-probabilities, per frame; a command takes its best variant; the best
command is rejected when the free unit loop beats it by more than reject or the second best trails it by less than
margin, both in thousandths of a nat a frame. emit writes contracts/golden/command_ctc/, which the C matches exactly.
Run: python -m srpipe.tasks.command.ctc.postproc.ctc_score [--out <golden root>]
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from srpipe.core.config import ML_ROOT
from srpipe.generated import lang_vi
from srpipe.golden.gold import write_gold
from srpipe.lang import g2p
from srpipe.lang.normalize import normalize
from srpipe.tasks import command
from srpipe.tasks.command.ctc.model.encoder import BLANK, n_classes

GOLDEN_ROOT = ML_ROOT.parent / "contracts" / "golden"
BLOCK = "command_ctc"
REJECTED = -1
MILLI = np.float32(1000.0)
CAP = 65535  # the uint16 fields of ai_engine_command_result_t
DECISION = ("command", "score_permille", "margin_permille", "free_gap_permille")
SEED = 20261001
WINDOWS, FRAMES = 12, 48  # a case's windows and their longest, in frames
SPREAD = (0.5, 6.0)


def variants(text: str) -> list[np.ndarray]:
    """The lang_vi units of text as each dialect reads it, a reading the same as an earlier one left out."""
    out: list[np.ndarray] = []
    for dialect in range(len(lang_vi.DIALECTS)):
        units = np.array(g2p.g2p(normalize(text, dialect), dialect), dtype=np.uint8)
        if not any(np.array_equal(units, seen) for seen in out):
            out.append(units)
    return out


def exp32(x: np.float32) -> np.float32:
    return np.float32(math.exp(float(x)))


def logsumexp(terms: list[np.float32]) -> np.float32:
    """log of the sum of exp of terms: exp and log in double rounded to float, the sum in float in order, as the C."""
    top = max(terms)
    if top == -np.inf:
        return np.float32(-np.inf)
    total = np.float32(0.0)
    for t in terms:
        total = np.float32(total + exp32(np.float32(t - top)))
    return np.float32(top + np.float32(math.log(float(total))))


def sequence_score(log_probs: np.ndarray, units: np.ndarray, skip: bool = True) -> np.float32:
    """log P(units | window) / frames by the CTC forward pass over log_probs (classes, frames), a unit's class its id
    plus one past the blank; -inf when the window is too short. skip off forbids moving from a unit straight to the
    next, the negative control of the golden set."""
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
            if skip and s >= 2 and label != BLANK and label != labels[s - 2]:
                terms.append(before[s - 2])
            alpha[s] = np.float32(logsumexp(terms) + log_probs[label, t])
    tail = [alpha[-1]] + ([alpha[-2]] if len(labels) > 1 else [])
    return np.float32(logsumexp(tail) / np.float32(frames))


def free_score(log_probs: np.ndarray) -> np.float32:
    """The best path with no constraint, per frame: every frame's likeliest class, summed in frame order."""
    total = np.float32(0.0)
    for v in log_probs.max(axis=0):
        total = np.float32(total + np.float32(v))
    return np.float32(total / np.float32(log_probs.shape[1]))


def milli(x: np.float32) -> int:
    """x in thousandths, rounded half to even as lrintf does, held within the uint16 fields."""
    return int(min(CAP, max(0, np.rint(np.float32(x * MILLI)))))


def command_scores(log_probs: np.ndarray, lexicon: list[list[np.ndarray]], skip: bool = True) -> np.ndarray:
    """Each command's best variant score."""
    return np.array([max(sequence_score(log_probs, v, skip) for v in forms) for forms in lexicon], dtype=np.float32)


def decide(log_probs: np.ndarray, lexicon: list[list[np.ndarray]], reject: int, margin: int, skip: bool = True):
    """The decision of one window, int32 in the order of DECISION; the score field is the winner's per-frame
    probability in permille. The first of equal scores wins."""
    scores = command_scores(log_probs, lexicon, skip)
    best = int(np.argmax(scores))
    second = max((s for k, s in enumerate(scores) if k != best), default=np.float32(-np.inf))
    reached = bool(scores[best] > -np.inf)
    gap = milli(np.float32(free_score(log_probs) - scores[best])) if reached else CAP
    lead = milli(np.float32(scores[best] - second)) if second > -np.inf else CAP
    accepted = reached and gap <= reject and lead >= margin
    score = milli(exp32(scores[best])) if reached else 0
    return np.array([best if accepted else REJECTED, score, lead, gap], dtype=np.int32), scores


def default_lexicon() -> list[list[np.ndarray]]:
    """Every command of default_vi.json, the unseen one too, in file order."""
    listed = json.loads(command.COMMANDS.read_text(encoding="utf-8"))["commands"]
    return [variants(c["text"]) for c in listed]


def said(rng: np.random.Generator, units: np.ndarray, frames: int) -> np.ndarray:
    """Log-probabilities of a window saying units, each unit held a few frames, blanks between and around them."""
    logits = rng.standard_normal((n_classes(), frames)).astype(np.float32)
    per = max(1, frames // (len(units) + 1))
    for k, u in enumerate(units):
        logits[int(u) + 1, k * per : k * per + per - 1] += 6.0
        logits[BLANK, k * per + per - 1] += 6.0
    logits[BLANK, len(units) * per :] += 6.0
    return log_softmax(logits)


def log_softmax(logits: np.ndarray) -> np.ndarray:
    top = logits.max(axis=0)
    return (logits - top - np.log(np.exp(logits - top).sum(axis=0))).astype(np.float32)


def case(windows: list[np.ndarray], lexicon: list[list[np.ndarray]], thresholds: np.ndarray, skip: bool = True):
    """One golden case: windows of (classes, frames) log-probabilities laid frame by frame and padded to FRAMES, the
    lexicon padded, each window's thresholds, and the command scores and decision of each."""
    longest = max(len(u) for forms in lexicon for u in forms)
    most = max(len(forms) for forms in lexicon)
    units = np.zeros((len(lexicon), most, longest), dtype=np.int32)
    n_units = np.zeros((len(lexicon), most), dtype=np.int32)
    for c, forms in enumerate(lexicon):
        for v, u in enumerate(forms):
            units[c, v, : len(u)], n_units[c, v] = u, len(u)
    grid = np.zeros((len(windows), FRAMES, n_classes()), dtype=np.float32)
    decisions, scores = [], []
    for w, (x, limits) in enumerate(zip(windows, thresholds, strict=True)):
        grid[w, : x.shape[1]] = x.T
        decision, row = decide(x, lexicon, int(limits[0]), int(limits[1]), skip)
        decisions.append(decision)
        scores.append(row)
    return {
        "log_probs": grid,
        "frames": np.array([x.shape[1] for x in windows], dtype=np.int32),
        "units": units,
        "n_variants": np.array([len(forms) for forms in lexicon], dtype=np.int32),
        "n_units": n_units,
        "thresholds": np.asarray(thresholds, dtype=np.int32),
        "scores": np.stack(scores),
        "decision": np.stack(decisions),
    }


def random_case(rng: np.random.Generator, lexicon: list[list[np.ndarray]]):
    """Windows of every confidence and length, a third of them saying a command, thresholds across their range."""
    windows = []
    for k in range(WINDOWS):
        frames = int(rng.integers(8, FRAMES + 1))
        if k % 3 == 0:
            forms = lexicon[int(rng.integers(len(lexicon)))]
            windows.append(said(rng, forms[int(rng.integers(len(forms)))], frames))
        else:
            spread = np.float32(rng.uniform(*SPREAD))
            windows.append(log_softmax(rng.standard_normal((n_classes(), frames)).astype(np.float32) * spread))
    thresholds = np.stack([rng.integers(0, 3001, WINDOWS), rng.integers(0, 501, WINDOWS)], axis=1)
    return case(windows, lexicon, thresholds)


def edge_case(rng: np.random.Generator):
    """A command of a repeated unit said and said once, two equal commands, a window too short for any command,
    and a winner exactly on the reject threshold and one exactly on the margin."""
    lexicon = [[np.array([3, 3], np.uint8)], [np.array([0, 1], np.uint8)], [np.array([0, 1], np.uint8)]]
    windows = [
        said(rng, np.array([3, 3], np.uint8), 24),
        said(rng, np.array([3], np.uint8), 24),
        said(rng, np.array([0, 1], np.uint8), 24),
        said(rng, np.array([3], np.uint8), 1),
        said(rng, np.array([3, 3], np.uint8), 30),
        said(rng, np.array([3, 3], np.uint8), 36),
    ]
    thresholds = np.full((len(windows), 2), (1000, 50), dtype=np.int32)
    thresholds[2, 1] = 1
    thresholds[4, 0] = decide(windows[4], lexicon, 0, 0)[0][3]
    thresholds[5] = (CAP, decide(windows[5], lexicon, 0, 0)[0][2])
    return case(windows, lexicon, thresholds)


def emit(root: Path) -> list[Path]:
    """Random windows over the default commands and over a small set, the edge windows, and a negative control whose
    forward pass never moves from one unit straight to the next."""
    rng = np.random.default_rng(SEED)
    lexicon = default_lexicon()
    cases = {
        "case_000": random_case(rng, lexicon),
        "case_001": random_case(rng, lexicon[:3]),
        "case_002": edge_case(rng),
    }
    windows = [x[:f].T for x, f in zip(cases["case_000"]["log_probs"], cases["case_000"]["frames"], strict=True)]
    cases["case_neg_000"] = case(windows, lexicon, cases["case_000"]["thresholds"], skip=False)
    written = []
    for name, tensors in cases.items():
        path = root / BLOCK / f"{name}.gold"
        write_gold(path, tensors)
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=GOLDEN_ROOT)
    for path in emit(parser.parse_args().out):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
