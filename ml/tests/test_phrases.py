"""Phrase search: a phrase a few syllable components away counts as a neighbour and one further away does not, and an
opening keeps the first syllable without spanning two transcripts."""

from __future__ import annotations

import numpy as np

from srpipe.core import corpus, phrases


def stream_of(texts: list[str]) -> tuple[np.ndarray, list[str]]:
    vocab: dict[str, int] = {}
    ids: list[int] = []
    for text in texts:
        ids += [vocab.setdefault(w, len(vocab)) for w in corpus.words(text)]
        ids.append(phrases.SEPARATOR)
    return np.array(ids, dtype=np.int32), list(vocab)


def test_neighbours_stop_at_the_miss_limit_and_leave_the_phrase_out() -> None:
    stream, vocab = stream_of(["ai đó gọi bí đỏ ơi nhé", "anh ấy bị bỏ rơi", "bị bỏ rơi", "chào bạn"])
    codes, tables = phrases.component_codes(vocab)
    near = phrases.neighbours("bí đỏ ơi", 3, stream, vocab, codes, tables)
    assert near == {"bị bỏ rơi": 2}
    assert phrases.neighbours("bí đỏ ơi", 1, stream, vocab, codes, tables) == {}


def test_openings_keep_the_first_syllable_and_stay_inside_a_transcript() -> None:
    stream, vocab = stream_of(["xin chào mọi người", "chào các bạn nhé", "chào mọi người", "nói chào", "chào mi na"])
    assert phrases.openings("chào mi na", stream, vocab) == {"chào mọi người": 2, "chào các bạn": 1}
    assert phrases.openings("xa lắm", stream, vocab) == {}
