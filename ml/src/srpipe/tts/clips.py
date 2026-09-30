"""Reference voices from a transcribed corpus, and synthetic clips that count only when the checker hears their text
back, tones included."""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import soundfile as sf
import yaml

from srpipe.lang.normalize import LangError, normalize
from srpipe.tts import engines

ROW_KEYS = ("speaker", "text", "say", "seed", "speed")
NOT_SPOKEN = ("id", "out", "speaker", "say", "rivals")
REFERENCES = "synth_refs"


@dataclass(frozen=True)
class Reference:
    speaker: str
    wav: Path
    text: str


def spelled(text: str) -> str:
    """Letters only, composed, lower case: 'Chào Mina!' equals 'chào mi na', and 'chào mí na' does not."""
    return re.sub(r"[\W_]", "", unicodedata.normalize("NFC", text).lower())


def speaker_references(
    corpus: Path, speakers: list[str] | None, ref_seconds: list[float], raw: Path, rejected: set[str]
) -> list[Reference]:
    """Per speaker, every one when speakers is None, the first clip whose length falls inside ref_seconds and whose
    item under raw is not rejected by screening, with its prompt as normalised text; the corpus holds
    waves/<speaker>/*.wav beside prompts.txt, as VIVOS does."""
    prompts = dict(
        line.split(" ", 1) for line in (corpus / "prompts.txt").read_text(encoding="utf-8").splitlines() if " " in line
    )
    low, high = ref_seconds
    refs = []
    for speaker in speakers or sorted(d.name for d in (corpus / "waves").iterdir() if d.is_dir()):
        for wav in sorted((corpus / "waves" / speaker).glob("*.wav")):
            if str(wav.relative_to(raw)) not in rejected and low <= sf.info(str(wav)).duration <= high:
                refs.append(Reference(speaker, wav, normalize(prompts[wav.stem])))
                break
    return refs


def parquet_references(
    name: str,
    files: list[Path],
    count: int,
    ref_seconds: list[float],
    rng: np.random.Generator,
    out: Path,
    raw: Path,
    rejected: set[str],
) -> list[Reference]:
    """count clips inside ref_seconds from Hugging Face audio parquet files under raw, written to out/<name>/ as WAV
    at their own rate. Each visit of a file takes one unused clip of a random row group and the files are visited in
    turn, so the clips spread over the corpus and its speakers. A row screening rejected, or whose transcript normalize
    refuses, is skipped."""
    low, high = ref_seconds
    used: set[tuple[int, int, int]] = set()
    spent: set[tuple[int, int]] = set()
    groups = sum(pq.ParquetFile(f).num_row_groups for f in files)
    refs: list[Reference] = []
    while len(refs) < count:
        if len(spent) == groups:
            raise ValueError(f"{name}: no clip of {low}-{high} s left to draw")
        for i in rng.permutation(len(files)):
            if len(refs) == count:
                break
            table = pq.ParquetFile(files[i])
            group = int(rng.integers(table.num_row_groups))
            first = sum(table.metadata.row_group(g).num_rows for g in range(group))
            rows = table.read_row_group(group, columns=["audio", "transcription"]).to_pylist()
            for j in rng.permutation(len(rows)):
                if (i, group, j) in used:
                    continue
                used.add((i, group, j))
                if f"{files[i].relative_to(raw)}#{first + j}" in rejected:
                    continue
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
            else:
                spent.add((i, group))
    return refs


