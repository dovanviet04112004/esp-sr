"""Candidate scoring: long transcripts are read in pieces, phrases never span two transcripts, a phrase a few
syllable components away counts as a neighbour and one further away does not."""

from __future__ import annotations

import numpy as np

from srpipe.tasks.wake import candidates


def stream_of(texts: list[str]) -> tuple[np.ndarray, list[str]]:
    vocab: dict[str, int] = {}
    ids: list[int] = []
    for text in texts:
        ids += [vocab.setdefault(w, len(vocab)) for w in candidates.words(text)]
        ids.append(candidates.SEPARATOR)
    return np.array(ids, dtype=np.int32), list(vocab)


def test_a_transcript_longer_than_normalize_takes_is_read_whole() -> None:
    text = " ".join(["một hai ba bốn năm"] * 30)
    assert len(text) > candidates.CHUNK_CHARS
    assert candidates.words(text) == ["một", "hai", "ba", "bốn", "năm"] * 30


def test_exact_near_and_far_phrases_are_told_apart() -> None:
    stream, vocab = stream_of(["ai đó gọi bí đỏ ơi nhé", "anh ấy bị bỏ rơi", "chào bạn", "bí đỏ", "ơi bạn"])
    codes, tables = candidates.component_codes(vocab)
    s = candidates.score("bí đỏ ơi", stream, vocab, codes, tables)
    assert s.exact_per_m > 0 and s.tones == 3 and s.readings == 1
    assert [p for p, _ in s.neighbours] == ["bị bỏ rơi"]
    assert "bí đỏ ơi" not in dict(s.neighbours)


def test_no_phrase_spans_two_transcripts() -> None:
    stream, vocab = stream_of(["xin chào bí đỏ", "ơi bạn"])
    codes, tables = candidates.component_codes(vocab)
    assert candidates.score("bí đỏ ơi", stream, vocab, codes, tables).exact_per_m == 0.0
    assert candidates.pair_per_m(("đỏ", "ơi"), stream, vocab) == 0.0
    assert candidates.pair_per_m(("xin", "chào"), stream, vocab) > 0.0
