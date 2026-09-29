"""Clips of real voices saying a phrase, cut from Vietnamese speech corpora on Hugging Face (KEHOACH 1.2).

scan reads every source's text over HTTP and lists the rows that say a phrase of configs/common/extract.yaml; fetch
streams only the audio that holds a match into cache/extract/<name>/whole/; cut aligns each sentence, keeps the first
start the checker hears as the phrase alone into raw/speech/<name>/<phrase>/ and deletes the sentence. Each step
records what it finished and resumes from there. Run: python -m srpipe.core.extract <name> {scan,fetch,cut}"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import soundfile as sf
import yaml

from srpipe.core import corpus
from srpipe.core.audio_io import ramped, to_grid_rate, write_wav
from srpipe.core.config import CONFIGS, ML_ROOT, data_paths, load_yaml, read_dotenv
from srpipe.generated import grid
from srpipe.tts import CONFIG as TTS_CONFIG
from srpipe.tts import engines

CONFIG = CONFIGS / "common" / "extract.yaml"
TEXT_COLUMNS = ("transcription", "text", "sentence", "transcript", "normalized_text", "raw_transcription", "content")
RETRY_CODES = (429, 500, 502, 503, 504)


class _NoTokenAcrossHosts(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            new.remove_header("Authorization")
        return new


class Http:
    """GET with the token, range reads and retries of the http section; the token never leaves the hub's host."""

    def __init__(self, spec: dict, token: str | None) -> None:
        self.spec, self.token = spec, token
        self.opener = urllib.request.build_opener(_NoTokenAcrossHosts)

    def open(self, url: str, start: int | None = None, stop: int | None = None):
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        if start is not None:
            headers["Range"] = f"bytes={start}-{stop - 1}"
        wait = self.spec["first_wait_s"]
        for attempt in range(self.spec["retries"] + 1):
            try:
                return self.opener.open(urllib.request.Request(url, headers=headers), timeout=self.spec["timeout_s"])
            except urllib.error.HTTPError as e:
                if e.code not in RETRY_CODES or attempt == self.spec["retries"]:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == self.spec["retries"]:
                    raise
            time.sleep(wait)
            wait *= 2
        raise AssertionError("unreachable")

    def read(self, url: str, start: int, stop: int) -> bytes:
        wait = self.spec["first_wait_s"]
        for attempt in range(self.spec["retries"] + 1):
            try:
                with self.open(url, start, stop) as r:
                    return r.read()
            except (OSError, urllib.error.URLError):
                if attempt == self.spec["retries"]:
                    raise
                time.sleep(wait)
                wait *= 2
        raise AssertionError("unreachable")

    def json(self, url: str):
        with self.open(url) as r:
            return json.loads(r.read())

    def pages(self, url: str) -> Iterator:
        """Every item of a paginated hub listing, following its Link: <...>; rel="next" headers."""
        while url:
            with self.open(url) as r:
                yield from json.loads(r.read())
                found = re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get("Link", ""))
            url = found.group(1) if found else ""


class RangeFile(io.RawIOBase):
    """A remote file read by range requests of at least read_ahead bytes, for pyarrow to seek in."""

    def __init__(self, http: Http, url: str, size: int, read_ahead: int) -> None:
        self.http, self.url, self.size, self.read_ahead = http, url, size, read_ahead
        self.pos, self.buf, self.buf_start = 0, b"", 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        self.pos = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence] + offset
        return self.pos

    def readinto(self, b) -> int:
        n = min(len(b), self.size - self.pos)
        if n <= 0:
            return 0
        if not (self.buf_start <= self.pos and self.pos + n <= self.buf_start + len(self.buf)):
            stop = min(self.size, self.pos + max(n, self.read_ahead))
            self.buf, self.buf_start = self.http.read(self.url, self.pos, stop), self.pos
        offset = self.pos - self.buf_start
        b[:n] = self.buf[offset : offset + n]
        self.pos += n
        return n


class Phrases:
    """The configured phrases by their northern reading, and which of them a text says."""

    def __init__(self, phrases: list[str]) -> None:
        self.phrases = phrases
        self.sounds = [corpus.sounds(p) for p in phrases]
        self.last = {s[-1] for s in self.sounds}

    def said(self, text: str | None) -> list[str]:
        if not text:
            return []
        text = unicodedata.normalize("NFC", text).lower()
        if not any(corpus.reading(t) in self.last for t in re.findall(r"\w+", text)):
            return []
        heard = corpus.sounds(text)
        return [p for p, s in zip(self.phrases, self.sounds, strict=True) if corpus.says(heard, s)]


def slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^0-9A-Za-z]+", "_", ascii_text).strip("_")


def decode(data: bytes) -> np.ndarray:
    """Encoded audio bytes as mono float64 at the grid's rate."""
    x, rate = sf.read(io.BytesIO(data), dtype="float64", always_2d=True)
    return to_grid_rate(x.mean(axis=1), rate)


class Job:
    """One extract of the config: its phrases, sources, and where its state and clips live."""

    def __init__(self, name: str, cfg: dict, paths: dict[str, Path], http: Http) -> None:
        self.name, self.cfg, self.spec, self.http = name, cfg, cfg["extracts"][name], http
        self.phrases = Phrases(self.spec["phrases"])
        self.state = paths["cache"] / "extract" / name
        self.out = paths["raw"] / "speech" / name
        self.manifest = paths["manifests"] / "speech" / f"{name}.yaml"
        self.hub, self.read_ahead = cfg["http"]["hub"], cfg["http"]["read_ahead_bytes"]

    def pins(self) -> dict[str, dict]:
        """Each source's revision and licence, the first read of an unpinned one fixing it in pins.json."""
        path = self.state / "pins.json"
        known = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        for s in self.spec["sources"]:
            if s["repo"] in known:
                continue
            branch = "refs%2Fconvert%2Fparquet" if s["kind"] == "parquet" else "main"
            sha = s.get("revision") or self.http.json(f"{self.hub}/api/datasets/{s['repo']}/revision/{branch}")["sha"]
            tags = self.http.json(f"{self.hub}/api/datasets/{s['repo']}").get("tags", [])
            licence = next((t.split(":", 1)[1] for t in tags if t.startswith("license:")), "none stated")
            known[s["repo"]] = {"revision": sha, "license": licence}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(known, indent=1), encoding="utf-8")
        return known

    def files(self, source: dict, revision: str, suffix: str, under: str = "") -> list[dict]:
        url = f"{self.hub}/api/datasets/{source['repo']}/tree/{revision}/{under}?recursive=true"
        return sorted((f for f in self.http.pages(url) if f["path"].endswith(suffix)), key=lambda f: f["path"])

    def url(self, source: dict, revision: str, path: str) -> str:
        return f"{self.hub}/datasets/{source['repo']}/resolve/{revision}/{urllib.parse.quote(path)}"

    def scan_file(self, source: dict, part: str) -> Path:
        return self.state / "scan" / slug(source["repo"]) / f"{slug(part)}.jsonl"

    def fetched_mark(self, source: dict, part: str) -> Path:
        return self.state / "fetched" / slug(source["repo"]) / slug(part)

    def whole(self, key: str) -> Path:
        return self.state / "whole" / f"{key}.wav"

    def save_whole(self, key: str, x: np.ndarray, match: dict) -> None:
        write_wav(self.whole(key), x)
        self.whole(key).with_suffix(".json").write_text(json.dumps(match, ensure_ascii=False), encoding="utf-8")


