"""Long rows of a corpus cut at word bounds into spans a training window holds (KEHOACH 1.2): a corpus of screen.yaml
with layout spans names its rows' layout under rows; rows longer than max_s are aligned word by word and cut in silences
of at least min_gap_s into the longest runs within max_s, shorter rows kept whole; each span decoded once into a 16 kHz
FLAC under interim/spans/, its item, speaker and text listed in interim/spans/<name>.tsv.
Aligned batches and written clips are kept, so a stopped run goes on. Usage: python -m srpipe.core.spans <name>"""

from __future__ import annotations

import argparse
import csv
import multiprocessing
import shutil
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf

from srpipe.core import audio_io, corpus, screen
from srpipe.core.config import data_paths, load_yaml
from srpipe.generated import grid
from srpipe.tts import CONFIG as TTS_CONFIG
from srpipe.tts import engines

FIELDS = ("item", "speaker", "text")


def runs(words: list[dict], max_s: float, min_gap_s: float, margin_s: float, end_s: float) -> list[tuple]:
    """Start, end and words of each run of aligned words {word, start, end} of a row end_s long: a run ends only at a
    silence of at least min_gap_s or the row's last word, is as long as max_s allows, and reaches margin_s into the
    silence on each side, never past half of it; a stretch with no such end within max_s is left out."""
    n = len(words)
    after = [words[k + 1]["start"] - words[k]["end"] for k in range(n - 1)] + [end_s - words[-1]["end"]]
    before = [words[0]["start"], *after[:-1]]
    ends_here = [k == n - 1 or after[k] >= min_gap_s for k in range(n)]
    out, i = [], 0
    while i < n:
        fits = [j for j in range(i, n) if ends_here[j] and words[j]["end"] - words[i]["start"] <= max_s]
        if not fits:
            i = next(k + 1 for k in range(i, n) if ends_here[k])
            continue
        j = fits[-1]
        start = max(0.0, words[i]["start"] - min(margin_s, before[i] / 2))
        end = min(end_s, words[j]["end"] + min(margin_s, after[j] / 2))
        out.append((start, end, [w["word"] for w in words[i : j + 1]]))
        i = j + 1
    return out


@dataclass
class Prepared:
    """A batch of numbered rows read for the aligner: its rows within max_s as spans, its longer rows as WAV in wavs."""

    whole: list[tuple[int, dict]]
    asked: list[dict]
    long_rows: dict[str, tuple[int, corpus.Clip, list[str], float]]
    wavs: Path


def prepared(batch: list[tuple[int, corpus.Clip]], spec: dict, wavs: Path, raw: Path) -> Prepared:
    """batch read from raw/: rows within max_s kept whole, the longer written to wavs at the grid's rate."""
    reader, rate = audio_io.ItemReader(raw), grid.SAMPLE_RATE_HZ
    whole, asked, long_rows = [], [], {}
    for k, clip in batch:
        said = corpus.words(clip.text or "")
        x = reader.read(clip.item)
        if not said or not len(x):
            continue
        if len(x) <= spec["max_s"] * rate:
            whole.append((k, {"item": clip.item, "speaker": clip.speaker or "", "text": " ".join(said)}))
            continue
        key = f"r{k:07d}"
        audio_io.write_wav(wavs / f"{key}.wav", x)
        asked.append({"id": key, "wav": str(wavs / f"{key}.wav"), "text": " ".join(said)})
        long_rows[key] = (k, clip, said, len(x) / rate)
    return Prepared(whole, asked, long_rows, wavs)


def aligned(ready: Prepared, spec: dict, tts: dict, work: Path, cache: Path) -> list[dict]:
    """The spans of a prepared batch in row order: its whole rows and the aligned runs of its longer rows."""
    times = engines.align(ready.asked, tts, work, cache) if ready.asked else {}
    out = list(ready.whole)
    for key, (k, clip, said, seconds) in ready.long_rows.items():
        words = times.get(key, [])
        if [w["word"] for w in words] != said:
            continue
        for start, end, run in runs(words, spec["max_s"], spec["min_gap_s"], spec["margin_s"], seconds):
            item = f"{clip.item}@{start:.3f}-{end:.3f}"
            out.append((k, {"item": item, "speaker": clip.speaker or "", "text": " ".join(run)}))
    shutil.rmtree(ready.wavs, ignore_errors=True)
    for folder in work.glob("align_*"):
        shutil.rmtree(folder)
    return [row for _, row in sorted(out, key=lambda kr: kr[0])]


