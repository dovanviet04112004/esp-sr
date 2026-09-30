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
import requests
import soundfile as sf
import yaml

from srpipe.core import extract
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


def test_only_a_phrase_with_a_pause_each_side_is_cut_into_those_pauses(tmp_path: Path, monkeypatch) -> None:
    job = job_for(tmp_path, [{"kind": "tsv_tar", "repo": "s/g", "revision": "rev0", "synthetic": True}], FakeHttp({}))
    words = {
        "s_g__1": [("nhờ", 0.1, 0.4), ("trợ", 0.6, 0.8), ("lí", 0.8, 1.0), ("nhé", 1.3, 1.6)],
        "s_g__2": [("nhờ", 0.1, 0.4), ("trợ", 0.4, 0.6), ("lý", 0.6, 0.8), ("nhé", 0.8, 1.1)],
    }
    for key, said in words.items():
        text = " ".join(w for w, _, _ in said)
        job.save_whole(
            key, spoken(2.0, [(a, b) for _, a, b in said]), {"key": key, "text": text, "phrases": ["trợ lý"]}
        )
    aligned = {k: [{"word": w, "start": a, "end": b} for w, a, b in v] for k, v in words.items()}
    monkeypatch.setattr(extract.engines, "align", lambda clips, *a: {c["id"]: aligned[c["id"]] for c in clips})
    asked = []
    monkeypatch.setattr(
        extract.engines, "hear_text", lambda clips, *a: asked.extend(clips) or {c["id"]: "Trợ lý." for c in clips}
    )
    device = load_yaml(extract.CONFIGS / "scenes" / "device.yaml")
    extract.cut(job, {}, device, tmp_path, False)
    spec = job.cfg["cut"]
    (clip,) = (tmp_path / "raw/speech/t/tro_ly").glob("*.wav")
    assert clip.stem.startswith("s_g__1") and len(asked) == 1
    assert abs(sf.info(str(clip)).duration - (1.0 - 0.6 + 2 * spec["margin_s"])) < 0.002
    x = sf.read(str(clip))[0]
    assert np.abs(x[: round(0.1 * grid.SAMPLE_RATE_HZ)]).max() == 0 and np.abs(x[-160:]).max() == 0
    assert not list((tmp_path / "cache/extract/t/whole").iterdir())
    (row,) = extract.read_index(tmp_path / "raw/speech/t")
    assert row["file"] == str(clip.relative_to(tmp_path / "raw/speech/t")) and row["origin"] == "synth"
    assert abs(row["seconds"] - sf.info(str(clip)).duration) < 0.002
    manifest = yaml.safe_load((tmp_path / "manifests/speech/t.yaml").read_text(encoding="utf-8"))
    assert manifest["counts"]["by_phrase"] == {"trợ lý": 1} and manifest["sources"][0]["synthetic"]
    records = {f.stem: extract.read_jsonl(f)[0] for f in (tmp_path / "cache/extract/t/cut").glob("*.jsonl")}
    assert (records["s_g__1"]["paused"], records["s_g__2"]["paused"], records["s_g__2"]["clips"]) == (1, 0, [])
    short = spoken(2.0, [(0.1, 0.49), (0.6, 1.0), (1.3, 1.6)])
    start, _ = extract.pause_bounds(short, (0.6, 1.0), spec)
    assert 0.49 < start < 0.6 - spec["guard_s"]


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
