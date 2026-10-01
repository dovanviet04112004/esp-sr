"""The ctc decision that ai_engine/src/command_ctc/ mirrors (KEHOACH 3.12): each variant of each command scored by the
CTC forward pass over the window's probabilities, per frame, each state a float32 mantissa with an exponent of its own
so no alignment underflows; a command takes its best variant; the best command is rejected when the free unit loop
beats it by more than reject or the second best trails it by less than margin, both in thousandths of a nat a frame.
emit writes contracts/golden/command_ctc/, which the C matches bit for bit.
Run: python -m srpipe.tasks.command.ctc.postproc.ctc_score [--out <golden root>]
"""

from __future__ import annotations

import argparse
import json
import math
import struct
from pathlib import Path

import numpy as np

from srpipe.core.config import ML_ROOT
from srpipe.generated import grid, lang_vi
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
# Magic, classes, frames, commands, most variants, most units, timed runs, reject and margin.
DECIDE_HEAD = struct.Struct("<4sHHBBBBHH")
DECIDE_MAGIC = b"SRCD"
DECISION_RECORD = struct.Struct("<hHHH")
ZERO_EXP = -(1 << 30)  # the exponent of a zero, below any a live value reaches
DROP_BELOW = 100  # orders under its sum's top: float32 rounds it away
EXP_MIN_NATS, EXP_MAX_NATS = np.float32(-10000.0), np.float32(10000.0)
LN2 = 0.6931471805599453
LOG2E = np.float32(float.fromhex("0x1.715476p+0"))
LN2_HI, LN2_LO = np.float32(float.fromhex("0x1.63p-1")), np.float32(float.fromhex("-0x1.bd0106p-13"))
# Cephes expf: e^r = 1 + r + r^2 p(r) on |r| <= ln(2)/2, p highest degree first.
EXP_POLY = tuple(
    np.float32(float.fromhex(h))
    for h in ("0x1.a0d2cep-13", "0x1.6e879cp-10", "0x1.11121p-7", "0x1.555382p-5", "0x1.555554p-3", "0x1p-1")
)


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


Wide = tuple[np.ndarray, np.ndarray]


def normalized(x: np.ndarray, e: np.ndarray) -> Wide:
    """x 2^e as float32 mantissas in [1, 2) and their exponents, a zero as (0, ZERO_EXP)."""
    mantissa, k = np.frexp(x.astype(np.float32))
    live = mantissa != 0
    return (
        np.where(live, mantissa * np.float32(2.0), np.float32(0.0)).astype(np.float32),
        np.where(live, e.astype(np.int64) + k - 1, ZERO_EXP),
    )


def exp_wide(x: np.ndarray) -> Wide:
    """e^x of float32 x as mantissas and exponents, by the float32 steps the C takes in the same order; 0 below
    EXP_MIN_NATS and for NaN, x held at EXP_MAX_NATS above it."""
    x = np.asarray(x, dtype=np.float32)
    live = x >= EXP_MIN_NATS
    x = np.minimum(np.where(live, x, np.float32(0.0)), EXP_MAX_NATS)
    n = np.floor(x * LOG2E + np.float32(0.5))
    r = (x - n * LN2_HI) - n * LN2_LO
    poly = np.full_like(r, EXP_POLY[0])
    for c in EXP_POLY[1:]:
        poly = poly * r + c
    poly = (poly * (r * r) + r) + np.float32(1.0)
    mantissa, e = normalized(poly, n.astype(np.int64))
    return np.where(live, mantissa, np.float32(0.0)).astype(np.float32), np.where(live, e, ZERO_EXP)


def wide_sum(terms: list[Wide]) -> Wide:
    """The float32 sum of terms in order, each scaled to the largest exponent among them exactly, a term DROP_BELOW
    orders under it dropped; the sum and that exponent."""
    top = np.max([e for _, e in terms], axis=0)
    total = np.zeros_like(terms[0][0])
    for m, e in terms:
        d = e - top
        scale = np.ldexp(np.float32(1.0), np.maximum(d, -DROP_BELOW).astype(np.int32))
        total = total + m * np.where(d >= -DROP_BELOW, scale, np.float32(0.0)).astype(np.float32)
    return total.astype(np.float32), top


def shifted(alpha: Wide, by: int, keep: np.ndarray | None = None) -> Wide:
    """alpha moved by states up, zeros in from the start; states outside keep zeroed too."""
    n = len(alpha[0])
    m = np.concatenate([np.zeros(by, np.float32), alpha[0]])[:n]
    e = np.concatenate([np.full(by, ZERO_EXP, np.int64), alpha[1]])[:n]
    if keep is not None:
        m, e = np.where(keep, m, np.float32(0.0)).astype(np.float32), np.where(keep, e, ZERO_EXP)
    return m, e


