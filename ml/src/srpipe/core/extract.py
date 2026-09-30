"""Clips of real voices saying a phrase, cut from Vietnamese speech corpora on Hugging Face (KEHOACH 1.2).

scan lists the rows whose text says a phrase of configs/common/extract.yaml; fetch streams only the audio of a match;
cut keeps each phrase said with a pause on each side, cut into those pauses and heard alone by the checker, then deletes
the sentence. Each step resumes. fetch --pilot, cut, listen is the sample to hear before the whole run.
Run: python -m srpipe.core.extract <name> {scan,fetch,cut,report,listen} [--follow | --pilot]"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import threading
import time
import unicodedata
import urllib.parse
from collections import defaultdict
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import requests
import soundfile as sf
import urllib3
import yaml

from srpipe.core import corpus, screen
from srpipe.core.audio_io import ItemReader, ramped, to_grid_rate, write_wav
from srpipe.core.config import CONFIGS, ML_ROOT, data_paths, load_yaml, read_dotenv
from srpipe.generated import grid
from srpipe.tts import CONFIG as TTS_CONFIG
from srpipe.tts import engines

CONFIG = CONFIGS / "common" / "extract.yaml"
TEXT_COLUMNS = (
    "transcription",
    "text",
    "sentence",
    "transcript",
    "normalized_text",
    "raw_transcription",
    "content",
    "utt",
    "human_transcript",
    "segment_text",
)
RETRY_CODES = (429, 500, 502, 503, 504)
STROKED_D = str.maketrans("đĐ", "dD")
INDEX_FIELDS = ("file", "phrase", "seconds", "origin", "text", "source", "revision", "key", "heard")
LEVEL_FLOOR = 1e-12  # -120 dB: digital silence counts as quiet


class Http:
    """GET with the token, range reads and retries of the http section, over one kept-alive session per thread;
    requests drops the token on a redirect off the hub, so the storage it redirects to never sees it."""

    def __init__(self, spec: dict, token: str | None) -> None:
        self.spec, self.token, self.local = spec, token, threading.local()

    def session(self) -> requests.Session:
        if not hasattr(self.local, "session"):
            self.local.session = requests.Session()
        return self.local.session

    def get(self, url: str, start: int | None = None, stop: int | None = None, stream: bool = False, auth: bool = True):
        headers = {"Authorization": f"Bearer {self.token}"} if self.token and auth else {}
        if start is not None:
            headers["Range"] = f"bytes={start}-{stop - 1}" if stop is not None else f"bytes={start}-"
        wait = self.spec["first_wait_s"]
        for attempt in range(self.spec["retries"] + 1):
            try:
                r = self.session().get(url, headers=headers, stream=stream, timeout=self.spec["timeout_s"])
                if r.status_code not in RETRY_CODES:
                    r.raise_for_status()
                    return r
            except (requests.ConnectionError, requests.Timeout):
                pass
            if attempt == self.spec["retries"]:
                raise OSError(f"{url}: gave up after {attempt + 1} tries")
            time.sleep(wait)
            wait *= 2
        raise AssertionError("unreachable")

    def open(self, url: str):
        """The body of url as a file to stream through, never held whole, picking up where it broke."""
        return io.BufferedReader(Resumed(self, url), buffer_size=self.spec["stream_buffer_bytes"])

    def body(self, url: str, start: int):
        raw = self.get(url, start if start else None, None, stream=True).raw
        # Closed at its end, the body fails a reader that asks once more past it, as zstd does.
        raw.decode_content, raw.auto_close = True, False
        return raw

    def resolve(self, url: str) -> str:
        """Where the hub redirects url, a signed storage address read without the token and without the hub."""
        return self.get(url, 0, 1).url

    def read(self, url: str, start: int, stop: int, auth: bool = True) -> bytes:
        """The bytes [start, stop) of url, asked again when the connection breaks while the body arrives."""
        wait = self.spec["first_wait_s"]
        for attempt in range(self.spec["retries"] + 1):
            try:
                return self.get(url, start, stop, auth=auth).content
            except (requests.exceptions.ChunkedEncodingError, requests.ConnectionError, requests.Timeout):
                if attempt == self.spec["retries"]:
                    raise
            time.sleep(wait)
            wait *= 2
        raise AssertionError("unreachable")

    def json(self, url: str):
        return self.get(url).json()

    def pages(self, url: str) -> Iterator:
        """Every item of a paginated hub listing, following its Link rel=next."""
        while url:
            r = self.get(url)
            yield from r.json()
            url = r.links.get("next", {}).get("url", "")


class Resumed(io.RawIOBase):
    """A streamed body that, when the connection breaks, asks again from the byte it reached: a shard of gigabytes
    over a long-haul line breaks now and then, and starting it over would never end."""

    def __init__(self, http: Http, url: str) -> None:
        self.http, self.url, self.pos, self.tries = http, url, 0, 0
        self.raw = http.body(url, 0)

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        while True:
            try:
                n = self.raw.readinto(b)
                self.pos += n
                return n
            except (requests.exceptions.RequestException, urllib3.exceptions.HTTPError, OSError):
                self.tries += 1
                if self.tries > self.http.spec["retries"]:
                    raise
                time.sleep(self.http.spec["first_wait_s"])
                self.raw = self.http.body(self.url, self.pos)

    def close(self) -> None:
        self.raw.close()
        super().close()


class RangeFile(io.RawIOBase):
    """A remote file read by range requests of at least read_ahead bytes, for pyarrow to seek in; the hub is asked
    once where the file lives, every read then goes to storage."""

    def __init__(self, http: Http, url: str, size: int, read_ahead: int) -> None:
        self.http, self.url, self.size, self.read_ahead = http, url, size, read_ahead
        self.pos, self.buf, self.buf_start, self.target = 0, b"", 0, None
        self.blocks: list[tuple[int, bytes]] = []

    def prefetch(self, ranges: list[tuple[int, int]], parallel: int) -> None:
        """Read the byte ranges [start, stop) at once, parallel requests at a time, for later reads to find."""
        self.target = self.target or self.http.resolve(self.url)
        with ThreadPoolExecutor(parallel) as pool:
            data = list(pool.map(lambda r: self.http.read(self.target, r[0], r[1], auth=False), ranges))
        self.blocks = [(start, d) for (start, _), d in zip(ranges, data, strict=True)]

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        self.pos = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence] + offset
        return self.pos

    def fill(self, stop: int) -> None:
        self.target = self.target or self.http.resolve(self.url)
        try:
            self.buf = self.http.read(self.target, self.pos, stop, auth=False)
        except requests.HTTPError:
            # A signed address expires; ask the hub again.
            self.target = self.http.resolve(self.url)
            self.buf = self.http.read(self.target, self.pos, stop, auth=False)
        self.buf_start = self.pos

    def readinto(self, b) -> int:
        n = min(len(b), self.size - self.pos)
        if n <= 0:
            return 0
        held = next((b for b in self.blocks if b[0] <= self.pos and self.pos + n <= b[0] + len(b[1])), None)
        if held:
            self.buf_start, self.buf = held
        elif not (self.buf_start <= self.pos and self.pos + n <= self.buf_start + len(self.buf)):
            self.fill(min(self.size, self.pos + max(n, self.read_ahead)))
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
    # NFKD keeps đ whole, so dropping what is not ASCII would file "đóng cửa" under ong_cua.
    ascii_text = unicodedata.normalize("NFKD", text.translate(STROKED_D)).encode("ascii", "ignore").decode()
    return re.sub(r"[^0-9A-Za-z]+", "_", ascii_text).strip("_")


def decode_span(data: bytes, start_s: float, end_s: float) -> np.ndarray:
    """[start_s, end_s) of encoded audio as mono float64 at the grid's rate; the rest is never decoded, so a long
    video costs only its segment in memory."""
    rate = sf.info(io.BytesIO(data)).samplerate
    x, _ = sf.read(
        io.BytesIO(data), start=round(start_s * rate), stop=round(end_s * rate), dtype="float64", always_2d=True
    )
    return to_grid_rate(x.mean(axis=1), rate)


def decode(data: bytes) -> np.ndarray:
    """Encoded audio bytes as mono float64 at the grid's rate."""
    x, rate = sf.read(io.BytesIO(data), dtype="float64", always_2d=True)
    return to_grid_rate(x.mean(axis=1), rate)


