"""The ctc postprocessing that ai_engine/src/command_ctc/ mirrors (KEHOACH 3.12): int8 logits to log-probabilities,
each variant of each command scored by the CTC forward pass, each state a float32 mantissa with an exponent of its
own; the best command is rejected when the free unit loop beats it by more than reject, the second best trails it by
less than margin, both in thousandths of a nat a frame of a window_s window, or a run of its own syllables short of it
scores no lower. emit writes contracts/golden/command_ctc/, which the C matches bit for bit.
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
from srpipe.generated import lang_vi, listen
from srpipe.golden.gold import write_gold
from srpipe.lang import g2p
from srpipe.lang.normalize import normalize
from srpipe.tasks import command

GOLDEN_ROOT = ML_ROOT.parent / "contracts" / "golden"
BLOCK = "command_ctc"
REJECTED = -1
MILLI = np.float32(1000.0)
CAP = 65535  # the uint16 fields of ai_engine_command_result_t
DECISION = ("command", "score_permille", "margin_permille", "free_gap_permille")
SEED = 20261001
WINDOWS, FRAMES = 12, 48  # a case's windows and their longest, in frames
SPREAD = (0.5, 6.0)
LOGIT_EXPONENTS = (-4, -3, -2)  # int8 steps 2^e of the golden logits
# Magic, classes, frames, commands, most variants, most units, timed runs, reject and margin.
DECIDE_HEAD = struct.Struct("<4sHHBBBBHH")
DECIDE_MAGIC = b"SRCD"
DECISION_RECORD = struct.Struct("<hHHH")
ZERO_EXP = -(1 << 30)  # the exponent of a zero, below any a live value reaches
BLANK = 0  # a frame's CTC blank; a lang_vi unit takes its id plus one
TONE_UNITS = frozenset(lang_vi.UNITS.index(t) for t in lang_vi.TONES)  # one ends each syllable
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


def n_classes() -> int:
    """The lang_vi units and the CTC blank, which takes class 0."""
    return len(lang_vi.UNITS) + 1


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


def sequence_score(table: Wide, units: np.ndarray, per_frames: int, skip: bool = True) -> np.float32:
    """log P(units | window) / per_frames by the CTC forward pass over the window's probabilities as exp_wide gives
    them, (classes, frames), a unit's class its id plus one past the blank; -inf when the window is too short. skip off
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
    return np.float32((math.log(float(total[0])) + float(top[0]) * LN2) / float(per_frames))


def free_score(log_probs: np.ndarray, per_frames: int) -> np.float32:
    """The best path with no constraint over per_frames: every frame's likeliest class, summed in frame order."""
    total = np.float32(0.0)
    for v in log_probs.max(axis=0):
        total = np.float32(total + np.float32(v))
    return np.float32(total / np.float32(per_frames))


def window_frames(stride: int) -> int:
    """T_W, every score's divisor: the frames of a window of listen's window_s at stride hops a frame (KEHOACH 3.12)."""
    return math.ceil(listen.WINDOW_HOPS / stride)


def milli(x: np.float32) -> int:
    """x in thousandths, rounded half to even as lrintf does, held within the uint16 fields."""
    return int(min(CAP, max(0, np.rint(np.float32(x * MILLI)))))


def command_scores(table: Wide, lexicon: list[list[np.ndarray]], per_frames: int, skip: bool = True) -> np.ndarray:
    """Each command's best variant score."""
    return np.array(
        [max(sequence_score(table, v, per_frames, skip) for v in forms) for forms in lexicon], dtype=np.float32
    )


def syllables(units: np.ndarray) -> list[np.ndarray]:
    """units cut after each tone unit, the last unit of every lang_vi syllable."""
    ends = [k + 1 for k, u in enumerate(units) if int(u) in TONE_UNITS]
    if not ends or ends[-1] != len(units):
        ends.append(len(units))
    return [units[a:b] for a, b in zip([0, *ends[:-1]], ends, strict=True)]


