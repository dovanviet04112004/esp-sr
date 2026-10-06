"""Screening: every layout lists its clips under the names splits use, long transcripts are read in pieces, the
measures see silence, clipping and noise, a clip is rejected by the first rule it breaks, a duplicate only repeats a
kept clip, and a corpus is measured again only when what measures it changes."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import soundfile as sf

from srpipe.core import corpus, screen
from srpipe.core.audio_io import write_wav
from srpipe.core.config import load_yaml
from srpipe.generated import grid

RATE = grid.SAMPLE_RATE_HZ


def speech(seconds: float, level: float, seed: int = 0) -> np.ndarray:
    """Bursts of noise shaped like syllables with pauses between them, at a peak near level."""
    rng = np.random.default_rng(seed)
    t = np.arange(round(seconds * RATE)) / RATE
    return level * rng.standard_normal(len(t)) * (np.sin(2 * np.pi * 2.0 * t) > 0) / 4


def wav_bytes(x: np.ndarray) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, x, RATE, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def test_a_transcript_longer_than_normalize_takes_is_read_whole() -> None:
    text = " ".join(["một hai ba bốn năm"] * 30)
    assert len(text) > corpus.CHUNK_CHARS
    assert corpus.read_text(text) == (["một", "hai", "ba", "bốn", "năm"] * 30, 0)
    assert corpus.read_text("") == ([], 0)


def test_every_layout_names_its_clips_as_splits_do(tmp_path: Path) -> None:
    raw = tmp_path
    vivos = raw / "speech" / "v" / "train"
    write_wav(vivos / "waves" / "S1" / "S1_1.wav", speech(1.0, 0.5))
    (vivos / "prompts.txt").write_text("S1_1 XIN CHÀO\n", encoding="utf-8")
    cv = raw / "speech" / "cv" / "cv-corpus-1" / "vi"
    (cv / "clips").mkdir(parents=True)
    (cv / "validated.tsv").write_text("client_id\tpath\tsentence\nk\ta.mp3\tmột câu\n", encoding="utf-8")
    table = pa.Table.from_pylist([{"audio": {"bytes": b"", "path": None}, "transcription": t} for t in "ab"])
    (raw / "speech" / "p" / "data").mkdir(parents=True)
    pq.write_table(table, raw / "speech" / "p" / "data" / "train-0.parquet")
    write_wav(raw / "noise" / "n" / "x" / "hum.wav", speech(1.0, 0.1))
    assert corpus.clips(raw, "speech", "v", {"layout": "vivos", "parts": ["train"]}) == [
        corpus.Clip("speech/v/train/waves/S1/S1_1.wav", "S1", "XIN CHÀO")
    ]
    spec = {"layout": "common_voice", "dirs": "cv-corpus-*/vi", "lists": ["validated.tsv"]}
    assert corpus.clips(raw, "speech", "cv", spec) == [
        corpus.Clip("speech/cv/cv-corpus-1/vi/clips/a.mp3", "k", "một câu")
    ]
    assert corpus.clips(raw, "speech", "p", {"layout": "parquet", "files": "data/*.parquet"}) == [
        corpus.Clip("speech/p/data/train-0.parquet#0", None, "a"),
        corpus.Clip("speech/p/data/train-0.parquet#1", None, "b"),
    ]
    assert corpus.clips(raw, "noise", "n", {"layout": "files"}) == [corpus.Clip("noise/n/x/hum.wav")]


def test_a_parquet_corpus_reads_the_text_and_speaker_columns_it_names(tmp_path: Path) -> None:
    rows = [{"audio": {"bytes": b"", "path": None}, "utt": t, "speaker_id": s} for t, s in (("a", "s1"), ("b", "s2"))]
    (tmp_path / "speech" / "m" / "data").mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), tmp_path / "speech" / "m" / "data" / "test-0.parquet")
    spec = {"layout": "parquet", "files": "data/*.parquet", "text": "utt", "speaker": "speaker_id"}
    assert corpus.clips(tmp_path, "speech", "m", spec) == [
        corpus.Clip("speech/m/data/test-0.parquet#0", "s1", "a"),
        corpus.Clip("speech/m/data/test-0.parquet#1", "s2", "b"),
    ]
    with pytest.raises(pa.ArrowInvalid, match="transcription"):
        corpus.clips(tmp_path, "speech", "m", {"layout": "parquet", "files": "data/*.parquet"})


def test_measures_see_silence_the_rail_and_noise() -> None:
    m = load_yaml(screen.CONFIG)["measure"]
    clean = screen.measure(speech(2.0, 0.5), RATE, "một hai ba", m)
    assert clean["syllables"] == 3 and clean["refused"] == 0 and clean["clipped"] == 0.0
    assert clean["loud_dbfs"] - clean["quiet_dbfs"] > 60 and 0.9 < clean["active_seconds"] < 1.2
    assert screen.measure(np.zeros(RATE), RATE, None, m)["rms_dbfs"] == -np.inf
    railed = screen.measure(np.clip(16 * speech(2.0, 0.5), -1, 1), RATE, None, m)
    assert railed["clipped"] > 0.1 and railed["syllables"] is None
    drowned = speech(2.0, 0.5) + 0.2 * np.random.default_rng(1).standard_normal(2 * RATE)
    noisy = screen.measure(drowned, RATE, None, m)
    assert noisy["loud_dbfs"] - noisy["quiet_dbfs"] < 6


def good_row(**change) -> dict:
    row = {"error": None, "rms_dbfs": -25.0, "syllables": 8.0, "refused": 0.0, "clipped": 0.0}
    return row | {"loud_dbfs": -15.0, "quiet_dbfs": -55.0, "active_seconds": 2.0, "pcm": "p"} | change


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"error": "LibsndfileError", "rms_dbfs": None}, "decode"),
        ({"rms_dbfs": -np.inf, "syllables": 0.0}, "silent"),
        ({"syllables": 0.0}, "text"),
        ({"refused": 1.0}, "text"),
        ({"clipped": 0.2}, "clipped"),
        ({"quiet_dbfs": -17.0}, "noisy"),
        ({"active_seconds": 0.1}, "rate"),
        ({"active_seconds": 60.0}, "rate"),
        ({}, None),
    ],
)
def test_a_clip_is_rejected_by_the_first_rule_it_breaks(change: dict, reason: str | None) -> None:
    rules = {"silent_rms_dbfs": -60.0, "text": True, "clipped_fraction": 0.01, "noisy_span_db": 6.0}
    rules |= {"syllables_per_second": [0.5, 12.0]}
    assert screen.broken(good_row(**change), rules) == reason
    assert screen.broken(good_row(**change), {"silent_rms_dbfs": rules["silent_rms_dbfs"]}) in (reason, None)


def test_a_duplicate_repeats_a_kept_clip_and_the_rejects_are_listed(tmp_path: Path) -> None:
    cfg = {"corpora": {"speech": {"a": {}, "b": {}}}, "judge": {"speech": load_yaml(screen.CONFIG)["judge"]["speech"]}}
    rows = {
        "a": [good_row(item="a1", pcm="x", syllables=0.0), good_row(item="a2", pcm="y")],
        "b": [good_row(item="b1", pcm="x"), good_row(item="b2", pcm="y")],
    }
    for name, measured in rows.items():
        screen.write_tsv(tmp_path / "speech" / f"{name}.tsv", screen.FIELDS, [r | {"seconds": 2.0} for r in measured])
    rejects = screen.judge(cfg, tmp_path)
    assert [(r["item"], r["reason"]) for r in rejects] == [("a1", "text"), ("b2", "duplicate")]
    screen.write_tsv(tmp_path / "screen" / "rejects.tsv", screen.REJECT_FIELDS, rejects)
    assert screen.rejected(tmp_path) == {"a1": "text", "b2": "duplicate"}
    with pytest.raises(FileNotFoundError, match="make screen"):
        screen.rejected(tmp_path / "none")


def test_a_corpus_is_measured_again_only_when_what_measures_it_changes(tmp_path: Path, monkeypatch) -> None:
    raw, out = tmp_path / "raw", tmp_path / "out"
    rows = [
        {"audio": {"bytes": wav_bytes(speech(1.0, 0.5, k)), "path": None}, "transcription": "một"} for k in (1, 2, 3)
    ]
    rows.append({"audio": {"bytes": b"not audio", "path": None}, "transcription": "hai"})
    (raw / "speech" / "p" / "data").mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), raw / "speech" / "p" / "data" / "train-0.parquet", row_group_size=2)
    cfg = load_yaml(screen.CONFIG)
    cfg["corpora"] = {"speech": {"p": {"layout": "parquet", "files": "data/*.parquet"}}}

    class Serial:
        def imap(self, f, tasks):
            return map(f, tasks)

    assert screen.measure_corpus(raw, out, "speech", "p", cfg, Serial())
    assert not screen.measure_corpus(raw, out, "speech", "p", cfg, Serial())
    measured = screen.read_measures(out / "speech" / "p.tsv")
    assert [r["item"] for r in measured] == [f"speech/p/data/train-0.parquet#{k}" for k in range(4)]
    assert [r["error"] for r in measured] == [None, None, None, "LibsndfileError"]
    assert len({r["pcm"] for r in measured[:3]}) == 3
    cfg["measure"]["frame_seconds"] *= 2
    assert screen.measure_corpus(raw, out, "speech", "p", cfg, Serial())
    with (out / "speech" / "p.tsv").open(encoding="utf-8") as f:
        assert next(csv.reader(f, delimiter="\t")) == list(screen.FIELDS)


def test_edits_count_syllables_substituted_dropped_and_added() -> None:
    assert screen.edits(["chào", "mi", "na"], ["chào", "mi", "na"]) == 0
    assert screen.edits(["chào", "mí", "na"], ["chào", "mi", "na"]) == 1
    assert screen.edits(["chào", "na"], ["chào", "mi", "na"]) == 1
    assert screen.edits(["ừ", "chào", "mi", "na", "nhé"], ["chào", "mi", "na"]) == 2
    assert screen.edits([], ["chào", "mi", "na"]) == 3


def test_audit_bins_draw_each_measure_from_decodable_texted_and_audible_clips() -> None:
    cfg = load_yaml(screen.CONFIG)
    cfg["audit"] |= {"per_bin": 2, "bins": {"rms_dbfs": [-np.inf, -60, 0], "span_db": [0, 6, 1000]}}
    rows = [good_row(item=f"q{k}", rms_dbfs=-70.0, quiet_dbfs=-72.0, loud_dbfs=-69.0) for k in range(3)]
    rows += [good_row(item=f"n{k}", quiet_dbfs=-17.0) for k in range(3)]
    rows += [good_row(item="e", error="LibsndfileError"), good_row(item="t", syllables=0.0)]
    picks = screen.audit_picks(cfg, rows)
    assert {key: count for key, (count, _) in picks.items()} == {
        ("rms_dbfs", 0): 3,
        ("rms_dbfs", 1): 3,
        ("span_db", 0): 3,
        ("span_db", 1): 0,
    }
    assert all(len(group) == min(2, count) for count, group in picks.values())
    assert {r["item"][0] for r in picks[("span_db", 0)][1]} == {"n"}
