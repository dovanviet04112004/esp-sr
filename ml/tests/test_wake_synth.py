"""Wake synthesis: the pilot asks every engine for every voice and text, and refuses any text but the wake word."""

from __future__ import annotations

from pathlib import Path

import pytest

from srpipe.core.config import load_yaml
from srpipe.tasks.wake import CONFIG, synth
from srpipe.tts import clips

PRESETS = [{"label": "Một", "id": "v1"}, {"label": "Hai", "id": "v2"}]


def refs(root: Path) -> list[clips.Reference]:
    return [clips.Reference("A", root / "a.wav", "x"), clips.Reference("B", root / "b.wav", "y")]


def test_the_pilot_asks_every_engine_for_every_voice_and_text(tmp_path: Path) -> None:
    cfg = load_yaml(CONFIG)
    requests = synth.pilot_requests(cfg, PRESETS, refs(tmp_path), tmp_path / "out")
    spec = cfg["synth"]
    assert len(requests["vieneu"]) == len(spec["vieneu"]["texts"]) * (len(PRESETS) + 2)
    assert len(requests["f5"]) == len(spec["f5"]["texts"]) * 2 * len(spec["f5"]["seeds"])
    for reqs in requests.values():
        assert len({r["id"] for r in reqs}) == len(reqs)
        assert all(clips.spelled(r["text"]) == clips.spelled(cfg["word"]) for r in reqs)
    assert all(r["ref_text"] in ("x", "y") for r in requests["f5"])
    assert {r["speaker"] for r in requests["vieneu"]} == {"Một", "Hai", "A", "B"}


def test_a_text_that_is_not_the_wake_word_is_refused(tmp_path: Path) -> None:
    cfg = load_yaml(CONFIG)
    cfg["synth"]["f5"]["texts"] = ["chào mí na"]
    with pytest.raises(ValueError, match="not the wake word"):
        synth.pilot_requests(cfg, PRESETS, refs(tmp_path), tmp_path / "out")
