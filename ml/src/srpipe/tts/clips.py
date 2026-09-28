"""Reference voices from a transcribed corpus, and synthetic clips that count only when the checker hears their text
back, tones included."""

from __future__ import annotations

import hashlib
import io
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import soundfile as sf

from srpipe.lang.normalize import LangError, normalize
from srpipe.tts import engines

ROW_KEYS = ("speaker", "text", "seed", "speed")


@dataclass(frozen=True)
class Reference:
    speaker: str
    wav: Path
    text: str


def spelled(text: str) -> str:
    """Letters only, composed, lower case: 'Chào Mina!' equals 'chào mi na', and 'chào mí na' does not."""
    return re.sub(r"[\W_]", "", unicodedata.normalize("NFC", text).lower())


def speaker_references(corpus: Path, speakers: list[str] | None, ref_seconds: list[float]) -> list[Reference]:
    """Per speaker, every one when speakers is None, the first clip whose length falls inside ref_seconds, with its
    prompt as normalised text; the corpus holds waves/<speaker>/*.wav beside prompts.txt, as VIVOS does."""
    prompts = dict(
        line.split(" ", 1) for line in (corpus / "prompts.txt").read_text(encoding="utf-8").splitlines() if " " in line
    )
    low, high = ref_seconds
    refs = []
    for speaker in speakers or sorted(d.name for d in (corpus / "waves").iterdir() if d.is_dir()):
        for wav in sorted((corpus / "waves" / speaker).glob("*.wav")):
            if low <= sf.info(str(wav)).duration <= high:
                refs.append(Reference(speaker, wav, normalize(prompts[wav.stem])))
                break
    return refs


def parquet_references(
    name: str, files: list[Path], count: int, ref_seconds: list[float], rng: np.random.Generator, out: Path
) -> list[Reference]:
    """count clips inside ref_seconds from Hugging Face audio parquet files, each file visited in turn so the clips
    spread over the corpus, written to out/<name>/ as WAV at their own rate. A transcript normalize refuses is
    skipped."""
    low, high = ref_seconds
    per_visit = math.ceil(count / len(files))
    refs: list[Reference] = []
    while len(refs) < count:
        found = len(refs)
        for i in rng.permutation(len(files)):
            table = pq.ParquetFile(files[i])
            group = int(rng.integers(table.num_row_groups))
            rows = table.read_row_group(group, columns=["audio", "transcription"]).to_pylist()
            taken = 0
            for j in rng.permutation(len(rows)):
                if taken == per_visit or len(refs) == count:
                    break
                audio, rate = sf.read(io.BytesIO(rows[j]["audio"]["bytes"]), dtype="int16")
                if not low <= len(audio) / rate <= high:
                    continue
                try:
                    text = normalize(rows[j]["transcription"])
                except LangError:
                    continue
                speaker = f"{name}_{files[i].stem.split('-')[1]}_{group:03d}_{j:05d}"
                wav = out / name / f"{speaker}.wav"
                wav.parent.mkdir(parents=True, exist_ok=True)
                sf.write(str(wav), audio, rate, subtype="PCM_16")
                refs.append(Reference(speaker, wav, text))
                taken += 1
            if len(refs) == count:
                break
        if len(refs) == found:
            raise ValueError(f"{name}: no clip of {low}-{high} s left to draw")
    return refs


def render(requests: dict[str, list[dict]], tts: dict, work: Path, cache: Path) -> list[dict]:
    """Synthesise every engine's requests {id, speaker, text, out, ...} whose clip is not there yet, hear every clip
    back, and return one manifest row per clip, passed when the checker spells back the requested text."""
    rows = []
    for engine, reqs in requests.items():
        missing = [r for r in reqs if not Path(r["out"]).exists()]
        if missing:
            engines.synthesise(engine, missing, tts, work, cache)
        heard = engines.hear([{"id": f"{engine}/{r['id']}", "wav": r["out"]} for r in reqs], tts, work, cache)
        for r in reqs:
            wav = Path(r["out"])
            text = heard[f"{engine}/{r['id']}"]
            rows.append(
                {
                    "engine": engine,
                    "id": r["id"],
                    **{k: r[k] for k in ROW_KEYS if k in r},
                    "heard": text,
                    "passed": spelled(text) == spelled(r["text"]),
                    "seconds": round(sf.info(str(wav)).duration, 3),
                    "sha256": hashlib.sha256(wav.read_bytes()).hexdigest(),
                }
            )
    return rows