def clips_folder(raw: Path, name: str) -> Path:
    """Where the extract of that name keeps its clips and clips.tsv."""
    return raw / "speech" / name


class Job:
    """One extract of the config: its phrases, sources, and where its state and clips live."""

    def __init__(self, name: str, cfg: dict, paths: dict[str, Path], http: Http) -> None:
        self.name, self.cfg, self.spec, self.http = name, cfg, cfg["extracts"][name], http
        self.phrases = Phrases(self.spec["phrases"])
        self.state = paths["cache"] / "extract" / name
        self.out = clips_folder(paths["raw"], name)
        self.manifest = paths["manifests"] / "speech" / f"{name}.yaml"
        self.hub, self.read_ahead = cfg["http"]["hub"], cfg["http"]["read_ahead_bytes"]
        self.paths, self.lock, self.kept = paths, threading.Lock(), None

    def pins(self) -> dict[str, dict]:
        """Each source's revision and licence, the first read of an unpinned one fixing it in pins.json."""
        path = self.state / "pins.json"
        known = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        for s in self.spec["sources"]:
            if s["repo"] in known:
                continue
            if s["kind"] == "local":
                known[s["repo"]] = {"revision": "local", "license": "per corpus, docs/DU_LIEU.md"}
                continue
            branch = "refs%2Fconvert%2Fparquet" if s["kind"] == "parquet" and not s.get("own") else "main"
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
        """The sentence, then its match written atomically: a match on disk always has its whole sentence."""
        write_wav(self.whole(key), x)
        done_write(self.whole(key).with_suffix(".json"), [match])

    def local_clips(self) -> list[corpus.Clip]:
        with self.lock:
            if self.kept is None:
                self.kept = screen.kept_clips(load_yaml(screen.CONFIG), self.paths, "speech")
        return self.kept


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


