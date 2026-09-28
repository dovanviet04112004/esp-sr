"""Reference voices from a transcribed corpus, and synthetic clips that count only when the checker hears their text
back, tones included."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf

from srpipe.lang.normalize import normalize
from srpipe.tts import engines


@dataclass(frozen=True)
class Reference:
    speaker: str
    wav: Path
    text: str


def spelled(text: str) -> str:
    """Letters only, composed, lower case: 'Chào Mina!' equals 'chào mi na', and 'chào mí na' does not."""
    return re.sub(r"[\W_]", "", unicodedata.normalize("NFC", text).lower())


def references(corpus: Path, speakers: list[str], ref_seconds: list[float]) -> list[Reference]:
    """Per speaker, the first clip whose length falls inside ref_seconds, with its prompt as normalised text; the corpus
    holds waves/<speaker>/*.wav beside prompts.txt, as VIVOS does."""
    prompts = dict(
        line.split(" ", 1) for line in (corpus / "prompts.txt").read_text(encoding="utf-8").splitlines() if " " in line
    )
    low, high = ref_seconds
    refs = []
    for speaker in speakers:
        for wav in sorted((corpus / "waves" / speaker).glob("*.wav")):
            if low <= sf.info(str(wav)).duration <= high:
                refs.append(Reference(speaker, wav, normalize(prompts[wav.stem])))
                break
    return refs


def render(requests: dict[str, list[dict]], tts: dict, work: Path, cache: Path) -> list[dict]:
    """Synthesise every engine's requests {id, speaker, text, out, ...} and hear each clip back; one manifest row per
    clip, passed when the checker spells back the requested text."""
    rows = []
    for engine, reqs in requests.items():
        engines.synthesise(engine, reqs, tts, work, cache)
        heard = engines.hear([{"id": f"{engine}/{r['id']}", "wav": r["out"]} for r in reqs], tts, work, cache)
        for r in reqs:
            wav = Path(r["out"])
            text = heard[f"{engine}/{r['id']}"]
            rows.append(
                {
                    "engine": engine,
                    "id": r["id"],
                    "speaker": r["speaker"],
                    "text": r["text"],
                    "heard": text,
                    "passed": spelled(text) == spelled(r["text"]),
                    "seconds": round(sf.info(str(wav)).duration, 3),
                    "sha256": hashlib.sha256(wav.read_bytes()).hexdigest(),
                }
            )
    return rows
