"""Wake synthesis: positives ask every engine for every voice, text, seed and speed and refuse any text but the wake
word; negatives list the corpus near misses before the hand-picked ones and read each with distinct voices."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from srpipe.core import corpus
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
    voices = len(PRESETS) * len(vieneu["preset_seeds"]) + 3 * len(vieneu["clone_seeds"])
    assert len(requests["vieneu"]) == len(vieneu["texts"]) * voices
    assert len(requests["f5"]) == len(f5["texts"]) * 3 * len(f5["seeds"]) * len(f5["speeds"])
    for reqs in requests.values():
        assert len({r["id"] for r in reqs}) == len({r["out"] for r in reqs}) == len(reqs)
        assert all(clips.spelled(r["text"]) == clips.spelled(cfg["word"]) for r in reqs)
    assert {r["speed"] for r in requests["f5"]} == set(f5["speeds"])
    assert all(r["ref_text"].startswith("câu của") for r in requests["f5"])
    assert {r["speaker"] for r in requests["vieneu"]} == {"Một", "Hai", "Ba", "A", "B", "C"}
    assert all(r["say"] == cfg["word"] for reqs in requests.values() for r in reqs)


def test_a_positive_that_is_not_the_wake_word_is_refused(tmp_path: Path) -> None:
    cfg = load_yaml(CONFIG)
    cfg["synth"]["positives"]["f5"]["texts"] = ["chào mí na"]
    with pytest.raises(ValueError, match="not the wake word"):
        synth.positive_requests(cfg, cfg["synth"]["positives"], PRESETS, refs(tmp_path), tmp_path)


def pinned() -> dict:
    """wake.yaml with the word and near-miss settings these tests were written around, whatever word is chosen."""
    cfg = load_yaml(CONFIG)
    cfg["word"] = "chào mi na"
    phrases = ["chào mi", "mi na", "mi na ơi", "chào chị na", "chào mi nhé"]
    cfg["synth"]["negatives"] |= {"misses": 3, "openings": 30, "phrases": phrases}
    cfg["synth"]["hard"] = {
        "onsets": ["m", "n", "J", "l", "b_<", "v"],
        "near_syllables": 30,
        "any_syllables": 15,
        "families": [{"text": "chào {x}", "fill": "near"}],
        "held_out": ["chào mẹ"],
        "voices": {"vieneu": 4, "f5": 3},
    }
    return cfg


def stream_of(texts: list[str]) -> tuple[np.ndarray, list[str]]:
    vocab: dict[str, int] = {}
    ids: list[int] = []
    for text in texts:
        ids += [vocab.setdefault(w, len(vocab)) for w in corpus.words(text)]
        ids.append(candidates.SEPARATOR)
    return np.array(ids, dtype=np.int32), list(vocab)


def test_negatives_come_neighbours_first_then_openings_then_the_hand_picked(tmp_path: Path) -> None:
    cfg = pinned()
    corpus = ["trào thi đua", "trào thi đua nhé", "cho mi na", "chào mọi người", "chào mọi người", "chào mi nhé"]
    stream, vocab = stream_of(corpus)
    texts = synth.negative_texts(cfg, stream, vocab, *candidates.component_codes(vocab))
    phrases = cfg["synth"]["negatives"]["phrases"]
    assert texts[:2] == ["trào thi đua", "cho mi na"]
    assert texts.index("chào mọi người") < texts.index(phrases[0])
    assert texts.count("chào mi nhé") == 1
    assert texts[-(len(phrases) - 1) :] == [p for p in phrases if p != "chào mi nhé"]


def test_no_negative_sounds_like_the_wake_word_however_it_is_spelled() -> None:
    cfg = load_yaml(CONFIG)
    cfg["word"] = "trợ lý"
    cfg["synth"]["negatives"] |= {"misses": 1, "openings": 1, "phrases": ["trợ lý ơi", "hỗ trợ"]}
    stream, vocab = stream_of(["trợ lí", "trợ lí nhé", "trợ giúp", "chị lý"])
    texts = synth.negative_texts(cfg, stream, vocab, *candidates.component_codes(vocab))
    assert texts == ["chị lý", "trợ giúp", "hỗ trợ"]


def test_each_negative_is_read_by_distinct_voices(tmp_path: Path) -> None:
    cfg = load_yaml(CONFIG)
    cfg["synth"]["negatives"]["voices"] = {"vieneu": 4, "f5": 2}
    texts = ["chào mi", "mi na ơi"]
    requests = synth.negative_requests(
        cfg, texts, PRESETS, refs(tmp_path), np.random.default_rng(0), tmp_path, cfg["synth"]["negatives"]["voices"]
    )
    assert len(requests["vieneu"]) == 4 * len(texts) and len(requests["f5"]) == 2 * len(texts)
    for reqs in requests.values():
        assert len({r["id"] for r in reqs}) == len(reqs)
        for text in texts:
            assert len({r["speaker"] for r in reqs if r["text"] == text}) == sum(r["text"] == text for r in reqs)
    assert all(r["speed"] == 1.0 and r["seed"] == 0 for r in requests["f5"])
    assert all(r["rivals"] == [cfg["word"]] and "say" not in r for reqs in requests.values() for r in reqs)


def clip(n: int, passed: bool, margin: float, rival: float, sha: str) -> dict:
    return {
        "engine": "e",
        "id": f"clone_{n}",
        "passed": passed,
        "margin": margin,
        "rivals": {"chào mi na": rival},
        "sha256": sha,
    }


def test_the_threshold_lets_the_configured_share_of_near_misses_pass_and_no_clip_repeats(tmp_path: Path) -> None:
    cfg = pinned()
    cfg["synth"]["false_accept"] = 0.1
    negatives = [clip(n, True, 0.0, float(n + 1), f"n{n}") for n in range(10)]
    negatives += [clip(10, False, 3.0, 0.5, "n10"), clip(11, True, 0.0, 9.5, "n9")]
    positives = [clip(0, True, 0.0, 0.0, "p0"), clip(1, False, 1.5, 0.0, "p1"), clip(2, False, 4.0, 0.0, "p2")]
    positives += [clip(3, True, 0.0, 0.0, "p0")]
    hard = [clip(0, True, 0.0, 5.0, "h0"), clip(1, True, 0.0, 1.0, "h1"), clip(2, True, 0.0, 9.0, "n9")]
    for name, rows in (("positives", positives), ("negatives", negatives), ("hard", hard)):
        (tmp_path / synth.SETS[name]).mkdir()
        synth.write_manifest(tmp_path / synth.SETS[name], {"clips": rows})
    margin = synth.select(cfg, tmp_path)
    assert margin == pytest.approx(2.0)
    kept = {
        s: [c["kept"] for c in yaml.safe_load((tmp_path / synth.SETS[s] / "manifest.yaml").read_text())["clips"]]
        for s in synth.SETS_KEPT
    }
    assert kept["positives"] == [True, True, False, False]
    assert kept["negatives"] == [False, False, *[True] * 8, False, False]
    assert kept["hard"] == [True, False, False]


def test_hard_families_fill_their_slots_and_never_say_the_word_or_a_held_out_phrase() -> None:
    cfg = pinned()
    cfg["synth"]["hard"] |= {
        "near_syllables": 3,
        "any_syllables": 2,
        "held_out": ["chào mẹ"],
        "families": [
            {"text": "chào {x}", "fill": "near"},
            {"text": "chào {x} na", "fill": "near"},
            {"text": "{x} mi na", "fill": "any"},
        ],
    }
    vocab = ["mẹ", "mi", "là", "tôi", "chào", "na", "có"]
    counts = {"mẹ": 9, "mi": 8, "là": 7, "tôi": 6, "chào": 5, "na": 4, "có": 1}
    stream = np.array([vocab.index(w) for w, n in counts.items() for _ in range(n)], dtype=np.int32)
    codes, tables = candidates.component_codes(vocab)
    texts = synth.hard_texts(cfg, stream, vocab, codes, tables)
    assert texts == ["chào mi", "chào là", "chào là na", "mẹ mi na", "mi mi na"]
    assert "chào mi na" not in texts and "chào mẹ" not in texts and "chào mẹ na" not in texts
    assert len(texts) == len(set(texts))