def audio_column(schema: pa.Schema) -> str:
    return next(n for n in schema.names if pa.types.is_struct(schema.field(n).type) or n == "audio")


def holds_audio(meta: pq.FileMetaData, audio: str) -> bool:
    """Whether any row of the file may hold audio by its column statistics: some sets keep only a link to the source
    recording, every audio cell null."""
    for g in range(meta.num_row_groups):
        group = meta.row_group(g)
        for c in (group.column(i) for i in range(group.num_columns)):
            stats = c.statistics
            if c.path_in_schema.split(".")[0] == audio and (
                stats is None or not stats.has_null_count or stats.null_count < group.num_rows
            ):
                return True
    return False


def audio_of(job: Job, value: dict) -> np.ndarray:
    """One row's audio cell as mono float64 at the grid's rate: the bytes of a file, a decoded array with its rate,
    or an hf://datasets/<repo>@<revision>/<path> the file is fetched from."""
    if value.get("bytes"):
        return decode(value["bytes"])
    if value.get("array") is not None:
        return to_grid_rate(np.asarray(value["array"], dtype=np.float64), int(value["sampling_rate"]))
    found = re.fullmatch(r"hf://datasets/([^@]+)@([^/]+)/(.+)", value.get("path") or "")
    if not found:
        raise ValueError(f"an audio cell with neither bytes, array nor an hf:// path: {value.get('path')!r}")
    repo, revision, path = found.groups()
    return decode(job.http.get(f"{job.hub}/datasets/{repo}/resolve/{revision}/{urllib.parse.quote(path)}").content)


def parquet_parts(job: Job, source: dict, pin: dict) -> list[tuple[str, dict]]:
    under = f"{source['config']}" if source.get("config") else ""
    return [(f["path"], f) for f in job.files(source, pin["revision"], ".parquet", under)]


def chunk_ranges(metadata: pq.FileMetaData, column: str) -> list[tuple[int, int]]:
    """Byte ranges of one column's chunk in every row group, dictionary page included."""
    ranges = []
    for g in range(metadata.num_row_groups):
        group = metadata.row_group(g)
        chunk = next(group.column(c) for c in range(group.num_columns) if group.column(c).path_in_schema == column)
        start = chunk.dictionary_page_offset if chunk.has_dictionary_page else chunk.data_page_offset
        ranges.append((start, start + chunk.total_compressed_size))
    return ranges


def scan_parquet(job: Job, source: dict, pin: dict, part: str, info: dict) -> list[dict]:
    remote = RangeFile(job.http, job.url(source, pin["revision"], part), info["size"], job.read_ahead)
    pf = pq.ParquetFile(remote)
    column = text_column(pf.schema_arrow.names)
    if not holds_audio(pf.metadata, audio_column(pf.schema_arrow)):
        raise ValueError("no audio: every audio cell is null")
    remote.prefetch(chunk_ranges(pf.metadata, column), job.cfg["http"]["file_parallel"])
    texts = pf.read(columns=[column]).column(0).to_pylist()
    return [
        {"key": f"{slug(source['repo'])}__{slug(part)}__{i}", "text": t, "phrases": said, "fetch": part, "row": i}
        for i, t in enumerate(texts)
        if (said := job.phrases.said(t))
    ]


def fetch_parquet(job: Job, source: dict, pin: dict, part: str, matches: list[dict], info: dict) -> None:
    pf = pq.ParquetFile(RangeFile(job.http, job.url(source, pin["revision"], part), info["size"], job.read_ahead))
    audio = audio_column(pf.schema_arrow)
    starts = np.cumsum([0] + [pf.metadata.row_group(g).num_rows for g in range(pf.num_row_groups)])
    by_group = defaultdict(list)
    for m in matches:
        by_group[int(np.searchsorted(starts, m["row"], side="right")) - 1].append(m)
    for group, found in sorted(by_group.items()):
        cells = pf.read_row_group(group, columns=[audio]).column(0)
        for m in found:
            job.save_whole(m["key"], audio_of(job, cells[m["row"] - starts[group]].as_py()), m)


def scan_arrow(job: Job, source: dict, pin: dict, part: str, info: dict) -> list[dict]:
    """An .arrow stream read end to end: text and audio sit in the same batches, so the audio of a match is kept
    as the stream passes."""
    matches, offset = [], 0
    with job.http.open(job.url(source, pin["revision"], part)) as r:
        reader = pa.ipc.open_stream(r)
        column = text_column(reader.schema.names)
        audio = audio_column(reader.schema)
        for batch in reader:
            texts = batch.column(column).to_pylist()
            for i, t in enumerate(texts):
                if said := job.phrases.said(t):
                    m = {"key": f"{slug(source['repo'])}__{slug(part)}__{offset + i}", "text": t, "phrases": said}
                    job.save_whole(m["key"], audio_of(job, batch.column(audio)[i].as_py()), m | {"fetch": part})
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
                        "key": f"{slug(source['repo'])}__{slug(Path(source['transcripts']).stem)}__{slug(ident)}",
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


