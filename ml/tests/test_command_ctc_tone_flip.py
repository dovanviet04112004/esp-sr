"""srpipe.tasks.command.ctc.tone_flip: the places a sentence offers, never an open rhyme; units as training reads them
with each tone found; the other tone's lead takes the sign of the tone the net hears; a session's clips resynthesised
over every target and moved at one place only; the summary's shares; the step of KEHOACH 3.11 in each case."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("parselmouth")

from srpipe.core import splits  # noqa: E402
from srpipe.lang import g2p  # noqa: E402
from srpipe.lang.normalize import normalize  # noqa: E402
from srpipe.tasks.command.ctc import tone_flip  # noqa: E402
from srpipe.tasks.command.ctc.postproc.ctc_score import BLANK  # noqa: E402

NORTH = 0


def said(text: str) -> list[g2p.Syllable]:
    return g2p.syllables(normalize(text, NORTH), NORTH)


@pytest.mark.parametrize(
    ("text", "places"),
    [
        ("tắt đèn", {"first": 0}),
        ("bật đèn phòng khách", {"first": 0, "later": 3}),
        ("đèn tắt", {"later": 1}),
        ("mở cửa", {}),
        ("má", {}),
    ],
)
def test_a_sentence_offers_its_checked_sac_or_nang_syllables(text: str, places: dict) -> None:
    assert tone_flip.checked_places(said(text)) == places


@pytest.mark.parametrize(
    ("text", "places"),
    [
        ("tắt đèn", [("first", True), ("later", False)]),
        ("bật quạt", [("first", True), ("later", True)]),
        ("mở cửa đẹp", [("first", False), ("later", False), ("later", True)]),
    ],
)
def test_each_tone_is_placed_and_checked_by_the_stop_next_to_it(text: str, places: list) -> None:
    units, tone_at = tone_flip.unit_ids(said(text))
    got = tone_flip.tone_places(units)
    assert [k for k, _, _ in got] == list(tone_at)
    assert [(place, checked) for _, place, checked in got] == places


def test_units_are_trainings_with_each_syllables_tone_found() -> None:
    syllables = said("bật đèn phòng khách")
    units, tone_at = tone_flip.unit_ids(syllables)
    assert np.array_equal(units, np.array(g2p.g2p(normalize("bật đèn phòng khách", NORTH), NORTH)) + 1)
    assert [int(units[k]) - 1 for k in tone_at] == [g2p.UNIT_ID[s.tone] for s in syllables]


class FixedLogits(torch.nn.Module):
    """A net that answers every window with the same logits (1, classes, frames)."""

    chunk_multiple = 1

    def __init__(self, logits: torch.Tensor) -> None:
        super().__init__()
        self.logits = logits
        self.front = SimpleNamespace(hop_stride=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.logits[None]


def heard_net(units: np.ndarray, tone_at: int, tone_heard: str) -> tuple[SimpleNamespace, int]:
    """A net whose frames spell units with a blank after each, the tone at tone_at heard as tone_heard."""
    classes = len(g2p.UNIT_ID) + 1
    path = []
    for k, u in enumerate(units):
        path += [g2p.UNIT_ID[tone_heard] + 1 if k == tone_at else int(u), BLANK]
    logits = torch.zeros(classes, len(path))
    logits[path, torch.arange(len(path))] = 10.0
    return SimpleNamespace(model=FixedLogits(logits), mean=np.zeros(4), std=np.ones(4)), len(path)


def test_the_other_tones_lead_takes_the_sign_of_the_tone_heard() -> None:
    units, tone_at = tone_flip.unit_ids(said("tắt đèn"))
    target = tone_flip.Target(0, "first", "T5", (0.1, 0.2), np.full(10, 120.0), units, tone_at[0])
    for heard, sign in (("T5", -1.0), ("T6", 1.0)):
        net, frames = heard_net(units, tone_at[0], heard)
        lead = tone_flip.other_tone_lead(net, np.zeros((frames, 4), dtype=np.float32), target)
        assert np.sign(lead) == sign and abs(lead) > 5.0


def test_a_session_is_moved_at_one_place_and_resynthesised_at_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def recorded(x: np.ndarray, stretch: tuple, target_hz: np.ndarray | None, cfg: object) -> np.ndarray:
        calls.append((stretch, None if target_hz is None else float(target_hz[0])))
        return x + 1.0

    monkeypatch.setattr(tone_flip.repitch, "resynthesised", recorded)
    rows = [splits.Row("a.wav", "s1", "r", "public"), splits.Row("b.wav", "s2", "r", "public")]
    units = np.zeros(1, dtype=np.int64)
    targets = [
        tone_flip.Target(1, "later", "T5", (0.3, 0.4), np.full(10, 90.0), units, 0),
        tone_flip.Target(0, "later", "T6", (0.5, 0.6), np.full(10, 120.0), units, 0),
        tone_flip.Target(0, "first", "T5", (0.1, 0.2), np.full(10, 200.0), units, 0),
    ]
    dry = {0: np.zeros(4), 1: np.zeros(4)}
    expected = {
        "first": [((0.1, 0.2), 200.0), ((0.5, 0.6), None), ((0.3, 0.4), None)],
        "later": [((0.1, 0.2), None), ((0.5, 0.6), 120.0), ((0.3, 0.4), 90.0)],
        None: [((0.1, 0.2), None), ((0.5, 0.6), None), ((0.3, 0.4), None)],
    }
    for place, wanted in expected.items():
        calls.clear()
        out = tone_flip.moved_clips(dry, rows, targets, place, None)
        assert calls == wanted
        assert np.array_equal(out["a.wav"], np.full(4, 2.0)) and np.array_equal(out["b.wav"], np.ones(4))


def test_the_summary_counts_leads_per_place_and_tone() -> None:
    rows = [
        {"place": "first", "tone": "T5", "same": -1.0, "swap": 1.0, "moved": 0.5},
        {"place": "first", "tone": "T6", "same": -1.0, "swap": -0.5, "moved": 0.3},
        {"place": "first", "tone": "T6", "unheard": True},
    ]
    got = {(r["place"], r["tone"]): r for r in tone_flip.summary(rows)}
    assert got[("first", "T5")]["added"] == 1.0
    both = got[("first", "both")]
    assert both["syllables"] == 2 and both["added"] == 0.5
    assert both["moved"] == pytest.approx(0.4) and both["lead_moved"] == pytest.approx(1.25)
    assert ("later", "both") not in got


def row(place: str, moved: float, added: float) -> dict:
    return {"place": place, "tone": "both", "moved": moved, "added": added}


@pytest.mark.parametrize(
    ("first", "later", "step"),
    [
        (row("first", 1.0, 0.4), row("later", 1.0, 0.0), "no later syllable follows"),
        (row("first", 0.3, 0.0), row("later", 1.0, 0.5), "hardly follow"),
        (row("first", 0.8, 0.3), row("later", 1.0, 0.5), "the net follows F0"),
        (row("first", 0.8, 0.1), row("later", 1.0, 0.5), "with the tone swap"),
    ],
)
def test_the_step_of_the_plan_follows_the_first_syllable_against_a_later_one(first, later, step) -> None:
    assert step in tone_flip.branch([first, later], 0.5)


def test_an_utterance_counts_through_the_one_window_that_holds_it_alone() -> None:
    spans = [(10, 20), (30, 60), (80, 90)]
    said = [(12, 18), (32, 40), (50, 58), (82, 88)]
    assert tone_flip.owned_windows(spans, said, set()) == {0: 0, 3: 2}
    assert tone_flip.owned_windows(spans, said, {3}) == {0: 0}


def test_a_session_is_moved_on_every_microphone_and_comes_back_as_int16(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def recorded(x: np.ndarray, stretch: tuple, target_hz: np.ndarray | None, cfg: object) -> np.ndarray:
        calls.append((stretch, None if target_hz is None else float(target_hz[0])))
        return x * 2.0

    monkeypatch.setattr(tone_flip.repitch, "resynthesised", recorded)
    pcm = np.full((8, 2), 1000, dtype=np.int16)
    spoken = [tone_flip.Spoken("s", 1, "T6", (0.3, 0.4)), tone_flip.Spoken("s", 0, "T5", (0.1, 0.2))]
    out = tone_flip.moved_session(pcm, spoken, {("s", 0): np.full(10, 150.0)}, None)
    assert calls == [((0.1, 0.2), 150.0), ((0.1, 0.2), 150.0), ((0.3, 0.4), None), ((0.3, 0.4), None)]
    assert out.dtype == np.int16 and np.all(out == 4000)
    assert np.all(tone_flip.moved_session(np.full((8, 2), 20000, dtype=np.int16), spoken, {}, None) == 32767)


def test_the_units_heard_are_matched_to_the_said_ones_by_edit_distance() -> None:
    assert tone_flip.matched([1, 2, 3], np.array([1, 2, 3])) == [1, 2, 3]
    assert tone_flip.matched([1, 9, 3], np.array([1, 2, 3])) == [1, 9, 3]
    assert tone_flip.matched([1, 3], np.array([1, 2, 3])) == [1, None, 3]


def test_a_held_pitch_dim_reads_its_train_mean_and_nothing_else_moves() -> None:
    x = np.arange(12, dtype=np.float32).reshape(2, 6)
    mean = np.full(6, -1.0, dtype=np.float32)
    voicing = tone_flip.held(x, tone_flip.HOLDS["voicing"], mean)
    assert np.all(voicing[:, 3] == -1.0) and np.array_equal(np.delete(voicing, 3, axis=1), np.delete(x, 3, axis=1))
    f0 = tone_flip.held(x, tone_flip.HOLDS["f0"], mean)
    assert np.all(f0[:, 4:] == -1.0) and np.array_equal(f0[:, :4], x[:, :4])
    assert np.array_equal(tone_flip.held(x, (), mean), x) and x[0, 3] == 3.0


def test_each_window_is_decided_with_the_pitch_dims_asked_held(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    def heard(net: object, x: np.ndarray) -> str:
        seen.append(x.copy())
        return "heard"

    monkeypatch.setattr(tone_flip.gate, "ctc_heard", heard)
    net = SimpleNamespace(mean=np.full(6, -1.0, dtype=np.float32))
    x = np.arange(12, dtype=np.float32).reshape(2, 6)
    assert tone_flip.decide(net, {("s", 0): x}) == {("s", 0): "heard"}
    tone_flip.decide(net, {("s", 0): x}, tone_flip.HOLDS["f0"])
    assert np.array_equal(seen[0], x)
    assert np.all(seen[1][:, 4:] == -1.0) and np.array_equal(seen[1][:, :4], x[:, :4])
