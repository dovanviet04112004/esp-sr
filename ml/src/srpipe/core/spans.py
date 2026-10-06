"""Long rows of a corpus cut at word bounds into spans a training window holds (KEHOACH 1.2): a corpus of screen.yaml
with layout spans names its rows' layout under rows; rows longer than max_s are aligned word by word and cut in silences
of at least min_gap_s into the longest runs within max_s, shorter rows kept whole, the list in interim/spans/<name>.tsv.
Batches of align_batch rows are kept under cache/spans/<name>/, so a stopped run goes on.
Usage: python -m srpipe.core.spans <name>"""

from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path

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


def batch_spans(batch: list[tuple[int, corpus.Clip]], spec: dict, tts: dict, work: Path, paths: dict) -> list[dict]:
    """The spans of a batch of numbered rows, in row order: whole rows within max_s, aligned runs of the longer."""
    reader, rate = audio_io.ItemReader(paths["raw"]), grid.SAMPLE_RATE_HZ
    out, asked, long_rows = [], [], {}
    for k, clip in batch:
        said = corpus.words(clip.text or "")
        x = reader.read(clip.item)
        if not said or not len(x):
            continue
        if len(x) <= spec["max_s"] * rate:
            out.append((k, {"item": clip.item, "speaker": clip.speaker or "", "text": " ".join(said)}))
            continue
        key = f"r{k:07d}"
        audio_io.write_wav(work / "wav" / f"{key}.wav", x)
        asked.append({"id": key, "wav": str(work / "wav" / f"{key}.wav"), "text": " ".join(said)})
        long_rows[key] = (k, clip, said, len(x) / rate)
    times = engines.align(asked, tts, work, paths["cache"]) if asked else {}
    for key, (k, clip, said, seconds) in long_rows.items():
        words = times.get(key, [])
        if [w["word"] for w in words] != said:
            continue
        for start, end, run in runs(words, spec["max_s"], spec["min_gap_s"], spec["margin_s"], seconds):
            item = f"{clip.item}@{start:.3f}-{end:.3f}"
            out.append((k, {"item": item, "speaker": clip.speaker or "", "text": " ".join(run)}))
    shutil.rmtree(work / "wav", ignore_errors=True)
    for folder in work.glob("align_*"):
        shutil.rmtree(folder)
    return [row for _, row in sorted(out, key=lambda kr: kr[0])]


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
    rows = list(enumerate(corpus.clips(paths["raw"], "speech", name, spec["rows"])))
    work = paths["cache"] / "spans" / name
    every = []
    for first in range(0, len(rows), spec["align_batch"]):
        done = work / "batches" / f"{first:07d}.tsv"
        if not done.exists():
            write_rows(done, batch_spans(rows[first : first + spec["align_batch"]], spec, tts, work, paths))
        every += read_rows(done)
        print(f"spans: {min(first + spec['align_batch'], len(rows))}/{len(rows)} rows, {len(every)} spans", flush=True)
    out = paths["interim"] / "spans" / f"{name}.tsv"
    write_rows(out, every)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", help="a speech corpus of configs/common/screen.yaml with layout spans")
    args = parser.parse_args(argv)
    print(cut(args.name, load_yaml(screen.CONFIG), load_yaml(TTS_CONFIG), data_paths()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
