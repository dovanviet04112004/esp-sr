"""Shared TTS: the checker compares spelling with tones, references are the first clip in the span, and a rendered clip
passes only when its own text is heard back."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from srpipe.core.audio_io import write_wav
from srpipe.core.config import load_yaml
from srpipe.generated import grid
from srpipe.tts import CONFIG, PROJECTS, clips, engines


def test_spelling_keeps_tones_and_drops_case_space_and_punctuation() -> None:
    assert clips.spelled("Chào Mina!") == clips.spelled("chào mi na")
    assert clips.spelled("chào mí na") != clips.spelled("chào mi na")
    assert clips.spelled("chao mi na") != clips.spelled("chào mi na")


def fake_corpus(root: Path) -> Path:
    corpus = root / "c"
    lines = []
    for spk, seconds in (("A", (2.0, 5.0, 6.0)), ("B", (9.0, 4.5))):
        for n, s in enumerate(seconds):
            write_wav(corpus / "waves" / spk / f"{spk}_{n}.wav", np.zeros(round(s * grid.SAMPLE_RATE_HZ)))
            lines.append(f"{spk}_{n} CÂU SỐ {n}")
    (corpus / "prompts.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return corpus


def test_the_reference_is_the_first_clip_inside_the_span(tmp_path: Path) -> None:
    refs = clips.references(fake_corpus(tmp_path), ["A", "B"], [4.0, 7.5])
    assert [(r.speaker, r.wav.name, r.text) for r in refs] == [
        ("A", "A_1.wav", "câu số một"),
        ("B", "B_1.wav", "câu số một"),
    ]


def test_every_engine_and_the_checker_has_a_pinned_project() -> None:
    tts = load_yaml(CONFIG)
    for name in [*tts["engines"], "asr"]:
        assert (PROJECTS / name / "run.py").is_file()
        assert (PROJECTS / name / "uv.lock").is_file()
    pins = [tts["asr"], *(v for spec in tts["engines"].values() for v in spec.values())]
    assert all(len(pin.split("@")[1]) == 40 for pin in pins)


def test_a_clip_passes_only_when_its_own_text_is_heard(tmp_path: Path, monkeypatch) -> None:
    requests = {
        "e": [
            {"id": "a", "speaker": "A", "text": "Chào Mina!", "out": str(tmp_path / "a.wav")},
            {"id": "b", "speaker": "B", "text": "chào mi na", "out": str(tmp_path / "b.wav")},
        ]
    }

    def synthesise(engine, reqs, tts, work, cache):
        for r in reqs:
            write_wav(Path(r["out"]), np.zeros(grid.SAMPLE_RATE_HZ))

    monkeypatch.setattr(engines, "synthesise", synthesise)
    monkeypatch.setattr(engines, "hear", lambda c, *a: {"e/a": "chào mi na.", "e/b": "chào mí na"})
    rows = clips.render(requests, {}, tmp_path / "work", tmp_path)
    assert [(r["id"], r["speaker"], r["passed"]) for r in rows] == [("a", "A", True), ("b", "B", False)]
    assert rows[0]["seconds"] == 1.0
