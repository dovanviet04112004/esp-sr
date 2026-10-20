"""bss against answers known without a room: doa's angles per hop are given, so each rule of KEHOACH 3.8 shows alone,
the three failures tinyai-signal measured in its learning mode among them."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from srpipe.dsp.afe import bss
from srpipe.generated import grid

SHORT = {"decision_s": 2.0, "recheck_s": 0.25, "first_speech_s": 0.5, "seat_forget_s": 3.0}


def separator(**over) -> bss.Bss:
    return bss.Bss(replace(bss.BssConfig(), **(SHORT | over)))


def bins(seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((2, grid.N_BINS)) + 1j * rng.standard_normal((2, grid.N_BINS))
    return x[0].astype(np.complex64), x[1].astype(np.complex64)


def speak(
    b: bss.Bss, plan: list[tuple[int, int | None]], seed: int = 3, numbered: list | None = None
) -> list[tuple[float, int]]:
    """Hops of talkers at whole angles or of silence, None, as doa reads them: a search every other hop, the angle
    wandering a grid step either side; the gate and seats after each hop, and into numbered what numbered() says."""
    rng = np.random.default_rng(seed)
    x0, x1 = bins()
    out, hop = [], 0
    for hops, angle in plan:
        for _ in range(hops):
            searched = angle is not None and hop % 2 == 0
            reading = int(np.clip(angle + 2 * rng.integers(-1, 2), 0, 180)) if angle is not None else -1
            b.process(x0, x1, reading, 120, searched)
            out.append((float(b.gate), b.seats))
            if numbered is not None:
                numbered.append(b.numbered())
            hop += 1
    return out


def test_the_outputs_are_the_gate_and_its_rest_of_the_mean() -> None:
    b = separator()
    x0, x1 = bins()
    y0, y1 = b.process(x0, x1, 150, 120, True)
    mean = (np.float32(0.5) * (x0 + x1)).astype(np.complex64)
    np.testing.assert_array_equal(y0, (mean * b.gate).astype(np.complex64))
    np.testing.assert_allclose(y0 + y1, mean, rtol=1e-6)


def test_broadside_keeps_keep_degs_side_from_the_first_search() -> None:
    b = separator(keep_deg=135.0)
    first = speak(b, [(1, None)])
    kept = speak(b, [(40, 140)])
    gated = speak(b, [(40, 40)])
    assert first[0][0] == 0.5
    assert all(g == 1.0 for g, _ in kept[1:]) and all(g == 0.0 for g, _ in gated[2:])
    assert all(s == 0 for _, s in kept + gated)


def test_learn_splits_two_talkers_on_one_side_that_broadside_cannot() -> None:
    plan = [(120, 40), (120, 76), (120, 40), (120, 76)]
    learnt = speak(separator(mode=bss.MODE_LEARN, keep_deg=76.0), plan)
    broadside = speak(separator(keep_deg=76.0), plan)
    last_turn = slice(-100, None)
    assert learnt[-1][1] == 2
    assert np.mean([g for g, _ in learnt[-220:-120]]) < 0.05 and np.mean([g for g, _ in learnt[last_turn]]) > 0.95
    assert min(g for g, _ in broadside[120:]) > 0.9


def test_one_talker_alone_never_makes_two_seats() -> None:
    assert max(s for _, s in speak(separator(mode=bss.MODE_LEARN), [(600, 48)])) == 0


def test_a_talker_holding_the_floor_past_the_window_keeps_both_seats() -> None:
    out = speak(separator(mode=bss.MODE_LEARN, seat_forget_s=60.0), [(100, 48), (100, 130), (150, 130)])
    split = next(i for i, (_, s) in enumerate(out) if s == 2)
    assert split < 200 and all(s == 2 for _, s in out[split:])


def test_the_last_two_seats_stay_while_one_talker_holds_the_floor() -> None:
    seen: list = []
    out = speak(separator(mode=bss.MODE_LEARN), [(100, 48), (100, 130), (250, 130)], numbered=seen)
    split = next(i for i, (_, s) in enumerate(out) if s == 2)
    assert all(s == 2 for _, s in out[split:])
    assert near(seen[-1][1][0], 130) and near(seen[-1][1][1], 48)


def test_silence_ages_no_seat() -> None:
    seen: list = []
    plan = [(60, 90), (60, 30), (60, 150)] * 2 + [(250, None)] + [(60, 90), (60, 30), (60, 150)]
    out = speak(separator(mode=bss.MODE_LEARN, seats_max=3), plan, numbered=seen)
    three = next(i for i, (_, s) in enumerate(out) if s == 3)
    assert all(s == 3 for _, s in out[three:])
    assert all(near(a, b) for a, b in zip(seen[-1][1][:3], seen[three][1][:3], strict=True))


def test_a_quiet_talker_is_still_a_seat() -> None:
    """No level gate: tinyai-signal lost the quieter of two talkers 12 dB apart; here the level never enters."""
    b = separator(mode=bss.MODE_LEARN)
    speak(b, [(100, 48), (100, 130)])
    assert b.seats == 2


def test_keep_most_keeps_the_seat_with_the_most_updates() -> None:
    b = separator(mode=bss.MODE_LEARN, keep_rule=bss.KEEP_MOST, keep_deg=130.0, seat_forget_s=60.0)
    out = speak(b, [(60, 130), (300, 48)])
    assert out[-1][1] == 2 and out[-1][0] == 1.0 and abs(b.directions()[0] - 48) <= 2


@pytest.mark.parametrize(
    "over",
    [
        {"seats_max": 1},
        {"seats_max": bss.SEATS_CAP + 1},
        {"keep_deg": 200.0},
        {"first_speech_s": 9.0},
        {"bins_per_gap": 0},
        {"smooth_bins": 9},
        {"prominence": 0.0},
        {"core_share": 0.6},
        {"min_gap": 0.002},
    ],
)
def test_a_configuration_the_c_side_refuses_is_refused(over: dict) -> None:
    with pytest.raises(ValueError):
        separator(**over)


def test_reset_is_where_init_leaves_it() -> None:
    b = separator(mode=bss.MODE_LEARN)
    speak(b, [(100, 48), (100, 130)])
    b.reset()
    assert (b.gate, b.seats, b.filled, b.directions()) == (0.5, 0, 0, (-1, -1))


def near(angle: int, talker: int) -> bool:
    return abs(angle - talker) <= 4


def test_broadside_numbers_the_side_past_90_deg_0_and_the_other_1() -> None:
    b = separator(keep_deg=135.0)
    gates, angles = b.numbered()
    assert gates[:2] == [0.5, 0.5] and angles == [-1] * bss.SEATS_CAP
    speak(b, [(4, 150)])
    gates, _ = b.numbered()
    assert gates[0] == b.gate == 1.0 and gates[1] < 1e-6
    speak(b, [(4, 30)])
    gates, _ = b.numbered()
    assert gates[0] == b.gate == 0.0 and gates[1] == 1.0


def test_held_seats_keep_their_numbers_when_seats_are_taken_or_forgotten() -> None:
    b = separator(mode=bss.MODE_LEARN, seats_max=3)
    seen: list = []
    plan = [(60, 90), (60, 30), (60, 150), (60, 90), (60, 30), (60, 150), (100, 30), (100, 150), (100, 30)]
    out = speak(b, plan, numbered=seen)
    two = seen[next(i for i, (_, s) in enumerate(out) if s == 2)][1]
    three = seen[next(i for i, (_, s) in enumerate(out) if s == 3)][1]
    after = seen[-1][1]
    assert near(two[0], 90) and near(two[1], 30) and two[2:] == [-1, -1]
    assert near(three[0], 90) and near(three[1], 30) and near(three[2], 150)
    assert out[-1][1] == 2 and after[0] == -1 and near(after[1], 30) and near(after[2], 150)
    assert all(abs(sum(float(g) for g in gates) - 1.0) < 1e-6 for gates, _ in seen)
    assert all(np.float32(gate) in gates for (gate, _), (gates, _) in zip(out, seen, strict=True))


def delayed_kept(b: bss.Bss, plan: list[tuple[int, int | None]], back: int) -> np.ndarray:
    """The kept seat's gate each hop gets when it leaves back hops late, as test_apps/bss_live sends it."""
    seen: list = []
    hops = sum(n for n, _ in plan)
    x0, x1 = bins()
    rng = np.random.default_rng(3)
    hop = 0
    for n, angle in plan:
        for _ in range(n):
            searched = angle is not None and hop % 2 == 0
            reading = int(np.clip(angle + 2 * rng.integers(-1, 2), 0, 180)) if angle is not None else -1
            b.process(x0, x1, reading, 120, searched)
            if hop >= back:
                gates, kept = b.numbered_back(back)
                seen.append(float(gates[kept]))
            hop += 1
    assert len(seen) == hops - back
    return np.array(seen)


def test_back_zero_is_the_last_search_under_the_seats_held_now() -> None:
    b = separator(mode=bss.MODE_LEARN, history_s=1.0)
    gates, _ = b.numbered_back(0)
    assert gates == b.numbered()[0]
    speak(b, [(120, 40), (120, 140)])
    assert b.numbered_back(0)[0] == b.numbered()[0]
    with pytest.raises(ValueError):
        b.numbered_back(b.history + 1)


def test_delay_puts_both_first_turns_on_one_side_into_their_own_seats() -> None:
    plan = [(120, 40), (120, 76), (120, 40), (120, 76)]
    live = [g for g, _ in speak(separator(mode=bss.MODE_LEARN, keep_deg=76.0), plan)]
    back = 250
    late = delayed_kept(separator(mode=bss.MODE_LEARN, keep_deg=76.0, history_s=back * 0.016), plan, back)
    first_a, first_b = slice(20, 120), slice(140, 240)
    assert np.mean(live[first_a]) > 0.9
    assert np.mean(late[first_a]) < 0.05 and np.mean(late[first_b]) > 0.95
