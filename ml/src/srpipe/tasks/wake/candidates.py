"""Score wake word candidates on the transcripts of the speech corpora (E11-T5, KEHOACH 3.11).

For each candidate of configs/models/wake.yaml: its syllables as the north speaks them, how many tones and nuclei it
spans, how many dialect readings lang_vi gives it, and how often the corpora say it or a phrase one or two syllable
components away from it (onset, glide, nucleus, coda or tone), per million phrases of its length. The normalised
transcripts are cached under cache/wake_candidates/, so adding a candidate rescores in seconds.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import itertools
import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from srpipe.core.config import data_paths, load_yaml
from srpipe.lang import g2p, lexicon
from srpipe.lang.normalize import LangError, normalize
from srpipe.tasks.wake.quant import CONFIG

COMPONENTS = ("onset", "glide", "nucleus", "coda", "tone")
NORTH = 0
CHUNK_CHARS = 200
SEPARATOR = -1
PARQUET_CORPORA = ("fpt_open", "vlsp", "bud500")
CV_FILES = ("validated.tsv", "other.tsv")
TOP_NEIGHBOURS = 5
NEIGHBOUR_MISSES = 3
PER_MILLION = 1e6


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


def words(text: str) -> list[str]:
    """Normalised syllables of text, read in pieces short enough for normalize; a piece it refuses is dropped."""
    out: list[str] = []
    piece: list[str] = []
    for word in [*text.split(), None]:
        if word is not None and sum(map(len, piece)) + len(piece) + len(word) <= CHUNK_CHARS:
            piece.append(word)
            continue
        with contextlib.suppress(LangError):
            out += normalize(" ".join(piece)).split()
        piece = [word] if word is not None else []
    return out


def token_stream(speech: Path, cache: Path) -> tuple[np.ndarray, list[str]]:
    """Syllable ids of every transcript, SEPARATOR between transcripts, and the vocabulary; cached."""
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


def pair_per_m(pair: tuple[str, str], stream: np.ndarray, vocab: list[str]) -> float:
    """How often two syllables follow each other, per million pairs of the corpora."""
    index = {w: i for i, w in enumerate(vocab)}
    if pair[0] not in index or pair[1] not in index:
        return 0.0
    first, second = stream[:-1], stream[1:]
    pairs = int(np.sum((first != SEPARATOR) & (second != SEPARATOR)))
    return PER_MILLION * int(np.sum((first == index[pair[0]]) & (second == index[pair[1]]))) / pairs


@dataclass(frozen=True)
class Score:
    phrase: str
    reading: str
    tones: int
    nuclei: int
    readings: int
    exact_per_m: float
    near1_per_m: float
    near2_per_m: float
    near3_per_m: float
    common_pair_per_m: float
    neighbours: list[tuple[str, int]]


def score(phrase: str, stream: np.ndarray, vocab: list[str], codes: np.ndarray, tables: dict) -> Score:
    words_of = normalize(phrase).split()
    syllables = g2p.syllables(" ".join(words_of), NORTH)
    wanted = np.array([[tables[c].get(getattr(s, c), -2) for c in COMPONENTS] for s in syllables])
    n = len(syllables)
    span = len(stream) - n + 1
    valid = np.ones(span, dtype=bool)
    mismatches = np.zeros(span, dtype=np.int32)
    for k in range(n):
        ids = stream[k : k + span]
        valid &= ids != SEPARATOR
        # A syllable spelling cannot build has code -1 everywhere, so it misses on all five components.
        mismatches += np.sum(codes[ids.clip(0)] != wanted[k], axis=1)
    phrases = int(valid.sum())
    near = valid & (mismatches <= NEIGHBOUR_MISSES)
    at = np.flatnonzero(near & (mismatches > 0))
    found = Counter(" ".join(vocab[stream[i + k]] for k in range(n)) for i in at)
    return Score(
        phrase=phrase,
        reading=" | ".join(" ".join(s.units()) for s in syllables),
        tones=len({s.tone for s in syllables}),
        nuclei=len({s.nucleus for s in syllables}),
        readings=len(lexicon.entry(phrase, lexicon.ALL_DIALECTS)),
        exact_per_m=PER_MILLION * int(np.sum(valid & (mismatches == 0))) / phrases,
        near1_per_m=PER_MILLION * int(np.sum(valid & (mismatches <= 1))) / phrases,
        near2_per_m=PER_MILLION * int(np.sum(valid & (mismatches <= 2))) / phrases,
        near3_per_m=PER_MILLION * int(near.sum()) / phrases,
        common_pair_per_m=max(pair_per_m(pair, stream, vocab) for pair in itertools.pairwise(words_of)),
        neighbours=found.most_common(TOP_NEIGHBOURS),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("phrases", nargs="*", help="score these instead of the candidates of wake.yaml")
    args = parser.parse_args(argv)
    paths = data_paths()
    stream, vocab = token_stream(paths["raw"] / "speech", paths["cache"] / "wake_candidates")
    codes, tables = component_codes(vocab)
    phrases = args.phrases or load_yaml(CONFIG)["candidates"]
    scores = sorted((score(p, stream, vocab, codes, tables) for p in phrases), key=lambda s: (s.near3_per_m, -s.tones))
    print(f"{int(np.sum(stream != SEPARATOR))} syllables, {len(vocab)} distinct")
    print("phrase | tones | nuclei | readings | exact/M | <=1/M | <=2/M | <=3/M | commonest pair/M | nearest")
    for s in scores:
        near = ", ".join(f"{p} ({c})" for p, c in s.neighbours)
        print(
            f"{s.phrase} | {s.tones} | {s.nuclei} | {s.readings} | {s.exact_per_m:.2f} | {s.near1_per_m:.1f} | "
            f"{s.near2_per_m:.1f} | {s.near3_per_m:.1f} | {s.common_pair_per_m:.0f} | {near}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