def parts(forms: list[np.ndarray]) -> list[np.ndarray]:
    """Every run of whole syllables of a variant in forms short of the whole variant, in variant, then start, then
    length order, a run listed already left out."""
    out: list[np.ndarray] = []
    for units in forms:
        cut = syllables(units)
        for first in range(len(cut)):
            for last in range(first + 1, len(cut) + 1 - (first == 0)):
                run = np.concatenate(cut[first:last])
                if not any(np.array_equal(run, seen) for seen in out):
                    out.append(run)
    return out


def decide(
    log_probs: np.ndarray,
    lexicon: list[list[np.ndarray]],
    reject: int,
    margin: int,
    per_frames: int,
    skip: bool = True,
    own_parts: bool = True,
):
    """The decision of one window of (classes, frames) log-probabilities, int32 in the order of DECISION, every score
    divided by per_frames, T_W on the device; the score field is e to the winner's score in permille. The winner is
    also rejected when a part of its own scores no lower than it (KEHOACH 3.12). The first of equal scores wins.
    own_parts off never scores the winner's parts, a negative control of the golden set."""
    table = exp_wide(log_probs)
    scores = command_scores(table, lexicon, per_frames, skip)
    best = int(np.argmax(scores))
    second = max((s for k, s in enumerate(scores) if k != best), default=np.float32(-np.inf))
    reached = bool(scores[best] > -np.inf)
    gap = milli(np.float32(free_score(log_probs, per_frames) - scores[best])) if reached else CAP
    lead = milli(np.float32(scores[best] - second)) if second > -np.inf else CAP
    whole = not own_parts or all(
        sequence_score(table, p, per_frames, skip) < scores[best] for p in parts(lexicon[best])
    )
    accepted = reached and gap <= reject and lead >= margin and whole
    score = milli(exp32(scores[best])) if reached else 0
    return np.array([best if accepted else REJECTED, score, lead, gap], dtype=np.int32), scores


def default_lexicon() -> list[list[np.ndarray]]:
    """Every command of default_vi.json, the unseen one too, in file order."""
    listed = json.loads(command.COMMANDS.read_text(encoding="utf-8"))["commands"]
    return [variants(c["text"]) for c in listed]


def quantised(logits: np.ndarray, exponent: int) -> np.ndarray:
    """float logits on the int8 grid of 2^exponent, rounded half to even and held to int8, as the chip gives them."""
    return np.clip(np.rint(logits / np.float32(2.0**exponent)), -128, 127).astype(np.int8)


def frame_log_probs(logits: np.ndarray, exponent: int, center: bool = True) -> np.ndarray:
    """Log-probabilities (classes, frames) of int8 logits worth logits 2^exponent, by the float32 steps the C takes:
    each frame's largest logit taken off exactly, exp_wide of every class summed in class order by wide_sum, the log of
    the sum in double rounded to float, taken off each. center off leaves the largest on, the negative control."""
    x = np.ldexp(logits.astype(np.float32), exponent).astype(np.float32)
    d = (x - x.max(axis=0)).astype(np.float32) if center else x
    total, top = wide_sum([exp_wide(row) for row in d])
    logs = [math.log(float(s)) + float(t) * LN2 for s, t in zip(total, top, strict=True)]
    return (d - np.array(logs, dtype=np.float32)).astype(np.float32)


