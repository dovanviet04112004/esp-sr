"""Every clip of a corpus under raw/, named as a split names it (KEHOACH 4.4.1): a file under raw/, or
<parquet under raw/>#<row> for a corpus packed as Hugging Face audio parquet. Each corpus declares its layout in
configs/common/screen.yaml."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq

from srpipe.lang.normalize import LangError, normalize

AUDIO_SUFFIXES = (".wav", ".flac", ".mp3")
TEXT_COLUMN = "transcription"
CHUNK_CHARS = 200


@dataclass(frozen=True)
class Clip:
    item: str
    speaker: str | None = None
    text: str | None = None


def common_voice(root: Path, raw: Path, spec: dict) -> Iterator[Clip]:
    """The clips of spec's lists (validated.tsv, other.tsv, ...) in each release directory, speaker the client id."""
    for release in sorted(root.glob(spec["dirs"])):
        for name in spec["lists"]:
            with (release / name).open(encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
                    item = (release / "clips" / row["path"]).relative_to(raw)
                    yield Clip(str(item), row["client_id"], row["sentence"])


def vivos(root: Path, raw: Path, spec: dict) -> Iterator[Clip]:
    """<part>/waves/<speaker>/*.wav beside <part>/prompts.txt of '<id> <text>' lines, for each part of spec."""
    for part in spec["parts"]:
        lines = (root / part / "prompts.txt").read_text(encoding="utf-8").splitlines()
        prompts = dict(line.split(" ", 1) for line in lines if " " in line)
        for wav in sorted((root / part / "waves").glob("*/*.wav")):
            yield Clip(str(wav.relative_to(raw)), wav.parent.name, prompts.get(wav.stem))


def parquet(root: Path, raw: Path, spec: dict) -> Iterator[Clip]:
    """Every row of the parquet files spec matches, with its transcription; these corpora carry no speaker."""
    for path in sorted(root.glob(spec["files"])):
        name = path.relative_to(raw)
        for row, text in enumerate(pq.read_table(path, columns=[TEXT_COLUMN]).column(0).to_pylist()):
            yield Clip(f"{name}#{row}", None, text)


def files(root: Path, raw: Path, spec: dict) -> Iterator[Clip]:
    """Every audio file under the corpus, untranscribed: noise and room impulse responses."""
    for path in sorted(p for p in root.rglob("*") if p.suffix.lower() in AUDIO_SUFFIXES):
        yield Clip(str(path.relative_to(raw)))


LAYOUTS = {"common_voice": common_voice, "vivos": vivos, "parquet": parquet, "files": files}


def clips(raw: Path, kind: str, name: str, spec: dict) -> list[Clip]:
    """Every clip of raw/<kind>/<name> in a fixed order, read by the layout spec names."""
    return list(LAYOUTS[spec["layout"]](raw / kind / name, raw, spec))


def read_text(text: str) -> tuple[list[str], int]:
    """Normalised syllables of text, read in pieces short enough for normalize, and how many pieces it refused."""
    out: list[str] = []
    refused = 0
    piece: list[str] = []
    for word in [*text.split(), None]:
        if word is not None and sum(map(len, piece)) + len(piece) + len(word) <= CHUNK_CHARS:
            piece.append(word)
            continue
        if piece:
            try:
                out += normalize(" ".join(piece)).split()
            except LangError:
                refused += 1
        piece = [word] if word is not None else []
    return out, refused


def words(text: str) -> list[str]:
    """Normalised syllables of text; a piece normalize refuses is dropped."""
    return read_text(text)[0]