def member_id(source: dict, name: str) -> str:
    """What a tar member is matched by: a manifest's files by name alone, as its paths and the tar's differ above it;
    a YODAS video or a GigaSpeech2 utterance by its stem."""
    return Path(name).name if source["kind"] == "manifest_tar" else Path(name).stem


def fetch_tar(job: Job, source: dict, pin: dict, shard: str, matches: list[dict]) -> str:
    """Stream one shard end to end and keep the members that hold a match; a shard is never stored. Says how many
    of the matches it held, so a shard that held none of those it should is seen."""
    wanted = defaultdict(list)
    for m in matches:
        wanted[yodas_segment(m["id"])[0] if source["kind"] == "yodas" else member_id(source, m["id"])].append(m)
    left = set(wanted)
    with job.http.open(job.url(source, pin["revision"], shard)) as r:
        if shard.endswith(".zst"):
            import zstandard

            stream, mode = zstandard.ZstdDecompressor().stream_reader(r), "r|"
        else:
            stream, mode = r, "r|gz"
        with tarfile.open(fileobj=stream, mode=mode) as tar:
            for member in tar:
                ident = member_id(source, member.name)
                if not member.isfile() or ident not in left:
                    continue
                data = tar.extractfile(member).read()
                for m in wanted[ident]:
                    if source["kind"] == "yodas":
                        job.save_whole(m["key"], decode_span(data, *yodas_segment(m["id"])[1:]), m)
                    else:
                        job.save_whole(m["key"], decode(data), m)
                left.discard(ident)
                if not left:
                    break
    return f", held {len(wanted) - len(left)} of {len(wanted)} wanted files"


def scan_local(job: Job, prefix: str) -> list[dict]:
    """Clips of a corpus under raw/ that screening kept and that say a phrase."""
    return [
        {"key": f"local__{slug(c.item)}", "text": c.text, "phrases": said, "fetch": prefix, "item": c.item}
        for c in job.local_clips()
        if c.item.startswith(prefix) and (said := job.phrases.said(c.text))
    ]


def fetch_local(job: Job, matches: list[dict]) -> None:
    reader = ItemReader(job.paths["raw"])
    for m in sorted(matches, key=lambda m: m["item"]):
        job.save_whole(m["key"], reader.read(m["item"]), m)


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
        elif s["kind"] == "local":
            parts += [(s, prefix, {}) for prefix in s["corpora"]]
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
    if kind == "local":
        return scan_local(job, part)
    return scan_tsv(job, source, pin) if kind == "tsv_tar" else scan_manifest(job, source, pin)


def run_parallel(tasks: list, work, workers: int, label: str) -> list:
    """Run work on every task on a thread pool, reporting each as it finishes; a failed task never stops the others,
    and the failed ones come back for the caller to try again."""
    failed = []
    with ThreadPoolExecutor(workers) as pool:
        futures = {pool.submit(work, *t): t for t in tasks}
        for n, future in enumerate(as_completed(futures), 1):
            t = futures[future]
            try:
                note = future.result()
                print(f"{label} {n}/{len(tasks)} {t[0]['repo']} {t[1]}{note or ''}", flush=True)
            except Exception as e:
                print(f"{label} {n}/{len(tasks)} {t[0]['repo']} {t[1]} FAILED {type(e).__name__}: {e}", flush=True)
                failed.append(t)
    return failed


def scan(job: Job) -> None:
    pins = job.pins()

    def one(source: dict, part: str, info: dict) -> str:
        matches = scan_part(job, source, pins[source["repo"]], part, info)
        done_write(job.scan_file(source, part), matches)
        if source["kind"] == "arrow":
            job.fetched_mark(source, part).parent.mkdir(parents=True, exist_ok=True)
            job.fetched_mark(source, part).touch()
        return f": {len(matches)}"

    parts = scan_parts(job, pins)
    todo = [p for p in parts if not job.scan_file(p[0], p[1]).exists()]
    print(f"scan: {len(todo)} text parts left", flush=True)
    (job.state / "scan.done").unlink(missing_ok=True)
    # An .arrow part streams its audio through memory, so few run at once, after the rest.
    run_parallel([p for p in todo if p[0]["kind"] != "arrow"], one, job.cfg["workers"]["scan"], "scan")
    run_parallel([p for p in todo if p[0]["kind"] == "arrow"], one, job.cfg["workers"]["stream"], "scan")
    if all(job.scan_file(p[0], p[1]).exists() for p in parts):
        (job.state / "scan.done").touch()


def fetch_parts(job: Job, pins: dict) -> list[tuple[dict, str, list[dict], dict]]:
    """(source, audio part, its matches, file info) of every part holding a match, most matches first."""
    parts = []
    for s in job.spec["sources"]:
        if s["kind"] == "arrow" or s.get("hold"):
            continue
        own = [s["transcripts"] if s["kind"] == "tsv_tar" else s["manifest"]] if "_tar" in s["kind"] else None
        found = (
            [job.scan_file(s, part) for part in own] if own else (job.state / "scan" / slug(s["repo"])).glob("*.jsonl")
        )
        matches = [m for f in sorted(f for f in found if f.exists()) for m in read_jsonl(f)]
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


