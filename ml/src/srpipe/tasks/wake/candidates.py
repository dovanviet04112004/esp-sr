"""Score wake word candidates on the transcripts of the speech corpora (E11-T5, KEHOACH 3.11).

For each candidate of configs/models/wake.yaml: its syllables as the north speaks them, how many tones and nuclei it
spans, how many dialect readings lang_vi gives it, and how often the corpora say it or a phrase one or two syllable
components away from it (onset, glide, nucleus, coda or tone), per million phrases of its length. The normalised
transcripts are cached by srpipe.core.phrases, so adding a candidate rescores in seconds.
"""

from __future__ import annotations

import argparse
import itertools
from dataclasses import dataclass

import numpy as np

from srpipe.core.config import data_paths, load_yaml
from srpipe.core.corpus import NORTH
from srpipe.core.phrases import CACHE, SEPARATOR, component_codes, mismatches, neighbours, token_stream
from srpipe.lang import g2p, lexicon
from srpipe.lang.normalize import normalize
from srpipe.tasks.wake import CONFIG

TOP_NEIGHBOURS = 5
NEIGHBOUR_MISSES = 3
PER_MILLION = 1e6


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
    valid, missed = mismatches(phrase, stream, codes, tables)
    phrases = int(valid.sum())
    return Score(
        phrase=phrase,
        reading=" | ".join(" ".join(s.units()) for s in syllables),
        tones=len({s.tone for s in syllables}),
        nuclei=len({s.nucleus for s in syllables}),
        readings=len(lexicon.entry(phrase, lexicon.ALL_DIALECTS)),
        exact_per_m=PER_MILLION * int(np.sum(valid & (missed == 0))) / phrases,
        near1_per_m=PER_MILLION * int(np.sum(valid & (missed <= 1))) / phrases,
        near2_per_m=PER_MILLION * int(np.sum(valid & (missed <= 2))) / phrases,
        near3_per_m=PER_MILLION * int(np.sum(valid & (missed <= NEIGHBOUR_MISSES))) / phrases,
        common_pair_per_m=max(pair_per_m(pair, stream, vocab) for pair in itertools.pairwise(words_of)),
        neighbours=neighbours(phrase, NEIGHBOUR_MISSES, stream, vocab, codes, tables).most_common(TOP_NEIGHBOURS),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("phrases", nargs="*", help="score these instead of the candidates of wake.yaml")
    args = parser.parse_args(argv)
    paths = data_paths()
    stream, vocab = token_stream(paths["raw"] / "speech", paths["cache"] / CACHE)
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
