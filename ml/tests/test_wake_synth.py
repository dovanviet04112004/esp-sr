"""Wake synthesis: positives ask every engine for every voice, text, seed and speed and refuse any text but the wake
word; negatives list the corpus near misses before the hand-picked ones and read each with distinct voices."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from srpipe.core.config import load_yaml
from srpipe.tasks.wake import CONFIG, candidates, synth
from srpipe.tts import clips

PRESETS = [{"label": "Một", "id": "v1"}, {"label": "Hai", "id": "v2"}, {"label": "Ba", "id": "v3"}]


def refs(root: Path) -> list[clips.Reference]:
    return [clips.Reference(s, root / f"{s}.wav", f"câu của {s}") for s in ("A", "B", "C")]


@pytest.mark.parametrize("which", ["pilot", "positives"])
def test_positives_ask_every_engine_for_every_voice_text_seed_and_speed(tmp_path: Path, which: str) -> None:
    cfg = load_yaml(CONFIG)
    spec = cfg["synth"][which]
    requests = synth.positive_requests(cfg, spec, PRESETS, refs(tmp_path), tmp_path)
    vieneu, f5 = spec["vieneu"], spec["f5"]
    assert len(requests["vieneu"]) == len(vieneu["texts"]) * (len(PRESETS) * len(vieneu["seeds"]) + 3)
    assert len(requests["f5"]) == len(f5["texts"]) * 3 * len(f5["seeds"]) * len(f5["speeds"])
    for reqs in requests.values():
        assert len({r["id"] for r in reqs}) == len({r["out"] for r in reqs}) == len(reqs)
        assert all(clips.spelled(r["text"]) == clips.spelled(cfg["word"]) for r in reqs)
    assert {r["speed"] for r in requests["f5"]} == set(f5["speeds"])
    assert all(r["ref_text"].startswith("câu của") for r in requests["f5"])
    assert {r["speaker"] for r in requests["vieneu"]} == {"Một", "Hai", "Ba", "A", "B", "C"}


def test_a_positive_that_is_not_the_wake_word_is_refused(tmp_path: Path) -> None:
    cfg = load_yaml(CONFIG)
    cfg["synth"]["positives"]["f5"]["texts"] = ["chào mí na"]
    with pytest.raises(ValueError, match="not the wake word"):
        synth.positive_requests(cfg, cfg["synth"]["positives"], PRESETS, refs(tmp_path), tmp_path)


def stream_of(texts: list[str]) -> tuple[np.ndarray, list[str]]:
    vocab: dict[str, int] = {}
    ids: list[int] = []
    for text in texts:
        ids += [vocab.setdefault(w, len(vocab)) for w in candidates.words(text)]
        ids.append(candidates.SEPARATOR)
    return np.array(ids, dtype=np.int32), list(vocab)


def test_negatives_come_neighbours_first_then_openings_then_the_hand_picked(tmp_path: Path) -> None:
    cfg = load_yaml(CONFIG)
    corpus = ["trào thi đua", "trào thi đua nhé", "cho mi na", "chào mọi người", "chào mọi người", "chào mi nhé"]
    stream, vocab = stream_of(corpus)
    texts = synth.negative_texts(cfg, stream, vocab, *candidates.component_codes(vocab))
    phrases = cfg["synth"]["negatives"]["phrases"]
    assert texts[:2] == ["trào thi đua", "cho mi na"]
    assert texts.index("chào mọi người") < texts.index(phrases[0])
    assert texts.count("chào mi nhé") == 1
    assert texts[-(len(phrases) - 1) :] == [p for p in phrases if p != "chào mi nhé"]


def test_each_negative_is_read_by_distinct_voices(tmp_path: Path) -> None:
    cfg = load_yaml(CONFIG)
    spec = cfg["synth"]["negatives"]
    spec["voices"] = {"vieneu": 4, "f5": 2}
    texts = ["chào mi", "mi na ơi"]
    requests = synth.negative_requests(spec, texts, PRESETS, refs(tmp_path), np.random.default_rng(0), tmp_path)
    assert len(requests["vieneu"]) == 4 * len(texts) and len(requests["f5"]) == 2 * len(texts)
    for reqs in requests.values():
        assert len({r["id"] for r in reqs}) == len(reqs)
        for text in texts:
            assert len({r["speaker"] for r in reqs if r["text"] == text}) == sum(r["text"] == text for r in reqs)
    assert all(r["speed"] == 1.0 and r["seed"] == 0 for r in requests["f5"])
