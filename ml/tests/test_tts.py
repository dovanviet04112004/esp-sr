"""Shared TTS: the checker compares spelling with tones, references are the first clip in the span or parquet draws
spread over the files, and a rendered clip passes only when its own text is heard back, made again only when missing."""

from __future__ import annotations

import io
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import soundfile as sf

from srpipe.core.audio_io import write_wav
from srpipe.core.config import load_yaml
from srpipe.generated import grid
from srpipe.tts import CONFIG, PROJECTS, clips, engines


def test_spelling_keeps_tones_and_drops_case_space_and_punctuation() -> None:
    assert clips.spelled("Chào Mina!") == clips.spelled("chào mi na")
    assert clips.spelled("chào mí na") != clips.spelled("chào mi na")
    assert clips.spelled("chao mi na") != clips.spelled("chào mi na")


def fake_corpus(root: Path) -> Path:
    corpus = root / "c"
    lines = []
    for spk, seconds in (("A", (2.0, 5.0, 6.0)), ("B", (9.0, 4.5))):
        for n, s in enumerate(seconds):
            write_wav(corpus / "waves" / spk / f"{spk}_{n}.wav", np.zeros(round(s * grid.SAMPLE_RATE_HZ)))
            lines.append(f"{spk}_{n} CÂU SỐ {n}")
    (corpus / "prompts.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return corpus


def test_the_reference_is_the_first_clip_inside_the_span(tmp_path: Path) -> None:
    corpus = fake_corpus(tmp_path)
    refs = clips.speaker_references(corpus, ["A", "B"], [4.0, 7.5])
    assert [(r.speaker, r.wav.name, r.text) for r in refs] == [
        ("A", "A_1.wav", "câu số một"),
        ("B", "B_1.wav", "câu số một"),
    ]
    assert clips.speaker_references(corpus, None, [4.0, 7.5]) == refs


def wav_bytes(seconds: float) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.zeros(round(seconds * grid.SAMPLE_RATE_HZ), dtype=np.int16), grid.SAMPLE_RATE_HZ, format="WAV")
    return buf.getvalue()


def fake_parquet(root: Path) -> list[Path]:
    files = []
    for f in range(3):
        rows = [
            {"audio": {"bytes": wav_bytes(5.0), "path": None}, "transcription": f"TỆP {f} CÂU {n}"} for n in range(6)
        ]
        rows += [{"audio": {"bytes": wav_bytes(2.0), "path": None}, "transcription": "ngắn"}]
        rows += [{"audio": {"bytes": wav_bytes(5.0), "path": None}, "transcription": "x" * 300}]
        path = root / f"train-{f:05d}-of-00003.parquet"
        pq.write_table(pa.Table.from_pylist(rows), path, row_group_size=4)
        files.append(path)
    return files


def test_parquet_references_spread_over_the_files_and_skip_what_does_not_fit(tmp_path: Path) -> None:
    files = fake_parquet(tmp_path)
    refs = clips.parquet_references("c", files, 5, [4.0, 7.5], np.random.default_rng(1), tmp_path / "out")
    assert len(refs) == 5 and len({r.speaker for r in refs}) == 5
    assert all(4.0 <= sf.info(str(r.wav)).duration <= 7.5 and r.text.startswith("tệp") for r in refs)
    per_file = Counter(r.speaker.split("_")[1] for r in refs)
    assert len(per_file) == len(files) and max(per_file.values()) == 2
    again = clips.parquet_references("c", files, 5, [4.0, 7.5], np.random.default_rng(1), tmp_path / "out")
    assert again == refs


def test_every_engine_and_the_checker_has_a_pinned_project() -> None:
    tts = load_yaml(CONFIG)
    for name in [*tts["engines"], "asr"]:
        assert (PROJECTS / name / "run.py").is_file()
        assert (PROJECTS / name / "uv.lock").is_file()
    repos = ("checkpoint", "codec", "vocoder")
    pins = [tts["asr"]["model"], *(spec[k] for spec in tts["engines"].values() for k in repos if k in spec)]
    assert all(len(pin.split("@")[1]) == 40 for pin in pins)


def render_with_fakes(requests: dict, heard: dict, root: Path, monkeypatch) -> tuple[list[dict], list[str]]:
    """Render with an engine that writes one second of silence and records what it was asked for."""
    made: list[str] = []

    def synthesise(engine, reqs, tts, work, cache):
        for r in reqs:
            write_wav(Path(r["out"]), np.zeros(grid.SAMPLE_RATE_HZ))
            made.append(r["id"])

    monkeypatch.setattr(engines, "synthesise", synthesise)
    monkeypatch.setattr(engines, "hear", lambda c, *a: heard)
    return clips.render(requests, {}, root / "work", root), made


def test_a_clip_passes_only_when_its_own_text_is_heard(tmp_path: Path, monkeypatch) -> None:
    requests = {
        "e": [
            {"id": "a", "speaker": "A", "text": "Chào Mina!", "seed": 3, "out": str(tmp_path / "a.wav")},
            {"id": "b", "speaker": "B", "text": "chào mi na", "out": str(tmp_path / "b.wav")},
        ]
    }
    rows, made = render_with_fakes(requests, {"e/a": "chào mi na.", "e/b": "chào mí na"}, tmp_path, monkeypatch)
    assert [(r["id"], r["speaker"], r["passed"]) for r in rows] == [("a", "A", True), ("b", "B", False)]
    assert rows[0]["seconds"] == 1.0 and rows[0]["seed"] == 3 and "seed" not in rows[1]
    assert made == ["a", "b"]


def test_a_rerun_makes_only_the_missing_clips_and_hears_them_all(tmp_path: Path, monkeypatch) -> None:
    requests = {"e": [{"id": i, "speaker": i, "text": "x", "out": str(tmp_path / f"{i}.wav")} for i in "abc"]}
    heard = {f"e/{i}": "x" for i in "abc"}
    render_with_fakes(requests, heard, tmp_path, monkeypatch)
    (tmp_path / "b.wav").unlink()
    rows, made = render_with_fakes(requests, heard, tmp_path, monkeypatch)
    assert made == ["b"] and [r["id"] for r in rows] == ["a", "b", "c"]