def said(rng: np.random.Generator, units: np.ndarray, frames: int, exponent: int) -> np.ndarray:
    """int8 logits (classes, frames) on the grid of 2^exponent of a window saying units, each unit held a few frames,
    blanks between and around them."""
    logits = rng.standard_normal((n_classes(), frames)).astype(np.float32)
    per = max(1, frames // (len(units) + 1))
    for k, u in enumerate(units):
        logits[int(u) + 1, k * per : k * per + per - 1] += 6.0
        logits[BLANK, k * per + per - 1] += 6.0
    logits[BLANK, len(units) * per :] += 6.0
    return quantised(logits, exponent)


def case(
    windows: list[tuple[np.ndarray, int]],
    lexicon: list[list[np.ndarray]],
    thresholds: np.ndarray,
    skip: bool = True,
    center: bool = True,
    own_parts: bool = True,
    by_own_frames: bool = False,
):
    """One golden case: windows of int8 logits (classes, frames) and their exponents, logits and log-probabilities
    laid frame by frame and padded to FRAMES, the lexicon padded, each window's thresholds, FRAMES as the scores'
    divisor, and the command scores and decision of each; by_own_frames divides by each window's own frames instead
    while the case still says FRAMES, a negative control."""
    longest = max(len(u) for forms in lexicon for u in forms)
    most = max(len(forms) for forms in lexicon)
    units = np.zeros((len(lexicon), most, longest), dtype=np.int32)
    n_units = np.zeros((len(lexicon), most), dtype=np.int32)
    for c, forms in enumerate(lexicon):
        for v, u in enumerate(forms):
            units[c, v, : len(u)], n_units[c, v] = u, len(u)
    logits = np.zeros((len(windows), FRAMES, n_classes()), dtype=np.float32)
    grid = np.zeros((len(windows), FRAMES, n_classes()), dtype=np.float32)
    decisions, scores = [], []
    for w, ((q, exponent), limits) in enumerate(zip(windows, thresholds, strict=True)):
        x = frame_log_probs(q, exponent, center)
        logits[w, : q.shape[1]] = q.T
        grid[w, : x.shape[1]] = x.T
        per_frames = q.shape[1] if by_own_frames else FRAMES
        decision, row = decide(x, lexicon, int(limits[0]), int(limits[1]), per_frames, skip, own_parts)
        decisions.append(decision)
        scores.append(row)
    return {
        "logits": logits,
        "exponent": np.array([exponent for _, exponent in windows], dtype=np.int32),
        "log_probs": grid,
        "frames": np.array([q.shape[1] for q, _ in windows], dtype=np.int32),
        "units": units,
        "n_variants": np.array([len(forms) for forms in lexicon], dtype=np.int32),
        "n_units": n_units,
        "thresholds": np.asarray(thresholds, dtype=np.int32),
        "per_frames": np.array([FRAMES], dtype=np.int32),
        "scores": np.stack(scores),
        "decision": np.stack(decisions),
    }


def random_case(rng: np.random.Generator, lexicon: list[list[np.ndarray]]):
    """Windows of every confidence and length, a third of them saying a command, thresholds across their range."""
    windows = []
    for k in range(WINDOWS):
        frames = int(rng.integers(8, FRAMES + 1))
        exponent = int(rng.choice(LOGIT_EXPONENTS))
        if k % 3 == 0:
            forms = lexicon[int(rng.integers(len(lexicon)))]
            windows.append((said(rng, forms[int(rng.integers(len(forms)))], frames, exponent), exponent))
        else:
            spread = np.float32(rng.uniform(*SPREAD))
            logits = rng.standard_normal((n_classes(), frames)).astype(np.float32) * spread
            windows.append((quantised(logits, exponent), exponent))
    thresholds = np.stack([rng.integers(0, 3001, WINDOWS), rng.integers(0, 501, WINDOWS)], axis=1)
    return case(windows, lexicon, thresholds)


def edge_case(rng: np.random.Generator):
    """A command of a repeated unit said and said once, two equal commands, a window too short for any command,
    and a winner exactly on the reject threshold and one exactly on the margin."""
    lexicon = [[np.array([3, 3], np.uint8)], [np.array([0, 1], np.uint8)], [np.array([0, 1], np.uint8)]]
    e = LOGIT_EXPONENTS[1]
    said_units = ([3, 3], [3], [0, 1], [3], [3, 3], [3, 3])
    lengths = (24, 24, 24, 1, 30, 36)
    windows = [(said(rng, np.array(u, np.uint8), frames, e), e) for u, frames in zip(said_units, lengths, strict=True)]
    thresholds = np.full((len(windows), 2), (1000, 50), dtype=np.int32)
    thresholds[2, 1] = 1
    thresholds[4, 0] = decide(frame_log_probs(*windows[4]), lexicon, 0, 0, FRAMES)[0][3]
    thresholds[5] = (CAP, decide(frame_log_probs(*windows[5]), lexicon, 0, 0, FRAMES)[0][2])
    return case(windows, lexicon, thresholds)


def partial_windows(rng: np.random.Generator, lexicon: list[list[np.ndarray]]) -> tuple[list, np.ndarray]:
    """For the command of most syllables and the last command, windows saying its first syllable alone, its last alone,
    then the whole of it; thresholds that let every window through, so only the winner's parts turn one down."""
    longest = max(range(len(lexicon)), key=lambda c: len(syllables(lexicon[c][0])))
    e, windows = LOGIT_EXPONENTS[1], []
    for c in sorted({longest, len(lexicon) - 1}):
        cut = syllables(lexicon[c][0])
        windows += [(said(rng, units, FRAMES, e), e) for units in (cut[0], cut[-1], lexicon[c][0])]
    return windows, np.tile(np.array([CAP, 0], dtype=np.int32), (len(windows), 1))


def packed_lexicon(lexicon: list[list[np.ndarray]]) -> tuple[tuple[int, int, int], bytes]:
    """The lexicon as ai_engine's unit app reads it: variants a command, units a variant, the units padded; with its
    commands, most variants and longest variant."""
    most, longest = max(len(f) for f in lexicon), max(len(u) for f in lexicon for u in f)
    units = np.zeros((len(lexicon), most, longest), dtype=np.uint8)
    n_units = np.zeros((len(lexicon), most), dtype=np.uint8)
    for c, forms in enumerate(lexicon):
        for v, u in enumerate(forms):
            units[c, v, : len(u)], n_units[c, v] = u, len(u)
    return (len(lexicon), most, longest), bytes(len(f) for f in lexicon) + n_units.tobytes() + units.tobytes()


def probe_record(cfg: dict) -> bytes:
    """The decision of the default commands over one window of the longest LENH keeps, saying one of them, as the
    unit app of ai_engine times it on board B: DECIDE_HEAD, the packed lexicon, the frames' log-probabilities from
    the next four-byte boundary, the expected decision, every command's score."""
    spec = cfg["probe"]["decide"]
    stride = math.prod(cfg["model"]["front"]["hop_strides"])
    frames = math.ceil(listen.WINDOW_HOPS / stride)
    rng, lexicon, e = np.random.default_rng(SEED), default_lexicon(), LOGIT_EXPONENTS[1]
    window = frame_log_probs(said(rng, lexicon[0][0], frames, e), e)
    decision, scores = decide(window, lexicon, *spec["thresholds"], frames)
    (commands, most, longest), packed = packed_lexicon(lexicon)
    head = DECIDE_HEAD.pack(
        DECIDE_MAGIC, n_classes(), frames, commands, most, longest, spec["runs"], *spec["thresholds"]
    )
    body = head + packed
    body += b"\0" * (-len(body) % 4) + np.ascontiguousarray(window.T, dtype="<f4").tobytes()
    return body + DECISION_RECORD.pack(*decision.tolist()) + scores.astype("<f4").tobytes()


def emit(root: Path) -> list[Path]:
    """Random windows over the default commands and over a small set, the edge windows, parts of commands said alone,
    and four negative controls: a forward pass that never moves from one unit straight to the next, log-probabilities
    with no largest taken off, a winner whose parts are left out of its rivals, and scores divided by each window's own
    frames."""
    rng = np.random.default_rng(SEED)
    lexicon = default_lexicon()
    cases = {
        "case_000": random_case(rng, lexicon),
        "case_001": random_case(rng, lexicon[:3]),
        "case_002": edge_case(rng),
    }
    partial, limits = partial_windows(rng, lexicon)
    cases["case_003"] = case(partial, lexicon, limits)
    first = cases["case_000"]
    windows = [
        (q[:f].T.astype(np.int8), int(e))
        for q, f, e in zip(first["logits"], first["frames"], first["exponent"], strict=True)
    ]
    cases["case_neg_000"] = case(windows, lexicon, first["thresholds"], skip=False)
    cases["case_neg_001"] = case(windows, lexicon, first["thresholds"], center=False)
    cases["case_neg_002"] = case(partial, lexicon, limits, own_parts=False)
    cases["case_neg_003"] = case(windows, lexicon, first["thresholds"], by_own_frames=True)
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