def taken_keys(job: Job) -> set[str]:
    """Matches already cut or waiting to be cut."""
    return {f.stem for f in (job.state / "cut").glob("*.jsonl")} | {f.stem for f in job.whole("").parent.glob("*.json")}


def pilot_parts(
    job: Job, parts: list[tuple[dict, str, list[dict], dict]], taken: set[str]
) -> list[tuple[dict, str, list[dict], dict]]:
    """The pilot's parts and matches: for each phrase, pilot.per_source matches next to each other, from a seeded start,
    in the part of each source that says it most, sources in config order, until pilot.per_phrase."""
    spec = job.cfg["pilot"]
    rng = np.random.default_rng(spec["seed"])
    order = [s["repo"] for s in job.spec["sources"]]
    chosen: dict[tuple[str, str], tuple[dict, str, dict, dict]] = {}
    for phrase in job.phrases.phrases:
        best: dict[str, tuple[dict, str, list[dict], dict]] = {}
        for source, part, matches, info in parts:
            said = [m for m in matches if phrase in m["phrases"] and m["key"] not in taken]
            if said and len(said) > len(best.get(source["repo"], (None, None, []))[2]):
                best[source["repo"]] = (source, part, said, info)
        need = spec["per_phrase"]
        for repo in dict.fromkeys(r for r in order if r in best):
            if need <= 0:
                break
            source, part, said, info = best[repo]
            said = sorted(said, key=lambda m: (m.get("row", 0), m["key"]))
            k = min(spec["per_source"], len(said), need)
            at = int(rng.integers(len(said) - k + 1))
            entry = chosen.setdefault((repo, part), (source, part, {}, info))
            entry[2].update({m["key"]: m for m in said[at : at + k]})
            need -= k
    return [(source, part, list(ms.values()), info) for source, part, ms, info in chosen.values()]


def fetch(job: Job, follow: bool, phrase: str | None = None, pilot: bool = False) -> None:
    """Every audio part not fetched yet, for its matches neither cut nor waiting; with phrase, only the matches that
    say it, and with pilot, only the pilot's matches (pilot_parts), every part left unmarked for a later whole fetch;
    with follow, again as scan finishes parts, until scan is done."""
    if phrase is not None and phrase not in job.phrases.phrases:
        raise ValueError(f"{phrase!r} is none of the phrases of {job.name}")
    pins = job.pins()

    def mark(source: dict, part: str) -> None:
        job.fetched_mark(source, part).parent.mkdir(parents=True, exist_ok=True)
        job.fetched_mark(source, part).touch()

    def one(source: dict, part: str, matches: list[dict], info: dict) -> str:
        # Sentences wait on the data disk for cut; below the floor, fetch waits for cut to free it.
        while shutil.disk_usage(job.state).free < job.cfg["fetch_min_free_gb"] * 1e9:
            time.sleep(job.cfg["follow_poll_s"])
        note = ""
        if source["kind"] == "parquet":
            fetch_parquet(job, source, pins[source["repo"]], part, matches, info)
        elif source["kind"] == "local":
            fetch_local(job, matches)
        else:
            note = fetch_tar(job, source, pins[source["repo"]], part, matches)
        if phrase is None and not pilot:
            mark(source, part)
        return f": {len(matches)} matches{note}"

    if pilot:
        todo = pilot_parts(job, fetch_parts(job, pins), taken_keys(job))
        print(f"fetch: pilot of {sum(len(p[2]) for p in todo)} matches in {len(todo)} audio parts", flush=True)
        failed = run_parallel(todo, one, job.cfg["workers"]["fetch"], "fetch")
        print(f"fetch: pilot done, {len(failed)} parts failed every try", flush=True)
        return
    failures = defaultdict(int)
    while True:
        scanned = (job.state / "scan.done").exists()
        taken = taken_keys(job)
        left = []
        for source, part, matches, info in fetch_parts(job, pins):
            if job.fetched_mark(source, part).exists():
                continue
            wanted = [m for m in matches if (phrase is None or phrase in m["phrases"]) and m["key"] not in taken]
            if wanted:
                left.append((source, part, wanted, info))
            elif phrase is None:
                mark(source, part)
        todo = [p for p in left if failures[(p[0]["repo"], p[1])] < job.cfg["http"]["retries"]]
        print(f"fetch: {len(todo)} audio parts left, {sum(len(p[2]) for p in todo)} matches", flush=True)
        if len(left) > len(todo):
            print(f"fetch: {len(left) - len(todo)} parts failed every try; a later run tries them again", flush=True)
        if todo:
            (job.state / "fetch.done").unlink(missing_ok=True)
            for p in run_parallel(todo, one, job.cfg["workers"]["fetch"], "fetch"):
                failures[(p[0]["repo"], p[1])] += 1
            continue
        if scanned and phrase is None:
            (job.state / "fetch.done").touch()
        if scanned or not follow:
            return
        time.sleep(job.cfg["follow_poll_s"])


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