def done_write(path: Path, lines: list[dict]) -> None:
    """Write a finished part atomically, so a stopped run never leaves half a part that looks done."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")
    os.replace(tmp, path)


def text_column(names: list[str]) -> str:
    found = next((c for c in TEXT_COLUMNS if c in names), None)
    if found is None:
        raise ValueError(f"no text column among {names}")
    return found


def audio_of(value) -> np.ndarray:
    """One row's audio cell, bytes of a file or a decoded array with its rate, as mono float64 at the grid's rate."""
    if value.get("bytes"):
        return decode(value["bytes"])
    return to_grid_rate(np.asarray(value["array"], dtype=np.float64), int(value["sampling_rate"]))


def parquet_parts(job: Job, source: dict, pin: dict) -> list[tuple[str, dict]]:
    under = f"{source['config']}" if source.get("config") else ""
    return [(f["path"], f) for f in job.files(source, pin["revision"], ".parquet", under)]


def scan_parquet(job: Job, source: dict, pin: dict, part: str, info: dict) -> list[dict]:
    pf = pq.ParquetFile(RangeFile(job.http, job.url(source, pin["revision"], part), info["size"], job.read_ahead))
    column = text_column(pf.schema_arrow.names)
    texts = pf.read(columns=[column]).column(0).to_pylist()
    return [
        {"key": f"{slug(source['repo'])}__{slug(part)}__{i}", "text": t, "phrases": said, "fetch": part, "row": i}
        for i, t in enumerate(texts)
        if (said := job.phrases.said(t))
    ]


def fetch_parquet(job: Job, source: dict, pin: dict, part: str, matches: list[dict], info: dict) -> None:
    pf = pq.ParquetFile(RangeFile(job.http, job.url(source, pin["revision"], part), info["size"], job.read_ahead))
    names = pf.schema_arrow.names
    audio = next(n for n in names if pa.types.is_struct(pf.schema_arrow.field(n).type) or n == "audio")
    starts = np.cumsum([0] + [pf.metadata.row_group(g).num_rows for g in range(pf.num_row_groups)])
    by_group = defaultdict(list)
    for m in matches:
        by_group[int(np.searchsorted(starts, m["row"], side="right")) - 1].append(m)
    for group, found in sorted(by_group.items()):
        cells = pf.read_row_group(group, columns=[audio]).column(0)
        for m in found:
            job.save_whole(m["key"], audio_of(cells[m["row"] - starts[group]].as_py()), m)


def scan_arrow(job: Job, source: dict, pin: dict, part: str, info: dict) -> list[dict]:
    """An .arrow stream read end to end: text and audio sit in the same batches, so the audio of a match is kept
    as the stream passes."""
    matches, offset = [], 0
    with job.http.open(job.url(source, pin["revision"], part)) as r:
        reader = pa.ipc.open_stream(r)
        column = text_column(reader.schema.names)
        audio = next(n for n in reader.schema.names if pa.types.is_struct(reader.schema.field(n).type) or n == "audio")
        for batch in reader:
            texts = batch.column(column).to_pylist()
            for i, t in enumerate(texts):
                if said := job.phrases.said(t):
                    m = {"key": f"{slug(source['repo'])}__{slug(part)}__{offset + i}", "text": t, "phrases": said}
                    job.save_whole(m["key"], audio_of(batch.column(audio)[i].as_py()), m | {"fetch": part})
                    matches.append(m | {"fetch": part, "row": offset + i})
            offset += len(texts)
    return matches


def scan_tsv(job: Job, source: dict, pin: dict) -> list[dict]:
    matches = []
    with job.http.open(job.url(source, pin["revision"], source["transcripts"])) as r:
        for raw in r:
            ident, _, text = raw.decode("utf-8", "replace").rstrip("\n").partition("\t")
            if said := job.phrases.said(text):
                shard = source["shards"].format(shard=ident.split("-")[0])
                matches.append(
                    {
                        "key": f"{slug(source['repo'])}__{slug(ident)}",
                        "text": text,
                        "phrases": said,
                        "fetch": shard,
                        "id": ident,
                    }
                )
    return matches


def yodas_segment(seg: str) -> tuple[str, float, float]:
    """audio id, start and end in seconds of a YODAS segment id <audio>-<n>-<start>-<end>, times in 10 ms."""
    audio, _, start, end = seg.rsplit("-", 3)
    return audio, int(start) / 100.0, int(end) / 100.0


def scan_yodas(job: Job, source: dict, pin: dict, part: str) -> list[dict]:
    with job.http.open(job.url(source, pin["revision"], part)) as r:
        videos = json.loads(r.read())
    shard = part.replace("/text/", "/audio/").removesuffix(".json") + ".tar.gz"
    return [
        {"key": f"{slug(source['repo'])}__{slug(seg)}", "text": text, "phrases": said, "fetch": shard, "id": seg}
        for video in videos
        for seg, text in video["text"].items()
        if (said := job.phrases.said(text))
    ]


def scan_manifest(job: Job, source: dict, pin: dict) -> list[dict]:
    import zstandard

    matches = []
    with job.http.open(job.url(source, pin["revision"], source["manifest"])) as r:
        for raw in io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(r), encoding="utf-8"):
            row = json.loads(raw)
            if said := job.phrases.said(row.get("text")):
                matches.append(
                    {
                        "key": f"{slug(source['repo'])}__{slug(row['id'])}",
                        "text": row["text"],
                        "phrases": said,
                        "fetch": "*",
                        "id": row["audio_filepath"].removeprefix("audio/"),
                    }
                )
    return matches


def fetch_tar(job: Job, source: dict, pin: dict, shard: str, matches: list[dict]) -> None:
    """Stream one shard end to end and keep the members that hold a match; a shard is never stored."""
    wanted = defaultdict(list)
    for m in matches:
        wanted[yodas_segment(m["id"])[0] if source["kind"] == "yodas" else m["id"]].append(m)
    left = set(wanted)
    with job.http.open(job.url(source, pin["revision"], shard)) as r:
        if shard.endswith(".zst"):
            import zstandard

            stream, mode = zstandard.ZstdDecompressor().stream_reader(r), "r|"
        else:
            stream, mode = r, "r|gz"
        with tarfile.open(fileobj=stream, mode=mode) as tar:
            for member in tar:
                name = member.name.removeprefix("./")
                ident = name if source["kind"] == "manifest_tar" else Path(name).stem
                if not member.isfile() or ident not in left:
                    continue
                x = decode(tar.extractfile(member).read())
                for m in wanted[ident]:
                    if source["kind"] == "yodas":
                        _, start, end = yodas_segment(m["id"])
                        rate = grid.SAMPLE_RATE_HZ
                        job.save_whole(m["key"], x[round(start * rate) : round(end * rate)], m)
                    else:
                        job.save_whole(m["key"], x, m)
                left.discard(ident)
                if not left:
                    return


def scan_parts(job: Job, pins: dict) -> list[tuple[dict, str, dict]]:
    """(source, part, file info) of every text part of every source."""
    parts = []
    for s in job.spec["sources"]:
        revision = pins[s["repo"]]["revision"]
        if s["kind"] == "parquet":
            parts += [(s, f["path"], f) for f in job.files(s, revision, ".parquet", s.get("config", ""))]
        elif s["kind"] == "arrow":
            parts += [(s, f["path"], f) for f in job.files(s, revision, ".arrow")]
        elif s["kind"] == "yodas":
            for lang in s["languages"]:
                parts += [(s, f["path"], f) for f in job.files(s, revision, ".json", f"data/{lang}/text")]
        else:
            parts.append((s, s["transcripts"] if s["kind"] == "tsv_tar" else s["manifest"], {}))
    return parts


def scan_part(job: Job, source: dict, pin: dict, part: str, info: dict) -> list[dict]:
    kind = source["kind"]
    if kind == "parquet":
        return scan_parquet(job, source, pin, part, info)
    if kind == "arrow":
        return scan_arrow(job, source, pin, part, info)
    if kind == "yodas":
        return scan_yodas(job, source, pin, part)
    return scan_tsv(job, source, pin) if kind == "tsv_tar" else scan_manifest(job, source, pin)


def run_parallel(tasks: list, work, workers: int, label: str) -> None:
    """Run work on every task on a thread pool, reporting each finished one and every failure, which the next run
    retries; a failed task never stops the others."""
    with ThreadPoolExecutor(workers) as pool:
        futures = {pool.submit(work, *t): t for t in tasks}
        for n, (future, t) in enumerate(futures.items(), 1):
            try:
                note = future.result()
                print(f"{label} {n}/{len(tasks)} {t[0]['repo']} {t[1]}{note or ''}", flush=True)
            except Exception as e:
                print(f"{label} {n}/{len(tasks)} {t[0]['repo']} {t[1]} FAILED {type(e).__name__}: {e}", flush=True)


def scan(job: Job) -> None:
    pins = job.pins()

    def one(source: dict, part: str, info: dict) -> str:
        matches = scan_part(job, source, pins[source["repo"]], part, info)
        done_write(job.scan_file(source, part), matches)
        if source["kind"] == "arrow":
            job.fetched_mark(source, part).parent.mkdir(parents=True, exist_ok=True)
            job.fetched_mark(source, part).touch()
        return f": {len(matches)}"

    todo = [p for p in scan_parts(job, pins) if not job.scan_file(p[0], p[1]).exists()]
    print(f"scan: {len(todo)} text parts left", flush=True)
    run_parallel(todo, one, job.cfg["workers"]["scan"], "scan")


def fetch_parts(job: Job, pins: dict) -> list[tuple[dict, str, list[dict], dict]]:
    """(source, audio part, its matches, file info) of every part holding a match, most matches first."""
    parts = []
    for s in job.spec["sources"]:
        if s["kind"] == "arrow":
            continue
        matches = [m for f in sorted((job.state / "scan" / slug(s["repo"])).glob("*.jsonl")) for m in read_jsonl(f)]
        if s["kind"] == "manifest_tar":
            shards = job.files(s, pins[s["repo"]]["revision"], ".tar.zst")
            parts += [(s, f["path"], matches, f) for f in shards if matches]
            continue
        by_part = defaultdict(list)
        for m in matches:
            by_part[m["fetch"]].append(m)
        sizes = (
            {f["path"]: f for f in job.files(s, pins[s["repo"]]["revision"], ".parquet", s.get("config", ""))}
            if s["kind"] == "parquet"
            else {}
        )
        parts += [(s, part, ms, sizes.get(part, {})) for part, ms in by_part.items()]
    return sorted(parts, key=lambda p: -len(p[2]))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def fetch(job: Job) -> None:
    pins = job.pins()

    def one(source: dict, part: str, matches: list[dict], info: dict) -> str:
        if source["kind"] == "parquet":
            fetch_parquet(job, source, pins[source["repo"]], part, matches, info)
        else:
            fetch_tar(job, source, pins[source["repo"]], part, matches)
        job.fetched_mark(source, part).parent.mkdir(parents=True, exist_ok=True)
        job.fetched_mark(source, part).touch()
        return f": {len(matches)} matches"

    todo = [p for p in fetch_parts(job, pins) if not job.fetched_mark(p[0], p[1]).exists()]
    print(f"fetch: {len(todo)} audio parts left, {sum(len(p[2]) for p in todo)} matches", flush=True)
    run_parallel(todo, one, job.cfg["workers"]["fetch"], "fetch")


def phrase_spans(words: list[dict], phrase: list[tuple[str, ...]]) -> list[tuple[float, float]]:
    """Start and end in seconds of every run of aligned words that reads as phrase."""
    heard = [(corpus.reading(s), w) for w in words for s in corpus.words(w["word"])]
    n = len(phrase)
    return [
        (heard[k][1]["start"], heard[k + n - 1][1]["end"])
        for k in range(len(heard) - n + 1)
        if [r for r, _ in heard[k : k + n]] == phrase
    ]


def segment(path: Path, start_s: float, end_s: float) -> np.ndarray:
    rate = grid.SAMPLE_RATE_HZ
    return sf.read(str(path), start=round(start_s * rate), stop=round(end_s * rate), dtype="float64")[0]


def cut(job: Job, tts: dict, device: dict, cache: Path) -> None:
    """Align the fetched sentences in batches, try each phrase's start in turn until the checker hears the phrase
    alone, keep those clips, then delete the sentences and every try."""
    spec, rate = job.cfg["cut"], grid.SAMPLE_RATE_HZ
    pad, ramp_s = np.zeros(round(device["session"]["pad_s"] * rate)), device["talker"]["edge_ramp_s"]
    sounds = dict(zip(job.phrases.phrases, job.phrases.sounds, strict=True))
    work = job.state / "work"
    sentences = sorted(f for f in (job.state / "whole").glob("*.json") if f.with_suffix(".wav").exists())
    print(f"cut: {len(sentences)} sentences left", flush=True)
    for first in range(0, len(sentences), spec["align_batch"]):
        batch = [json.loads(f.read_text(encoding="utf-8")) for f in sentences[first : first + spec["align_batch"]]]
        asked = [
            {"id": m["key"], "wav": str(job.whole(m["key"])), "text": " ".join(corpus.words(m["text"]))} for m in batch
        ]
        times = engines.align(asked, tts, work, cache)
        length = {m["key"]: sf.info(str(job.whole(m["key"]))).duration for m in batch}
        found = [
            {"key": m["key"], "n": n, "phrase": p, "span": span, "seconds": length[m["key"]]}
            for m in batch
            for p in m["phrases"]
            for n, span in enumerate(phrase_spans(times.get(m["key"], []), sounds[p]))
        ]
        kept, pending = {}, list(range(len(found)))
        for r, shift in enumerate(spec["start_shifts_s"]):
            tries = {}
            for i in pending:
                o = found[i]
                start, end = max(0.0, o["span"][0] + shift), min(o["seconds"], o["span"][1] + spec["tail_s"])
                wav = work / "tries" / f"{i}_{r}.wav"
                write_wav(wav, np.concatenate([pad, ramped(segment(job.whole(o["key"]), start, end), ramp_s), pad]))
                tries[f"{i}/{r}"] = {"id": f"{i}/{r}", "wav": str(wav), "targets": [o["phrase"]], "cut": [start, end]}
            heard = engines.hear(list(tries.values()), tts, work, cache) if tries else {}
            still = []
            for i in pending:
                said = heard[f"{i}/{r}"]["text"]
                if corpus.sounds(said) == sounds[found[i]["phrase"]]:
                    kept[i] = {"shift_s": shift, "heard": said, "cut_s": tries[f"{i}/{r}"]["cut"]}
                else:
                    still.append(i)
            pending = still
            shutil.rmtree(work / "tries", ignore_errors=True)
        by_key = defaultdict(list)
        for i, o in enumerate(found):
            by_key[o["key"]].append((i, o))
        for m in batch:
            clips = []
            for i, o in by_key.get(m["key"], []):
                if i not in kept:
                    continue
                name = f"{slug(o['phrase'])}/{m['key']}__{o['n']}.wav"
                write_wav(job.out / name, segment(job.whole(m["key"]), *kept[i]["cut_s"]))
                clips.append({"file": name, "phrase": o["phrase"], "span_s": o["span"]} | kept[i])
            done_write(job.state / "cut" / f"{m['key']}.jsonl", [m | {"clips": clips}])
            job.whole(m["key"]).unlink()
            job.whole(m["key"]).with_suffix(".json").unlink()
        for folder in work.glob("align_*"):
            shutil.rmtree(folder)
        write_index(job)
        done = min(first + spec["align_batch"], len(sentences))
        print(f"cut: {done}/{len(sentences)} sentences, {len(kept)} of {len(found)} clips kept", flush=True)


def write_index(job: Job) -> None:
    """raw/speech/<name>/clips.tsv of every kept clip, and the repo manifest of sources, pins and counts."""
    pins = job.pins()
    rows = [(c, m) for f in sorted((job.state / "cut").glob("*.jsonl")) for m in read_jsonl(f) for c in m["clips"]]
    repo_of = {slug(s["repo"]): s["repo"] for s in job.spec["sources"]}
    fields = ("file", "phrase", "text", "source", "revision", "key", "shift_s", "heard")
    lines = ["\t".join(fields)]
    for c, m in rows:
        source = repo_of[m["key"].split("__")[0]]
        values = (
            c["file"],
            c["phrase"],
            m["text"],
            source,
            pins[source]["revision"],
            m["key"],
            c["shift_s"],
            c["heard"],
        )
        lines.append("\t".join(str(v).replace("\t", " ").replace("\n", " ") for v in values))
    index = job.out / "clips.tsv"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")
    counts = defaultdict(int)
    by_source = defaultdict(int)
    for c, m in rows:
        counts[c["phrase"]] += 1
        by_source[repo_of[m["key"].split("__")[0]]] += 1
    body = {
        "name": job.name,
        "source_url": job.hub,
        "downloaded": date.today().isoformat(),
        "license": "per source, below",
        "sha256": {"clips.tsv": hashlib.sha256(index.read_bytes()).hexdigest()},
        "counts": {"clips": len(rows), "by_phrase": dict(counts), "by_source": dict(by_source)},
        "consumed_by": ["wake", "command"],
        "sources": [{"repo": s["repo"], "kind": s["kind"]} | pins[s["repo"]] for s in job.spec["sources"]],
        "notes": "Clips cut to one phrase each by configs/common/extract.yaml (KEHOACH 1.2); the sentences they came "
        "from are deleted once cut. No speaker ids: training only (KEHOACH 1.3).",
    }
    job.manifest.parent.mkdir(parents=True, exist_ok=True)
    job.manifest.write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=False), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", help="an extract of configs/common/extract.yaml")
    parser.add_argument("step", choices=["scan", "fetch", "cut"])
    args = parser.parse_args(argv)
    cfg, paths = load_yaml(CONFIG), data_paths()
    token = {**read_dotenv(ML_ROOT / ".env"), **os.environ}.get(cfg["http"]["token_env"])
    if not token:
        parser.error(f"{cfg['http']['token_env']} is not set in ml/.env: the gated corpora refuse an anonymous read")
    job = Job(args.name, cfg, paths, Http(cfg["http"], token))
    if args.step == "scan":
        scan(job)
    elif args.step == "fetch":
        fetch(job)
    else:
        cut(job, load_yaml(TTS_CONFIG), load_yaml(CONFIGS / "scenes" / "device.yaml"), paths["cache"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
