"""bss LEARN over scenarios of doa as it behaves on board B (KEHOACH 3.8): readings sweep from one talker to the
next after each change, a few stray anywhere, and talkers drift, overlap or fall silent. Each scenario names the seats
it ends with and the talkers that hold one; a talker's settled readings must land in its own seat."""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pytest

from srpipe.dsp.afe import bss
from srpipe.generated import grid

HOP_S = grid.HOP_SAMPLES / grid.SAMPLE_RATE_HZ
TURN = 125  # 2 s turns
SWEEP_HOPS = 12  # doa's 0.2 s smoothing after a change of talker
JITTER_DEG = 2.0
CONFIDENCE = 120
NEAR_DEG = 6  # a seat this close to a talker is theirs
OWN_SHARE_MIN = 0.9
SEEDS = (0, 1, 2)
STRAYS = (0.03, 0.06)


@dataclass(frozen=True)
class Turn:
    hops: int
    angle: float | None  # None: silence
    to: float | None = None  # the talker drifts to this angle over the turn
    overlap: float | None = None  # a second source at this angle; readings fall on either


def track(plan: list[Turn], rng: np.random.Generator, stray: float) -> tuple[list, list]:
    """doa per hop, as the chain runs it, and whose settled speech each hop is (None: silence, sweep or overlap)."""
    out, owner = [], []
    held, prev, hop = -1, None, 0
    for t in plan:
        for i in range(t.hops):
            target = None
            if t.angle is not None:
                target = t.angle if t.to is None else t.angle + (t.to - t.angle) * i / max(1, t.hops - 1)
                if t.overlap is not None and rng.random() < 0.5:
                    target = t.overlap
            searched = target is not None and hop % 2 == 0
            if searched:
                angle = target
                if prev is not None and i < SWEEP_HOPS and t.overlap is None:
                    angle = prev + (target - prev) * (i + 1) / (SWEEP_HOPS + 1)
                angle += rng.normal(0.0, JITTER_DEG)
                if rng.random() < stray:
                    angle = rng.uniform(0.0, 180.0)
                held = int(np.clip(round(angle / 2) * 2, 0, 180))
            out.append((held, CONFIDENCE, searched))
            settled = t.angle is not None and t.overlap is None and i >= 2 * SWEEP_HOPS
            owner.append(t.angle if settled else None)
            hop += 1
        if t.angle is not None:
            prev = t.angle if t.to is None else t.to
    return out, owner


def own_share(gates: list, degrees: list, owner: list, talker: float, after: int) -> float:
    """Share of the talker's settled hops from after on whose largest gate is the seat nearest them."""
    hits = total = 0
    for h in range(after, len(owner)):
        held = [(abs(d - talker), n) for n, d in enumerate(degrees[h]) if d >= 0]
        if owner[h] != talker or not held:
            continue
        total += 1
        hits += int(np.argmax(gates[h]) == min(held)[1])
    return hits / total if total else 1.0


S = Turn
SCENARIOS = {
    "one talker drifting": ([S(1250, 48, 58)], {}, 0, []),
    "two sides": ([S(TURN, 48), S(TURN, 130)] * 6, {}, 2, [48, 130]),
    "one side": ([S(TURN, 40), S(TURN, 76)] * 6, {}, 2, [40, 76]),
    "three": ([S(TURN, 30), S(TURN, 90), S(TURN, 150)] * 5, {"seats_max": 3}, 3, [30, 90, 150]),
    "four": ([S(TURN, 30), S(TURN, 60), S(TURN, 90), S(TURN, 150)] * 5, {"seats_max": 4}, 4, [30, 60, 90, 150]),
    "a fourth joins all three": (
        [S(TURN, 30), S(TURN, 90), S(TURN, 150)] * 5 + [S(TURN, 60), S(TURN, 30), S(TURN, 90), S(TURN, 150)] * 4,
        {"seats_max": 4},
        4,
        [30, 60, 90, 150],
    ),
    "a fourth joins one": (
        [S(TURN, 30), S(TURN, 90), S(TURN, 150)] * 5 + [S(TURN, 60), S(TURN, 150)] * 8,
        {"seats_max": 4},
        4,
        [30, 60, 90, 150],
    ),
    "a fourth talks alone": (
        [S(TURN, 30), S(TURN, 90), S(TURN, 150)] * 5 + [S(1000, 60)],
        {"seats_max": 4},
        4,
        [30, 60, 90, 150],
    ),
    "two seats, three talk": ([S(TURN, 40), S(TURN, 140), S(TURN, 90)] * 6, {"seats_max": 2}, 2, [40, 140]),
    "one talks 90 %": ([S(270, 48), S(30, 130)] * 6, {}, 2, [48, 130]),
    "close, 80 and 100": ([S(TURN, 80), S(TURN, 100)] * 6, {}, 2, [80, 100]),
    "closer than min_gap": ([S(TURN, 86), S(TURN, 94)] * 6, {}, 0, []),
    "a talker drifts": ([S(TURN, 48), S(TURN, 130, 118), S(TURN, 48), S(TURN, 118, 106)] * 3, {}, 2, [48]),
    "overlaps": ([S(TURN, 48), S(TURN, 130), S(TURN, 48, overlap=130)] * 5, {}, 2, [48, 130]),
    "long pauses": ([S(TURN, 48), S(400, None), S(TURN, 130), S(400, None)] * 4, {}, 2, [48, 130]),
    "strays only": ([S(1250, None)], {}, 0, []),
    "turns of 0.5 s": ([S(31, 48), S(31, 130)] * 24, {}, 2, [48, 130]),
    "leaves, comes back": (
        [S(TURN, 48), S(TURN, 130)] * 4 + [S(TURN, 48), S(TURN, 90)] * 20 + [S(TURN, 130), S(TURN, 48)] * 4,
        {"seats_max": 3},
        3,
        [48, 90, 130],
    ),
    "three seats, four talk": ([S(TURN, 30), S(TURN, 60), S(TURN, 90), S(TURN, 150)] * 5, {"seats_max": 3}, 3, []),
    "music and a talker": ([S(TURN, 130, overlap=12), S(TURN, 12)] * 6, {}, 2, [12]),
}


@pytest.mark.parametrize("stray", STRAYS)
@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("name", list(SCENARIOS))
def test_learn_seats_every_talker_and_no_one_else(name: str, seed: int, stray: float) -> None:
    plan, over, seats_want, talkers = SCENARIOS[name]
    b = bss.Bss(replace(bss.BssConfig(), mode=bss.MODE_LEARN, **over))
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((2, grid.N_BINS)) + 1j * rng.standard_normal((2, grid.N_BINS))
    x0, x1 = x[0].astype(np.complex64), x[1].astype(np.complex64)
    readings, owner = track(plan, rng, stray)
    seats, gates, degrees = [], [], []
    for angle, confidence, searched in readings:
        b.process(x0, x1, angle, confidence, searched)
        seats.append(b.seats)
        g, d = b.numbered()
        gates.append(g)
        degrees.append(d)
    assert seats[-1] == seats_want
    held = [d for d in degrees[-1] if d >= 0]
    for talker in talkers:
        assert any(abs(d - talker) <= NEAR_DEG for d in held), f"no seat near {talker}: {held}"
        seated = next(h for h in range(len(seats)) if seats[h] == seats_want)
        assert own_share(gates, degrees, owner, talker, seated) >= OWN_SHARE_MIN