def cut(job: Job, tts: dict, device: dict, cache: Path, follow: bool) -> None:
    """Every fetched sentence; with follow, again as fetch brings more, until fetch is done."""
    while True:
        fetched = (job.state / "fetch.done").exists()
        sentences = rarest_first(f for f in (job.state / "whole").glob("*.json") if f.with_suffix(".wav").exists())
        if sentences:
            cut_sentences(job, sentences, tts, device, cache)
            continue
        if fetched or not follow:
            return
        time.sleep(job.cfg["follow_poll_s"])


def rarest_first(paths: Iterator[Path]) -> list[Path]:
    """The waiting sentences, those holding the rarest phrase among them first: the checker hears about one try a
    second, so the wake word and the rare commands are cut before the thousands of common ones."""
    phrases = {path: json.loads(path.read_text(encoding="utf-8"))["phrases"] for path in paths}
    counts = defaultdict(int)
    for said in phrases.values():
        for p in said:
            counts[p] += 1
    return sorted(phrases, key=lambda path: (min(counts[p] for p in phrases[path]), path.name))


def quiet_run(quiet: np.ndarray, frame: int, step: int, most: int) -> int:
    """Consecutive quiet frames from frame on, stepping by step, at most most."""
    n = 0
    while n < most and 0 <= frame + step * n < len(quiet) and quiet[frame + step * n]:
        n += 1
    return n


def pause_bounds(x: np.ndarray, span: tuple[float, float], spec: dict) -> tuple[float, float] | None:
    """Seconds to cut a phrase aligned at span out of its sentence x: into the pause on each side past guard_s, at most
    margin_s past the aligned bound. None for running speech, where a side lacks min_pause_s of frames all
    quiet_below_peak_db under the phrase's loudest (KEHOACH 3.11)."""
    frame_s = spec["frame_s"]
    hop = round(frame_s * grid.SAMPLE_RATE_HZ)
    n = len(x) // hop
    level = 10 * np.log10(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1) + LEVEL_FLOOR)
    first, last = int(span[0] / frame_s), min(n, int(np.ceil(span[1] / frame_s)))
    if last <= first:
        return None
    quiet = level <= level[first:last].max() - spec["quiet_below_peak_db"]
    guard, need, most = (round(spec[k] / frame_s) for k in ("guard_s", "min_pause_s", "margin_s"))
    reach = most - guard
    before = quiet_run(quiet, first - guard - 1, -1, reach)
    after = quiet_run(quiet, last + guard, 1, reach)
    if min(before, after) < need:
        return None
    start = first - guard - (before if before == reach else before // 2)
    stop = last + guard + (after if after == reach else after // 2)
    return round(start * frame_s, 3), round(stop * frame_s, 3)


def cut_sentences(job: Job, sentences: list[Path], tts: dict, device: dict, cache: Path) -> None:
    """Align the sentences in batches, cut each phrase said with a pause on each side into those pauses, keep the
    clips the checker hears as the phrase alone, then delete the sentences and every try."""
    spec, rate = job.cfg["cut"], grid.SAMPLE_RATE_HZ
    if spec["guard_s"] + spec["min_pause_s"] > spec["margin_s"]:
        raise ValueError("cut: guard_s + min_pause_s must fit inside margin_s")
    pad, ramp_s = np.zeros(round(device["session"]["pad_s"] * rate)), device["talker"]["edge_ramp_s"]
    sounds = dict(zip(job.phrases.phrases, job.phrases.sounds, strict=True))
    work = job.state / "work"
    print(f"cut: {len(sentences)} sentences left", flush=True)
    for first in range(0, len(sentences), spec["align_batch"]):
        batch = [json.loads(f.read_text(encoding="utf-8")) for f in sentences[first : first + spec["align_batch"]]]
        asked = [
            {"id": m["key"], "wav": str(job.whole(m["key"])), "text": " ".join(corpus.words(m["text"]))} for m in batch
        ]
        times = engines.align(asked, tts, work, cache)
        found = []
        for m in batch:
            x = sf.read(str(job.whole(m["key"])), dtype="float64")[0]
            for p in m["phrases"]:
                for n, span in enumerate(phrase_spans(times.get(m["key"], []), sounds[p])):
                    found.append(
                        {"key": m["key"], "n": n, "phrase": p, "span": span, "cut": pause_bounds(x, span, spec)}
                    )
        paused = [i for i, o in enumerate(found) if o["cut"]]
        tries = []
        for i in paused:
            wav = work / "tries" / f"{i}.wav"
            write_wav(
                wav, np.concatenate([pad, ramped(segment(job.whole(found[i]["key"]), *found[i]["cut"]), ramp_s), pad])
            )
            tries.append({"id": str(i), "wav": str(wav)})
        heard = engines.hear_text(tries, tts, work, cache) if tries else {}
        shutil.rmtree(work / "tries", ignore_errors=True)
        kept = {i: heard[str(i)] for i in paused if corpus.sounds(heard[str(i)]) == sounds[found[i]["phrase"]]}
        by_key = defaultdict(list)
        for i, o in enumerate(found):
            by_key[o["key"]].append((i, o))
        for m in batch:
            clips = []
            for i, o in by_key.get(m["key"], []):
                if i not in kept:
                    continue
                name = f"{slug(o['phrase'])}/{m['key']}__{o['n']}.wav"
                write_wav(job.out / name, ramped(segment(job.whole(m["key"]), *o["cut"]), ramp_s))
                clips.append(
                    {"file": name, "phrase": o["phrase"], "span_s": o["span"], "cut_s": o["cut"], "heard": kept[i]}
                )
            mine = by_key.get(m["key"], [])
            record = m | {"clips": clips, "found": len(mine), "paused": sum(o["cut"] is not None for _, o in mine)}
            done_write(job.state / "cut" / f"{m['key']}.jsonl", [record])
            job.whole(m["key"]).unlink()
            job.whole(m["key"]).with_suffix(".json").unlink()
        for folder in work.glob("align_*"):
            shutil.rmtree(folder)
        write_index(job)
        done = min(first + spec["align_batch"], len(sentences))
        print(
            f"cut: {done}/{len(sentences)} sentences, {len(paused)} of {len(found)} phrases said with a pause,"
            f" {len(kept)} kept",
            flush=True,
        )


def write_index(job: Job) -> None:
    """raw/speech/<name>/clips.tsv of every kept clip, and the repo manifest of sources, pins and counts."""
    pins = job.pins()
    rows = [(c, m) for f in sorted((job.state / "cut").glob("*.jsonl")) for m in read_jsonl(f) for c in m["clips"]]
    repo_of = {slug(s["repo"]): s["repo"] for s in job.spec["sources"]}
    synthetic = {s["repo"] for s in job.spec["sources"] if s.get("synthetic")}
    lines = ["\t".join(INDEX_FIELDS)]
    for c, m in rows:
        source = repo_of[m["key"].split("__")[0]]
        values = (
            c["file"],
            c["phrase"],
            round(c["cut_s"][1] - c["cut_s"][0], 3),
            "synth" if source in synthetic else "public",
            m["text"],
            source,
            pins[source]["revision"],
            m["key"],
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
        "sources": [
            {"repo": s["repo"], "kind": s["kind"]}
            | ({"synthetic": True} if s.get("synthetic") else {})
            | pins[s["repo"]]
            for s in job.spec["sources"]
        ],
        "notes": "Clips cut to one phrase each by configs/common/extract.yaml (KEHOACH 1.2); the sentences they came "
        "from are deleted once cut. No speaker ids: training only (KEHOACH 1.3).",
    }
    job.manifest.parent.mkdir(parents=True, exist_ok=True)
    job.manifest.write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=False), encoding="utf-8")