def batch_spans(batch: list[tuple[int, corpus.Clip]], spec: dict, tts: dict, work: Path, paths: dict) -> list[dict]:
    """The spans of a batch of numbered rows, in row order: whole rows within max_s, aligned runs of the longer."""
    return aligned(prepared(batch, spec, work / "wav", paths["raw"]), spec, tts, work, paths["cache"])


def clip_item(item: str) -> str:
    """The item of the FLAC a span decodes to: its parquet's path less the suffix, then <row>[_<start>-<end>].flac."""
    whole, _, span = item.partition("@")
    name, _, row = whole.partition("#")
    return f"{name.removesuffix('.parquet')}/{row}{'_' + span if span else ''}.flac"


def write_clips(task: tuple[Path, Path, list[str]]) -> int:
    """Each item of one parquet decoded once into its FLAC under out at the grid's rate, as read gives it; how many
    written, a clip already there left alone."""
    raw, out, items = task
    reader, written = audio_io.ItemReader(raw), 0
    for item in items:
        path = out / clip_item(item)
        if path.exists():
            continue
        part = path.with_name(path.name + ".part")
        part.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(part), audio_io.to_int16(reader.read(item)), grid.SAMPLE_RATE_HZ, subtype="PCM_16", format="FLAC")
        part.replace(path)
        written += 1
    return written


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    with partial.open("w", encoding="utf-8", newline="") as f:
        out = csv.DictWriter(f, FIELDS, delimiter="\t", quoting=csv.QUOTE_NONE, lineterminator="\n")
        out.writeheader()
        out.writerows(rows)
    partial.replace(path)


def read_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE))


def cut(name: str, cfg: dict, tts: dict, paths: dict) -> Path:
    """Write interim/spans/<name>.tsv for the corpus name of cfg's speech corpora; the path written."""
    spec = cfg["corpora"]["speech"][name]
    if spec["layout"] != "spans":
        raise ValueError(f"speech/{name}: layout {spec['layout']}, not spans")
    tts = tts | {"align": tts["align"] | {"jobs": spec.get("align_jobs", tts["align"]["jobs"])}}
    rows = list(enumerate(corpus.clips(paths["raw"], "speech", name, spec["rows"])))
    work, size = paths["cache"] / "spans" / name, spec["align_batch"]
    for stale in [*work.glob("wav*"), *work.glob("align_*")]:
        shutil.rmtree(stale)
    firsts = range(0, len(rows), size)
    todo = [f for f in firsts if not (work / "batches" / f"{f:07d}.tsv").exists()]

    def read(first: int) -> Prepared:
        return prepared(rows[first : first + size], spec, work / f"wav_{first:07d}", paths["raw"])

    # The next batch is read while the aligner, a Docker run this thread only waits on, works on this one.
    with ThreadPoolExecutor(1) as reader:
        ahead = reader.submit(read, todo[0]) if todo else None
        for n, first in enumerate(todo):
            ready = ahead.result()
            ahead = reader.submit(read, todo[n + 1]) if n + 1 < len(todo) else None
            write_rows(work / "batches" / f"{first:07d}.tsv", aligned(ready, spec, tts, work, paths["cache"]))
            print(f"spans: {min(first + size, len(rows))}/{len(rows)} rows", flush=True)
    every = [row for f in firsts for row in read_rows(work / "batches" / f"{f:07d}.tsv")]
    by_file = defaultdict(list)
    for row in every:
        by_file[row["item"].partition("#")[0]].append(row["item"])
    tasks = [(paths["raw"], paths["interim"] / "spans", items) for items in by_file.values()]
    with multiprocessing.get_context("spawn").Pool(spec["clip_processes"]) as pool:
        for n, _ in enumerate(pool.imap_unordered(write_clips, tasks), 1):
            print(f"spans: {n}/{len(tasks)} files decoded", flush=True)
    every = [row | {"item": clip_item(row["item"])} for row in every]
    out = paths["interim"] / "spans" / f"{name}.tsv"
    write_rows(out, every)
    print(f"spans: {len(every)} spans of {len(rows)} rows", flush=True)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", help="a speech corpus of configs/common/screen.yaml with layout spans")
    args = parser.parse_args(argv)
    print(cut(args.name, load_yaml(screen.CONFIG), load_yaml(TTS_CONFIG), data_paths()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
