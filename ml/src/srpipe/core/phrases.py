"""Phrase search over the transcripts of every speech corpus, for any branch's near misses (KEHOACH 3.11, 3.12).

The transcripts become one stream of syllable ids with a separator between transcripts, cached; each syllable gets
codes for its five components in the north reading, so a phrase a few components away from another is found by
comparing codes, and a phrase never spans two transcripts.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from srpipe.core.corpus import NORTH, words
from srpipe.lang import g2p
from srpipe.lang.normalize import LangError, normalize

COMPONENTS = ("onset", "glide", "nucleus", "coda", "tone")
SEPARATOR = -1
PARQUET_CORPORA = ("fpt_open", "vlsp", "bud500")
CV_FILES = ("validated.tsv", "other.tsv")
CACHE = "phrases"


def transcripts(speech: Path) -> Iterator[str]:
    """Every transcript line of Common Voice (clips validated or pending), VIVOS, FPT, VLSP and Bud500."""
    for cv_dir in sorted((speech / "common_voice_vi").glob("cv-corpus-*/vi")):
        for name in CV_FILES:
            with (cv_dir / name).open(encoding="utf-8", newline="") as f:
                yield from (row["sentence"] for row in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE))
    for part in ("train", "test"):
        for line in (speech / "vivos" / part / "prompts.txt").read_text(encoding="utf-8").splitlines():
            yield line.split(" ", 1)[1] if " " in line else ""
    for corpus in PARQUET_CORPORA:
        for path in sorted((speech / corpus / "data").glob("*.parquet")):
            yield from pq.read_table(path, columns=["transcription"]).column(0).to_pylist()


def token_stream(speech: Path, cache: Path) -> tuple[np.ndarray, list[str]]:
    """Syllable ids of every transcript, SEPARATOR between transcripts, and the vocabulary; cached under cache."""
    ids_file, vocab_file = cache / "tokens.npy", cache / "vocab.json"
    if ids_file.exists() and vocab_file.exists():
        return np.load(ids_file), json.loads(vocab_file.read_text(encoding="utf-8"))
    vocab: dict[str, int] = {}
    ids: list[int] = []
    for text in transcripts(speech):
        ids += [vocab.setdefault(w, len(vocab)) for w in words(text or "")]
        ids.append(SEPARATOR)
    cache.mkdir(parents=True, exist_ok=True)
    stream = np.array(ids, dtype=np.int32)
    np.save(ids_file, stream)
    vocab_file.write_text(json.dumps(list(vocab), ensure_ascii=False), encoding="utf-8")
    return stream, list(vocab)


def component_codes(vocab: list[str]) -> tuple[np.ndarray, dict[str, dict[str, int]]]:
    """(len(vocab), 5) codes of each syllable's components in the north reading, -1 where spelling cannot build it."""
    tables: dict[str, dict[str, int]] = {c: {} for c in COMPONENTS}
    codes = np.full((len(vocab), len(COMPONENTS)), -1, dtype=np.int32)
    for i, word in enumerate(vocab):
        try:
            (syllable,) = g2p.syllables(word, NORTH)
        except (LangError, ValueError):
            continue
        codes[i] = [tables[c].setdefault(getattr(syllable, c), len(tables[c])) for c in COMPONENTS]
    return codes, tables


def mismatches(phrase: str, stream: np.ndarray, codes: np.ndarray, tables: dict) -> tuple[np.ndarray, np.ndarray]:
    """For every start in the stream, whether a phrase of the same length fits there and how many syllable components
    of it differ from phrase in the north reading."""
    syllables = g2p.syllables(normalize(phrase), NORTH)
    wanted = np.array([[tables[c].get(getattr(s, c), -2) for c in COMPONENTS] for s in syllables])
    n = len(syllables)
    span = len(stream) - n + 1
    valid = np.ones(span, dtype=bool)
    missed = np.zeros(span, dtype=np.int32)
    for k in range(n):
        ids = stream[k : k + span]
        valid &= ids != SEPARATOR
        # A syllable spelling cannot build has code -1 everywhere, so it misses on all five components.
        missed += np.sum(codes[ids.clip(0)] != wanted[k], axis=1)
    return valid, missed


def phrases_at(starts: np.ndarray, length: int, stream: np.ndarray, vocab: list[str]) -> Counter:
    return Counter(" ".join(vocab[stream[i + k]] for k in range(length)) for i in starts)


def neighbours(
    phrase: str, misses: int, stream: np.ndarray, vocab: list[str], codes: np.ndarray, tables: dict
) -> Counter:
    """Every corpus phrase one to misses syllable components away from phrase, with its count."""
    valid, missed = mismatches(phrase, stream, codes, tables)
    at = np.flatnonzero(valid & (missed > 0) & (missed <= misses))
    return phrases_at(at, len(normalize(phrase).split()), stream, vocab)


def openings(phrase: str, stream: np.ndarray, vocab: list[str]) -> Counter:
    """Every corpus phrase as long as phrase that opens with its first syllable, phrase itself left out."""
    words_of = normalize(phrase).split()
    if words_of[0] not in vocab:
        return Counter()
    n = len(words_of)
    first = np.flatnonzero(stream[: len(stream) - n + 1] == vocab.index(words_of[0]))
    at = first[np.all([stream[first + k] != SEPARATOR for k in range(n)], axis=0)]
    found = phrases_at(at, n, stream, vocab)
    found.pop(" ".join(words_of), None)
    return found