def read_index(folder: Path) -> list[dict]:
    """Every clip of folder/clips.tsv as {field: value}, seconds a number and file relative to folder."""
    lines = (folder / "clips.tsv").read_text(encoding="utf-8").splitlines()
    fields = lines[0].split("\t")
    rows = [dict(zip(fields, line.split("\t"), strict=True)) for line in lines[1:]]
    return [r | {"seconds": float(r["seconds"])} for r in rows]


def report(job: Job) -> str:
    """Where the extract stands: matches found, sentences waiting, aligned phrases said with a pause and the share of
    them the checker kept, clips and their lengths per phrase, and the data disk's free space."""
    matches = defaultdict(int)
    for f in (job.state / "scan").glob("*/*.jsonl"):
        for m in read_jsonl(f):
            for p in m["phrases"]:
                matches[p] += 1
    kept, found, paused, unaligned, lengths = defaultdict(int), 0, 0, 0, defaultdict(list)
    for f in (job.state / "cut").glob("*.jsonl"):
        for m in read_jsonl(f):
            found += m.get("found", 0)
            paused += m.get("paused", 0)
            unaligned += m.get("found", 0) == 0
            for c in m["clips"]:
                kept[c["phrase"]] += 1
                lengths[c["phrase"]].append(c["cut_s"][1] - c["cut_s"][0])
    waiting = sum(1 for _ in (job.state / "whole").glob("*.json"))
    free_gb = shutil.disk_usage(job.out.parent).free / 1e9
    lines = [
        f"scanned parts {sum(1 for _ in (job.state / 'scan').glob('*/*.jsonl'))}, waiting sentences {waiting},"
        f" {paused} of {found} aligned phrases said with a pause, kept {sum(kept.values())}"
        f" ({sum(kept.values()) / max(paused, 1):.0%} of those),"
        f" sentences without the phrase aligned {unaligned}, disk free {free_gb:.0f} GB"
    ]
    for p in job.phrases.phrases:
        spread = np.percentile(lengths[p], [5, 50, 95]).round(2).tolist() if lengths[p] else "-"
        lines.append(f"  {p}: matched {matches[p]}, kept {kept[p]}, seconds p5/p50/p95 {spread}")
    return "\n".join(lines)


