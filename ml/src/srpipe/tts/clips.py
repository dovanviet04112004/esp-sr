"""Reference voices from a transcribed corpus, and synthetic clips that count only when the checker hears their text
back, tones included."""

from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import soundfile as sf

from srpipe.lang.normalize import LangError, normalize
from srpipe.tts import engines

ROW_KEYS = ("speaker", "text", "say", "seed", "speed")
NOT_SPOKEN = ("id", "out", "speaker", "say", "rivals")


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
    """count clips inside ref_seconds from Hugging Face audio parquet files, written to out/<name>/ as WAV at their own
    rate. Each visit of a file takes one unused clip of a random row group and the files are visited in turn, so the
    clips spread over the corpus and its speakers. A transcript normalize refuses is skipped."""
    low, high = ref_seconds
    used: set[tuple[int, int, int]] = set()
    refs: list[Reference] = []
    while len(refs) < count:
        found = len(refs)
        for i in rng.permutation(len(files)):
            if len(refs) == count:
                break
            table = pq.ParquetFile(files[i])
            group = int(rng.integers(table.num_row_groups))
            rows = table.read_row_group(group, columns=["audio", "transcription"]).to_pylist()
            for j in rng.permutation(len(rows)):
                if (i, group, j) in used:
                    continue
                used.add((i, group, j))
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
                break
        if len(refs) == found:
            raise ValueError(f"{name}: no clip of {low}-{high} s left to draw")
    return refs


def said(request: dict) -> str:
    """What a clip must say: its say field, or the text it was read from."""
    return request.get("say", request["text"])


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(engine: str, request: dict, tts: dict) -> str:
    """What a clip is made from: the engine's pins, run.py and lock, and every request field that reaches the engine,
    a reference clip by its bytes rather than its path."""
    project = engines.PROJECTS / engine
    fields = {k: v for k, v in request.items() if k not in NOT_SPOKEN}
    if "ref_audio" in fields:
        fields["ref_audio"] = sha256_of(Path(fields["ref_audio"]))
    made_of = json.dumps({"engine": tts["engines"][engine], "request": fields}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(
        made_of.encode() + (project / "run.py").read_bytes() + (project / "uv.lock").read_bytes()
    ).hexdigest()


def made(work: Path) -> dict[str, str]:
    """The fingerprint each clip was last made from, by its path; work/made.jsonl."""
    listing = work / "made.jsonl"
    if not listing.exists():
        return {}
    return {r["out"]: r["fingerprint"] for r in map(json.loads, listing.read_text(encoding="utf-8").splitlines())}


def record_made(work: Path, fingerprints: dict[str, str]) -> None:
    work.mkdir(parents=True, exist_ok=True)
    with (work / "made.jsonl").open("a", encoding="utf-8") as f:
        f.writelines(json.dumps({"out": out, "fingerprint": fp}) + "\n" for out, fp in fingerprints.items())


def render(requests: dict[str, list[dict]], tts: dict, work: Path, cache: Path) -> list[dict]:
    """Synthesise every engine's requests {id, speaker, text, out, say?, rivals?, ...} whose clip is missing or was made
    from another fingerprint, hear every clip back, and return one manifest row per clip: heard, what the checker
    heard; passed, whether it spells what the clip must say; margin, how much more log-probability the checker gives
    what it heard than what the clip must say, near 0 when only the spelling differs; rivals, the same margin for each
    text it must not say."""
    rows = []
    for engine, reqs in requests.items():
        wanted = {r["out"]: fingerprint(engine, r, tts) for r in reqs}
        before = made(work)
        stale = [r for r in reqs if not Path(r["out"]).exists() or before.get(r["out"]) != wanted[r["out"]]]
        if stale:
            engines.synthesise(engine, stale, tts, work, cache)
            record_made(work, {r["out"]: wanted[r["out"]] for r in stale})
        targets = {r["id"]: [said(r), *r.get("rivals", [])] for r in reqs}
        asked = [{"id": f"{engine}/{r['id']}", "wav": r["out"], "targets": targets[r["id"]]} for r in reqs]
        heard = engines.hear(asked, tts, work, cache)
        for r in reqs:
            wav = Path(r["out"])
            h = heard[f"{engine}/{r['id']}"]
            gap = {t: round(h["logp"] - p, 3) for t, p in h["targets"].items()}
            rows.append(
                {
                    "engine": engine,
                    "id": r["id"],
                    **{k: r[k] for k in ROW_KEYS if k in r},
                    "heard": h["text"],
                    "passed": spelled(h["text"]) == spelled(said(r)),
                    "margin": gap[said(r)],
                    **({"rivals": {t: gap[t] for t in r["rivals"]}} if "rivals" in r else {}),
                    "seconds": round(sf.info(str(wav)).duration, 3),
                    "sha256": sha256_of(wav),
                }
            )
    return rows
