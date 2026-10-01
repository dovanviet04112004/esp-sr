"""Extract: phrases match by reading, parquet rows come by range reads of their row groups only, tar shards stream,
every step resumes, and a cut keeps the first try heard as the phrase alone and deletes the sentence."""

from __future__ import annotations

import io
import tarfile
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import requests
import soundfile as sf
import yaml

from srpipe.core import extract
from srpipe.core.audio_io import write_wav
from srpipe.core.config import load_yaml
from srpipe.generated import grid

TEXTS = ["hôm nay trời đẹp", "nhờ trợ lí mở cửa giúp", "hỗ trợ lực lượng", "BẬT ĐÈN lên đi", "không có gì"]


def wav_bytes(seconds: float, rate: int = grid.SAMPLE_RATE_HZ) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, 0.1 * np.sin(np.arange(round(seconds * rate)) * 0.05), rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class FakeHttp:
    """The hub as local bytes: files by path inside any repo, a listing per repo, one revision for all."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files, self.reads = files, []

    def path(self, url: str) -> str:
        return urllib.parse.unquote(url.split("/resolve/", 1)[1].split("/", 1)[1])

    def open(self, url: str, start: int | None = None, stop: int | None = None) -> io.BytesIO:
        data = self.files[self.path(url)]
        return io.BytesIO(data if start is None else data[start:stop])

    def resolve(self, url: str) -> str:
        return url

    def get(self, url: str) -> SimpleNamespace:
        return SimpleNamespace(content=self.files[self.path(url)])

    def read(self, url: str, start: int, stop: int, auth: bool = True) -> bytes:
        self.reads.append((start, stop))
        return self.files[self.path(url)][start:stop]

    def json(self, url: str) -> dict:
        return {"sha": "rev0"} if "/revision/" in url else {"tags": ["license:mit"]}

    def pages(self, url: str):
        under = urllib.parse.urlsplit(url).path.split("/tree/", 1)[1].split("/", 1)[1]
        return [{"path": p, "size": len(b), "type": "file"} for p, b in self.files.items() if p.startswith(under)]


def job_for(tmp_path: Path, sources: list[dict], http: FakeHttp) -> extract.Job:
    cfg = load_yaml(extract.CONFIG)
    cfg["extracts"] = {"t": {"phrases": ["trợ lý", "bật đèn", "mở cửa"], "sources": sources}}
    cfg["workers"] = {"scan": 2, "fetch": 2, "stream": 1}
    cfg["http"]["read_ahead_bytes"] = 64
    paths = {"cache": tmp_path / "cache", "raw": tmp_path / "raw", "manifests": tmp_path / "manifests"}
    return extract.Job("t", cfg, paths, http)


def test_phrases_match_by_reading_in_any_case_or_spelling() -> None:
    p = extract.Phrases(["trợ lý", "bật đèn", "mở cửa"])
    assert [p.said(t) for t in TEXTS] == [[], ["trợ lý", "mở cửa"], [], ["bật đèn"], []]
    assert p.said(None) == [] and p.said("chụp ảnh đi") == []
    assert [extract.slug(t) for t in ("đóng cửa", "Đèn bàn", "trợ lý")] == ["dong_cua", "Den_ban", "tro_ly"]
    assert extract.yodas_segment("a-b-c-00012-00000766-00001096") == ("a-b-c", 7.66, 10.96)


def test_parquet_rows_come_from_their_row_groups_and_a_rerun_has_nothing_left(tmp_path: Path) -> None:
    audio = [{"bytes": wav_bytes(0.5 + k / 10), "path": f"{k}.wav"} for k in range(len(TEXTS))]
    buf = io.BytesIO()
    pq.write_table(pa.table({"audio": audio, "transcription": TEXTS}), buf, row_group_size=2)
    assert extract.holds_audio(pq.ParquetFile(io.BytesIO(buf.getvalue())).metadata, "audio")
    linked = io.BytesIO()
    pq.write_table(
        pa.table({"audio": [None] * len(TEXTS), "transcription": TEXTS}).cast(
            pa.schema(
                [("audio", pa.struct([("bytes", pa.binary()), ("path", pa.string())])), ("transcription", pa.string())]
            )
        ),
        linked,
    )
    assert not extract.holds_audio(pq.ParquetFile(linked).metadata, "audio")
    http = FakeHttp({"default/train/0000.parquet": buf.getvalue()})
    job = job_for(tmp_path, [{"kind": "parquet", "repo": "o/d", "revision": "rev0"}], http)
    extract.scan(job)
    (scan_file,) = (tmp_path / "cache/extract/t/scan/o_d").glob("*.jsonl")
    assert [(m["row"], m["phrases"]) for m in extract.read_jsonl(scan_file)] == [
        (1, ["trợ lý", "mở cửa"]),
        (3, ["bật đèn"]),
    ]
    assert max(stop - start for start, stop in http.reads) < len(buf.getvalue())
    extract.fetch(job, False, "bật đèn")
    (first,) = (tmp_path / "cache/extract/t/whole").glob("*.wav")
    assert first.stem.endswith("__3") and not (tmp_path / "cache/extract/t/fetched").exists()
    extract.fetch(job, False)
    wholes = sorted((tmp_path / "cache/extract/t/whole").glob("*.wav"))
    assert [round(sf.info(str(w)).duration, 1) for w in wholes] == [0.6, 0.8]
    http.reads.clear()
    extract.scan(job)
    extract.fetch(job, False)
    assert http.reads == []


def test_tar_shards_stream_and_keep_only_their_matches(tmp_path: Path) -> None:
    ids = ["7-1-0", "7-1-1", "8-2-0"]
    tsv = "".join(f"{i}\t{t}\n" for i, t in zip(ids, ["trợ lý ơi", "chào bạn", "mở cửa ra"], strict=True)).encode()
    shards = {}
    for shard in ("7", "8"):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for i in (i for i in ids if i.startswith(shard)):
                data = wav_bytes(0.4)
                info = tarfile.TarInfo(f"release1/train/{shard}/1/{i}.wav")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        shards[f"data/{shard}.tar.gz"] = buf.getvalue()
    http = FakeHttp({"t.tsv": tsv, **shards})
    source = {
        "kind": "tsv_tar",
        "repo": "s/g",
        "revision": "rev0",
        "transcripts": "t.tsv",
        "shards": "data/{shard}.tar.gz",
    }
    job = job_for(tmp_path, [source], http)
    extract.scan(job)
    extract.fetch(job, False)
    assert sorted(p.stem for p in (tmp_path / "cache/extract/t/whole").glob("*.wav")) == [
        "s_g__t__7_1_0",
        "s_g__t__8_2_0",
    ]


def spoken(seconds: float, words: list[tuple[float, float]]) -> np.ndarray:
    """A sentence of silence with a tone over each (start, end) span, as the words a speaker says."""
    rate = grid.SAMPLE_RATE_HZ
    x = np.zeros(round(seconds * rate))
    for a, b in words:
        x[round(a * rate) : round(b * rate)] = 0.3 * np.sin(np.arange(round((b - a) * rate)) * 0.2)
    return x


def test_a_phrase_is_cut_where_a_pause_follows_it(tmp_path: Path, monkeypatch) -> None:
    job = job_for(tmp_path, [{"kind": "tsv_tar", "repo": "s/g", "revision": "rev0", "synthetic": True}], FakeHttp({}))
    job.cfg["cut"]["hear"] = True
    words = {
        "s_g__1": [("nhờ", 0.1, 0.4), ("trợ", 0.6, 0.8), ("lí", 0.8, 1.0), ("nhé", 1.3, 1.6)],
        "s_g__2": [("nhờ", 0.1, 0.4), ("trợ", 0.4, 0.6), ("lý", 0.6, 0.8), ("nhé", 0.8, 1.1)],
        "s_g__3": [("nhờ", 0.1, 0.4), ("trợ", 0.6, 0.8), ("lý", 0.8, 1.0), ("xanh", 1.02, 1.6)],
        "s_g__4": [("nhờ", 0.1, 0.6), ("trợ", 0.6, 0.8), ("lý", 0.8, 1.0), ("nhé", 1.3, 1.6)],
    }
    # A soft onset: the level test hears quiet where the aligner already has the next word.
    heard_from = {"s_g__3": {"xanh": 1.3}}
    for key, said in words.items():
        text = " ".join(w for w, _, _ in said)
        tones = [(heard_from.get(key, {}).get(w, a), b) for w, a, b in said]
        job.save_whole(key, spoken(2.0, tones), {"key": key, "text": text, "phrases": ["trợ lý"]})
    aligned = {k: [{"word": w, "start": a, "end": b} for w, a, b in v] for k, v in words.items()}
    monkeypatch.setattr(extract.engines, "align", lambda clips, *a: {c["id"]: aligned[c["id"]] for c in clips})
    asked = []
    monkeypatch.setattr(
        extract.engines, "hear_text", lambda clips, *a: asked.extend(clips) or {c["id"]: "Trợ lý." for c in clips}
    )
    device = load_yaml(extract.CONFIGS / "scenes" / "device.yaml")
    extract.cut(job, {}, device, tmp_path, False)
    spec = job.cfg["cut"]
    clips = {c.stem: c for c in (tmp_path / "raw/speech/t/tro_ly").glob("*.wav")}
    assert sorted(clips) == ["s_g__1__0", "s_g__4__0"] and len(asked) == 2
    paused, running = (sf.read(str(clips[k]))[0] for k in ("s_g__1__0", "s_g__4__0"))
    assert abs(len(paused) / grid.SAMPLE_RATE_HZ - (1.0 - 0.6 + 2 * spec["margin_s"])) < 0.002
    assert abs(len(running) / grid.SAMPLE_RATE_HZ - (1.0 - 0.6 + spec["guard_s"] + spec["margin_s"])) < 0.002
    assert np.abs(paused[: round(0.1 * grid.SAMPLE_RATE_HZ)]).max() == 0 and np.abs(paused[-160:]).max() == 0
    assert np.abs(running[:160]).max() > 0 and np.abs(running[-160:]).max() == 0
    assert not list((tmp_path / "cache/extract/t/whole").iterdir())
    rows = extract.read_index(tmp_path / "raw/speech/t")
    folder = tmp_path / "raw/speech/t"
    assert sorted(r["file"] for r in rows) == sorted(str(c.relative_to(folder)) for c in clips.values())
    assert all(r["origin"] == "synth" for r in rows)
    manifest = yaml.safe_load((tmp_path / "manifests/speech/t.yaml").read_text(encoding="utf-8"))
    assert manifest["counts"]["by_phrase"] == {"trợ lý": 2} and manifest["sources"][0]["synthetic"]
    records = {f.stem: extract.read_jsonl(f)[0] for f in (tmp_path / "cache/extract/t/cut").glob("*.jsonl")}
    assert (records["s_g__1"]["paused"], records["s_g__2"]["paused"], records["s_g__2"]["clips"]) == (1, 0, [])
    assert (records["s_g__3"]["apart"], records["s_g__3"]["paused"], records["s_g__4"]["paused"]) == (0, 0, 1)
    soft = spoken(2.0, [(0.1, 0.4), (0.6, 1.0), (1.3, 1.6)])
    assert extract.pause_bounds(soft, (0.6, 1.0), spec) is not None
    short = spoken(2.0, [(0.1, 0.49), (0.6, 1.0), (1.3, 1.6)])
    start, _ = extract.pause_bounds(short, (0.6, 1.0), spec)
    assert 0.49 < start < 0.6 - spec["guard_s"]
    opening = extract.pause_bounds(spoken(2.0, [(0.0, 0.4), (0.8, 1.2)]), (0.0, 0.4), spec)
    assert opening is not None and opening[0] == 0.0
    both = spec | {"sides": "both"}
    assert extract.pause_bounds(spoken(2.0, [(0.1, 0.6), (0.6, 1.0), (1.3, 1.6)]), (0.6, 1.0), both) is None
    assert extract.pause_bounds(soft, (0.6, 1.0), both) is not None


def test_without_the_checker_every_cut_is_kept(tmp_path: Path, monkeypatch) -> None:
    job = job_for(tmp_path, [{"kind": "tsv_tar", "repo": "s/g", "revision": "rev0"}], FakeHttp({}))
    job.cfg["cut"]["hear"] = False
    said = [("nhờ", 0.1, 0.6), ("trợ", 0.6, 0.8), ("lý", 0.8, 1.0), ("nhé", 1.3, 1.6)]
    job.save_whole(
        "s_g__1",
        spoken(2.0, [(a, b) for _, a, b in said]),
        {"key": "s_g__1", "text": "nhờ trợ lý nhé", "phrases": ["trợ lý"]},
    )
    aligned = {"s_g__1": [{"word": w, "start": a, "end": b} for w, a, b in said]}
    monkeypatch.setattr(extract.engines, "align", lambda clips, *a: {c["id"]: aligned[c["id"]] for c in clips})
    monkeypatch.setattr(extract.engines, "hear_text", lambda *a: pytest.fail("the checker was asked"))
    device = load_yaml(extract.CONFIGS / "scenes" / "device.yaml")
    extract.cut(job, {}, device, tmp_path, False)
    (row,) = extract.read_index(tmp_path / "raw/speech/t")
    assert row["phrase"] == "trợ lý" and row["heard"] == ""


def test_a_phrase_has_its_aligned_gaps_to_the_words_on_each_side() -> None:
    sounds = extract.Phrases(["bật đèn"]).sounds[0]
    words = [
        {"word": w, "start": a, "end": b} for w, a, b in [("bật", 0.3, 0.4), ("đèn", 0.6, 0.9), ("xanh", 0.92, 1.3)]
    ]
    ((start, end, before, after),) = extract.phrase_spans(words, sounds)
    assert (start, end) == (0.3, 0.9) and before == float("inf") and abs(after - 0.02) < 1e-9
    ((_, _, _, alone),) = extract.phrase_spans(words[:2], sounds)
    assert alone == float("inf")


def test_recut_queues_a_local_sources_sentences_again(tmp_path: Path) -> None:
    sources = [
        {"kind": "local", "repo": "local", "corpora": ["speech/x/"]},
        {"kind": "tsv_tar", "repo": "s/g", "revision": "rev0"},
    ]
    job = job_for(tmp_path, sources, FakeHttp({}))
    write_wav(tmp_path / "raw/speech/x/a.wav", np.full(grid.SAMPLE_RATE_HZ, 0.1))
    match = {"key": "local__a", "text": "nhờ trợ lý", "phrases": ["trợ lý"], "fetch": "speech/x/"}
    match["item"] = "speech/x/a.wav"
    extract.done_write(job.scan_file(sources[0], "speech/x/"), [match])
    assert extract.recut(job, "local") == 1
    assert job.whole("local__a").exists() and extract.read_jsonl(job.whole("local__a").with_suffix(".json")) == [match]
    with pytest.raises(ValueError):
        extract.recut(job, "s/g")


def test_a_stream_that_breaks_goes_on_from_the_byte_it_reached() -> None:
    data = bytes(range(256)) * 100

    class Cut(io.BytesIO):
        def readinto(self, b) -> int:
            if self.tell() >= 1000:
                raise requests.ConnectionError("connection broken")
            return super().readinto(memoryview(b)[: 1000 - self.tell()])

    class Hub:
        def __init__(self) -> None:
            self.spec, self.starts = {"retries": 2, "first_wait_s": 0.0}, []

        def body(self, url: str, start: int) -> io.BytesIO:
            self.starts.append(start)
            return (Cut if len(self.starts) == 1 else io.BytesIO)(data[start:])

    hub = Hub()
    assert io.BufferedReader(extract.Resumed(hub, "u"), 64).read() == data and hub.starts == [0, 1000]


def test_audio_by_hub_path_and_manifest_members_by_file_name(tmp_path: Path) -> None:
    job = job_for(tmp_path, [], FakeHttp({"data/a.wav": wav_bytes(0.3)}))
    x = extract.audio_of(job, {"bytes": None, "path": "hf://datasets/o/d@rev0/data/a.wav"})
    assert abs(len(x) - 0.3 * grid.SAMPLE_RATE_HZ) <= 1
    manifest = {"kind": "manifest_tar"}
    assert extract.member_id(manifest, "long/audio/C/v_00001.mp3") == extract.member_id(
        manifest, "long/audio-khong-sub/C/v_00001.mp3"
    )
    assert extract.member_id({"kind": "tsv_tar"}, "dev/22/22-52.wav") == "22-52"


def pilot_job(tmp_path: Path) -> extract.Job:
    sources = [{"kind": "parquet", "repo": f"o/{s}", "revision": "rev0"} for s in "abc"]
    job = job_for(tmp_path, sources, FakeHttp({}))
    job.cfg["pilot"] |= {"per_phrase": 5, "per_source": 2}
    return job


def matches_of(repo: str, part: str, phrase: str, rows: range) -> list[dict]:
    return [{"key": f"{repo}__{part}__{r}", "phrases": [phrase], "row": r} for r in rows]


def test_the_pilot_spreads_each_phrase_over_sources_in_runs_of_rows(tmp_path: Path) -> None:
    job = pilot_job(tmp_path)
    sources = {s["repo"]: s for s in job.spec["sources"]}
    parts = [
        (sources["o/a"], "p0", matches_of("o_a", "p0", "mở cửa", range(10)), {}),
        (sources["o/a"], "p1", matches_of("o_a", "p1", "mở cửa", range(3)), {}),
        (sources["o/b"], "p0", matches_of("o_b", "p0", "mở cửa", range(4)), {}),
        (sources["o/c"], "p0", matches_of("o_c", "p0", "bật đèn", range(1)), {}),
    ]
    taken = {"o_b__p0__0", "o_b__p0__1", "o_b__p0__2"}
    chosen = {(s["repo"], part): ms for s, part, ms, _ in extract.pilot_parts(job, parts, taken)}
    assert set(chosen) == {("o/a", "p0"), ("o/b", "p0"), ("o/c", "p0")}
    rows_a = [m["row"] for m in chosen[("o/a", "p0")]]
    assert len(rows_a) == 2 and rows_a[1] == rows_a[0] + 1
    assert [m["key"] for m in chosen[("o/b", "p0")]] == ["o_b__p0__3"]
    assert [m["key"] for m in chosen[("o/c", "p0")]] == ["o_c__p0__0"]
    assert extract.pilot_parts(job, parts, taken) == extract.pilot_parts(job, parts, taken)


def test_listen_copies_clips_from_source_after_source_at_one_level(tmp_path: Path) -> None:
    job = pilot_job(tmp_path)
    job.cfg["pilot"] |= {"listen_per_phrase": 3}
    for n, (repo, amp) in enumerate([("o_a", 0.3), ("o_a", 0.02), ("o_b", 0.1)]):
        name = f"mo_cua/{repo}__p__{n}__0.wav"
        extract.write_wav(job.out / name, amp * np.sin(np.arange(8000) * 0.05))
        clip = {"file": name, "phrase": "mở cửa", "span_s": [0.1, 0.4], "cut_s": [0.05, 0.5], "heard": "mở cửa"}
        record = {"key": f"{repo}__p__{n}", "text": "mở cửa ra", "phrases": ["mở cửa"], "clips": [clip]}
        extract.done_write(job.state / "cut" / f"{record['key']}.jsonl", [record])
    summary = extract.listen(job)
    assert "mở cửa: 3 sentences cut, 3 clips kept (100%), 2 sources" in summary
    index = (tmp_path / "cache/listen/t/index.tsv").read_text(encoding="utf-8").splitlines()
    assert index[0].split("\t")[:3] == ["file", "phrase", "source"] and len(index) == 4
    assert [line.split("\t")[2] for line in index[1:3]] == ["o/a", "o/b"]
    levels = []
    for line in index[1:]:
        x = sf.read(str(tmp_path / "cache/listen/t" / line.split("\t")[0]))[0]
        levels.append(10 * np.log10(np.mean(x**2)))
    assert max(levels) - min(levels) < 0.5
    assert [line.split("\t")[5:7] for line in index[1:2]] == [["50", "100"]]