def sequence_score(table: Wide, units: np.ndarray, skip: bool = True) -> np.float32:
    """log P(units | window) / frames by the CTC forward pass over the window's probabilities as exp_wide gives them,
    (classes, frames), a unit's class its id plus one past the blank; -inf when the window is too short. skip off
    forbids moving from a unit straight to the next, the negative control of the golden set."""
    mantissas, exponents = table
    frames = mantissas.shape[1]
    labels = np.array([BLANK] + [c for u in units for c in (int(u) + 1, BLANK)])
    jumps = np.zeros(len(labels), dtype=bool)
    jumps[2:] = skip & (labels[2:] != BLANK) & (labels[2:] != labels[:-2])
    m, e = np.zeros(len(labels), np.float32), np.full(len(labels), ZERO_EXP, np.int64)
    first = labels[:2]
    m[: len(first)], e[: len(first)] = mantissas[first, 0], exponents[first, 0]
    for t in range(1, frames):
        total, top = wide_sum([(m, e), shifted((m, e), 1), shifted((m, e), 2, jumps)])
        m, e = normalized(total * mantissas[labels, t], top + exponents[labels, t])
    tail = [(m[-1:], e[-1:])] + ([(m[-2:-1], e[-2:-1])] if len(labels) > 1 else [])
    total, top = wide_sum(tail)
    if total[0] == 0:
        return np.float32(-np.inf)
    return np.float32((math.log(float(total[0])) + float(top[0]) * LN2) / float(frames))


def free_score(log_probs: np.ndarray) -> np.float32:
    """The best path with no constraint, per frame: every frame's likeliest class, summed in frame order."""
    total = np.float32(0.0)
    for v in log_probs.max(axis=0):
        total = np.float32(total + np.float32(v))
    return np.float32(total / np.float32(log_probs.shape[1]))


def milli(x: np.float32) -> int:
    """x in thousandths, rounded half to even as lrintf does, held within the uint16 fields."""
    return int(min(CAP, max(0, np.rint(np.float32(x * MILLI)))))


def command_scores(table: Wide, lexicon: list[list[np.ndarray]], skip: bool = True) -> np.ndarray:
    """Each command's best variant score."""
    return np.array([max(sequence_score(table, v, skip) for v in forms) for forms in lexicon], dtype=np.float32)


def decide(log_probs: np.ndarray, lexicon: list[list[np.ndarray]], reject: int, margin: int, skip: bool = True):
    """The decision of one window of (classes, frames) log-probabilities, int32 in the order of DECISION; the score
    field is the winner's per-frame probability in permille. The first of equal scores wins."""
    scores = command_scores(exp_wide(log_probs), lexicon, skip)
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
    """Log-probabilities per frame of logits (classes, frames): exp and log in double, an exact sum, one rounding to
    float32, so the golden set comes out the same on every CPU (KEHOACH 3.14)."""
    out = np.empty(logits.shape, dtype=np.float32)
    for t in range(logits.shape[1]):
        column = [float(v) for v in logits[:, t]]
        top = max(column)
        total = math.log(math.fsum(math.exp(v - top) for v in column))
        out[:, t] = [v - top - total for v in column]
    return out


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


def probe_record(cfg: dict) -> bytes:
    """The decision of the default commands over one window of the longest LENH keeps, saying one of them, as the
    unit app of ai_engine times it on board B: DECIDE_HEAD, variants a command, units a variant, the units padded,
    the frames' log-probabilities from the next four-byte boundary, the expected decision, every command's score."""
    spec = cfg["probe"]["decide"]
    stride = math.prod(cfg["model"]["front"]["hop_strides"])
    frames = math.ceil(cfg["window_s"] * grid.SAMPLE_RATE_HZ / grid.HOP_SAMPLES / stride)
    rng, lexicon = np.random.default_rng(SEED), default_lexicon()
    window = said(rng, lexicon[0][0], frames)
    decision, scores = decide(window, lexicon, *spec["thresholds"])
    most, longest = max(len(f) for f in lexicon), max(len(u) for f in lexicon for u in f)
    units = np.zeros((len(lexicon), most, longest), dtype=np.uint8)
    n_units = np.zeros((len(lexicon), most), dtype=np.uint8)
    for c, forms in enumerate(lexicon):
        for v, u in enumerate(forms):
            units[c, v, : len(u)], n_units[c, v] = u, len(u)
    sizes = (n_classes(), frames, len(lexicon), most, longest, spec["runs"])
    head = DECIDE_HEAD.pack(DECIDE_MAGIC, *sizes, *spec["thresholds"])
    body = head + bytes(len(f) for f in lexicon) + n_units.tobytes() + units.tobytes()
    body += b"\0" * (-len(body) % 4) + np.ascontiguousarray(window.T, dtype="<f4").tobytes()
    return body + DECISION_RECORD.pack(*decision.tolist()) + scores.astype("<f4").tobytes()


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
