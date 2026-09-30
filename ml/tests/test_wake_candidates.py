"""Candidate scoring: phrases never span two transcripts, and the nearest corpus phrases come with each score."""

from __future__ import annotations

import numpy as np

from srpipe.core import corpus, phrases
from srpipe.tasks.wake import candidates


def stream_of(texts: list[str]) -> tuple[np.ndarray, list[str]]:
    vocab: dict[str, int] = {}
    ids: list[int] = []
    for text in texts:
        ids += [vocab.setdefault(w, len(vocab)) for w in corpus.words(text)]
        ids.append(phrases.SEPARATOR)
    return np.array(ids, dtype=np.int32), list(vocab)


def test_exact_near_and_far_phrases_are_told_apart() -> None:
    stream, vocab = stream_of(["ai đó gọi bí đỏ ơi nhé", "anh ấy bị bỏ rơi", "chào bạn", "bí đỏ", "ơi bạn"])
    codes, tables = phrases.component_codes(vocab)
    s = candidates.score("bí đỏ ơi", stream, vocab, codes, tables)
    assert s.exact_per_m > 0 and s.tones == 3 and s.readings == 1
    assert [p for p, _ in s.neighbours] == ["bị bỏ rơi"]
    assert "bí đỏ ơi" not in dict(s.neighbours)


def test_no_phrase_spans_two_transcripts() -> None:
    stream, vocab = stream_of(["xin chào bí đỏ", "ơi bạn"])
    codes, tables = phrases.component_codes(vocab)
    assert candidates.score("bí đỏ ơi", stream, vocab, codes, tables).exact_per_m == 0.0
    assert candidates.pair_per_m(("đỏ", "ơi"), stream, vocab) == 0.0
    assert candidates.pair_per_m(("xin", "chào"), stream, vocab) > 0.0