def training_references(
    spec: dict, ref_seconds: list[float], seed: int, raw: Path, out: Path, rejected: set[str], vivos: list[str] | None
) -> list[Reference]:
    """The VIVOS train speakers of vivos (every one when None) and the parquet draws of spec, among the clips screening
    kept; the same on every run since the draws are seeded; listed in out/references.yaml, parquet clips under out."""
    rng = np.random.default_rng(seed)
    refs = speaker_references(raw / spec["vivos"], vivos, ref_seconds, raw, rejected)
    for name, count in spec["parquet"]["counts"].items():
        files = sorted((raw / "speech" / name).glob(spec["parquet"]["files"]))
        refs += parquet_references(name, files, count, ref_seconds, rng, out, raw, rejected)
    rows = [{"speaker": r.speaker, "wav": str(r.wav), "text": r.text} for r in refs]
    out.mkdir(parents=True, exist_ok=True)
    (out / "references.yaml").write_text(yaml.safe_dump(rows, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return refs


def preset_request(k: int, voice: dict, n: int, text: str, seed: int, folder: Path) -> dict:
    """Voice k of the engine's presets reading text n."""
    rid = f"preset_{k:02d}_t{n}_s{seed}"
    return {
        "id": rid,
        "speaker": voice["label"],
        "text": text,
        "seed": seed,
        "voice": voice["id"],
        "out": str(folder / f"{rid}.wav"),
    }


def clone_request(engine: str, ref: Reference, n: int, text: str, seed: int, speed: float, folder: Path) -> dict:
    """The voice of ref reading text n; F5 also takes the reference's text and a speed, VieNeu neither."""
    rid = f"clone_{ref.speaker}_t{n}_s{seed}" + (f"_v{round(speed * 100)}" if engine == "f5" else "")
    req = {
        "id": rid,
        "speaker": ref.speaker,
        "text": text,
        "seed": seed,
        "ref_audio": str(ref.wav),
        "out": str(folder / f"{rid}.wav"),
    }
    return req | {"ref_text": ref.text, "speed": speed} if engine == "f5" else req


def write_manifest(out: Path, body: dict) -> Path:
    manifest = out / "manifest.yaml"
    manifest.write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return manifest


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


def edges(wav: Path, frame_s: float, below_peak_db: float) -> dict:
    """Seconds of lead and tail quieter than below_peak_db under the loudest frame of frame_s, the peak in dBFS and
    the samples at full scale: what a listener hears as a clipped start, a cut end or distortion."""
    x, rate = sf.read(str(wav), dtype="float32")
    hop = max(1, round(frame_s * rate))
    n = len(x) // hop
    level = 10.0 * np.log10(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1) + np.finfo(np.float32).tiny)
    loud = np.flatnonzero(level >= level.max() - below_peak_db)
    peak = float(np.max(np.abs(x)))
    return {
        "lead_s": round(float(loud[0] * frame_s), 3),
        "tail_s": round(float((n - 1 - loud[-1]) * frame_s), 3),
        "peak_dbfs": round(20.0 * float(np.log10(peak + np.finfo(np.float32).tiny)), 2),
        "full_scale": int(np.sum(np.abs(x) >= 1.0 - 1.0 / 32768.0)),
    }


def render(
    requests: dict[str, list[dict]], tts: dict, work: Path, cache: Path, timings: dict[str, dict] | None = None
) -> list[dict]:
    """Synthesise every engine's requests {id, speaker, text, out, say?, rivals?, ...} whose clip is missing or was made
    from another fingerprint, chunk_clips a run, each run recorded as made when it ends so a stopped render resumes at
    the run it lost; hear every clip back, and return one manifest row per clip: heard, what the checker
    heard; passed, whether it spells what the clip must say; margin, how much more log-probability the checker gives
    what it heard than what the clip must say, near 0 when only the spelling differs; rivals, the same margin for each
    text it must not say. timings, when given, gets per engine the clips made and heard and the seconds of each."""
    rows = []
    for engine, reqs in requests.items():
        wanted = {r["out"]: fingerprint(engine, r, tts) for r in reqs}
        before = made(work)
        stale = [r for r in reqs if not Path(r["out"]).exists() or before.get(r["out"]) != wanted[r["out"]]]
        started = time.monotonic()
        stale.sort(key=engines.voice_of)
        size = tts["chunk_clips"][engine]
        for k in range(0, len(stale), size):
            engines.synthesise(engine, stale[k : k + size], tts, work, cache)
            record_made(work, {r["out"]: wanted[r["out"]] for r in stale[k : k + size]})
        synthesised = time.monotonic()
        targets = {r["id"]: [said(r), *r.get("rivals", [])] for r in reqs}
        asked = [{"id": f"{engine}/{r['id']}", "wav": r["out"], "targets": targets[r["id"]]} for r in reqs]
        heard = engines.hear(asked, tts, work, cache)
        if timings is not None:
            timings[engine] = {
                "made": len(stale),
                "made_s": round(synthesised - started, 1),
                "heard": len(reqs),
                "heard_s": round(time.monotonic() - synthesised, 1),
            }
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