def listen(job: Job) -> str:
    """Copies of the kept clips for the owner to hear, pilot.listen_per_phrase of each phrase taken from source after
    source and matched to pilot.level_dbfs, into cache/listen/<name>/, with index.tsv; and per phrase the sentences
    cut, the clips kept, their sources, lengths, and the quiet each clip keeps before and after its phrase."""
    spec, rule = job.cfg["pilot"], job.cfg["cut"]
    rng = np.random.default_rng(spec["seed"])
    repo_of = {slug(s["repo"]): s["repo"] for s in job.spec["sources"]}
    records = [m for f in sorted((job.state / "cut").glob("*.jsonl")) for m in read_jsonl(f)]
    out = job.paths["cache"] / "listen" / job.name
    rows, lines = [], []
    for p in job.phrases.phrases:
        mine = [m for m in records if p in m["phrases"]]
        clips = [(c, m) for m in mine for c in m["clips"] if c["phrase"] == p]
        if not clips:
            lines.append(f"  {p}: {len(mine)} sentences cut, no clip kept")
            continue
        seconds = [c["cut_s"][1] - c["cut_s"][0] for c, _ in clips]
        lead = [1000 * (c["span_s"][0] - c["cut_s"][0]) for c, _ in clips]
        tail = [1000 * (c["cut_s"][1] - c["span_s"][1]) for c, _ in clips]
        sources = defaultdict(list)
        for c, m in clips:
            sources[repo_of[m["key"].split("__")[0]]].append((c, m))
        lines.append(
            f"  {p}: {len(mine)} sentences cut, {len(clips)} clips kept ({len(clips) / len(mine):.0%}),"
            f" {len(sources)} sources, seconds p5/p50/p95 {np.percentile(seconds, [5, 50, 95]).round(2).tolist()},"
            f" quiet kept before / after ms p5/p50 {np.percentile(lead, [5, 50]).round().tolist()}"
            f" / {np.percentile(tail, [5, 50]).round().tolist()}"
        )
        queues = [list(rng.permutation(len(v))) for v in sources.values()]
        picked = []
        while len(picked) < spec["listen_per_phrase"] and any(queues):
            for (repo, got), queue in zip(sources.items(), queues, strict=True):
                if queue and len(picked) < spec["listen_per_phrase"]:
                    picked.append((repo, *got[queue.pop(0)]))
        for n, (repo, c, m) in enumerate(picked):
            x = sf.read(str(job.out / c["file"]), dtype="float64")[0]
            hop = round(rule["frame_s"] * grid.SAMPLE_RATE_HZ)
            frames = x[: len(x) // hop * hop].reshape(-1, hop)
            power = np.mean(frames * frames, axis=1)
            loud = power >= power.max() * 10 ** (-rule["quiet_below_peak_db"] / 10)
            gain = 10 ** (spec["level_dbfs"] / 20) / np.sqrt(np.mean(power[loud]))
            name = f"{slug(p)}/{n:02d}_{slug(repo)}.wav"
            write_wav(out / name, x * min(gain, 1.0 / np.max(np.abs(x))))
            lead_ms = round(1000 * (c["span_s"][0] - c["cut_s"][0]))
            tail_ms = round(1000 * (c["cut_s"][1] - c["span_s"][1]))
            length = f"{c['cut_s'][1] - c['cut_s'][0]:.2f}"
            rows.append([name, p, repo, length, c["heard"], lead_ms, tail_ms, m["text"]])
    fields = ["file", "phrase", "source", "seconds", "heard", "quiet_before_ms", "quiet_after_ms", "text"]
    out.mkdir(parents=True, exist_ok=True)
    body = "\n".join("\t".join(str(v).replace("\t", " ") for v in r) for r in [fields, *rows])
    (out / "index.tsv").write_text(body + "\n", encoding="utf-8")
    return "\n".join([f"{len(rows)} copies in {out}", *lines])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", help="an extract of configs/common/extract.yaml")
    parser.add_argument("step", choices=["scan", "fetch", "cut", "report", "listen"])
    parser.add_argument("--follow", action="store_true", help="fetch or cut, keep taking up what the step before adds")
    parser.add_argument("--pilot", action="store_true", help="fetch only the pilot's sample of every phrase")
    parser.add_argument(
        "--phrase", help="fetch only the matches that say this phrase of the extract, ahead of the rest"
    )
    args = parser.parse_args(argv)
    cfg, paths = load_yaml(CONFIG), data_paths()
    if args.step in ("report", "listen"):
        job = Job(args.name, cfg, paths, Http(cfg["http"], None))
        print(report(job) if args.step == "report" else listen(job))
        return 0
    token = {**read_dotenv(ML_ROOT / ".env"), **os.environ}.get(cfg["http"]["token_env"])
    if not token:
        parser.error(f"{cfg['http']['token_env']} is not set in ml/.env: the gated corpora refuse an anonymous read")
    job = Job(args.name, cfg, paths, Http(cfg["http"], token))
    if args.step == "scan":
        scan(job)
    elif args.step == "fetch":
        fetch(job, args.follow, args.phrase, args.pilot)
    else:
        cut(job, load_yaml(TTS_CONFIG), load_yaml(CONFIGS / "scenes" / "device.yaml"), paths["cache"], args.follow)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
