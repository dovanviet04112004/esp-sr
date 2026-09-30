"""Shared TTS: the checker compares spelling with tones, references are the first clip in the span or parquet draws
spread over the files, and a rendered clip carries what was heard, whether it spells what the clip must say and the
checker's margins; a rerun makes only the missing clips; the checker's word times come back by clip."""

from __future__ import annotations

import io
import json
import os
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
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


def test_the_reference_is_the_first_clip_inside_the_span_that_screening_kept(tmp_path: Path) -> None:
    corpus = fake_corpus(tmp_path)
    refs = clips.speaker_references(corpus, ["A", "B"], [4.0, 7.5], tmp_path, set())
    assert [(r.speaker, r.wav.name, r.text) for r in refs] == [
        ("A", "A_1.wav", "câu số một"),
        ("B", "B_1.wav", "câu số một"),
    ]
    assert clips.speaker_references(corpus, None, [4.0, 7.5], tmp_path, set()) == refs
    kept = clips.speaker_references(corpus, ["A", "B"], [4.0, 7.5], tmp_path, {"c/waves/A/A_1.wav"})
    assert [r.wav.name for r in kept] == ["A_2.wav", "B_1.wav"]


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

    def draw(rejected: set[str]) -> list[clips.Reference]:
        rng = np.random.default_rng(1)
        return clips.parquet_references("c", files, 5, [4.0, 7.5], rng, tmp_path / "out", tmp_path, rejected)

    refs = draw(set())
    assert len(refs) == 5 and len({r.speaker for r in refs}) == 5
    assert all(4.0 <= sf.info(str(r.wav)).duration <= 7.5 and r.text.startswith("tệp") for r in refs)
    per_file = Counter(r.speaker.split("_")[1] for r in refs)
    assert len(per_file) == len(files) and max(per_file.values()) == 2
    assert draw(set()) == refs
    screened = {f"{f.name}#{row}" for f in files for row in range(4)}
    assert {tuple(r.speaker.split("_")[2:]) for r in draw(screened)} == {("001", "00000"), ("001", "00001")}


def test_every_engine_and_the_checker_has_a_pinned_project() -> None:
    tts = load_yaml(CONFIG)
    for name in [*tts["engines"], "asr"]:
        assert (PROJECTS / name / "run.py").is_file()
        assert (PROJECTS / name / "uv.lock").is_file()
    repos = ("checkpoint", "codec", "vocoder")
    pins = [tts["asr"]["model"], *(spec[k] for spec in tts["engines"].values() for k in repos if k in spec)]
    assert all(len(pin.split("@")[1]) == 40 for pin in pins)


def render_with_fakes(
    requests: dict, heard: dict, root: Path, monkeypatch, chunk: int = 100, stop_after: int | None = None
) -> tuple[list[dict], list[str]]:
    """Render with an engine that writes one second of silence and records what it was asked for, stopping like a
    killed run once it has written stop_after clips, and a checker that hears the given text and scores a target 5
    nats below it unless they spell alike."""
    made: list[str] = []

    def synthesise(engine, reqs, tts, work, cache):
        for r in reqs:
            if stop_after is not None and len(made) == stop_after:
                raise KeyboardInterrupt
            write_wav(Path(r["out"]), np.zeros(grid.SAMPLE_RATE_HZ))
            made.append(r["id"])

    def hear(asked, *a):
        return {
            c["id"]: {
                "text": heard[c["id"]],
                "logp": -1.0,
                "targets": {t: -1.0 - 5.0 * (clips.spelled(t) != clips.spelled(heard[c["id"]])) for t in c["targets"]},
            }
            for c in asked
        }

    project = root / "projects" / "e"
    if not project.exists():
        project.mkdir(parents=True)
        (project / "run.py").write_text("v1")
        (project / "uv.lock").write_text("lock")
    monkeypatch.setattr(engines, "PROJECTS", root / "projects")
    monkeypatch.setattr(engines, "synthesise", synthesise)
    monkeypatch.setattr(engines, "hear", hear)
    tts = {"engines": {"e": {"checkpoint": "c@1"}}, "chunk_clips": {"e": chunk}}
    return clips.render(requests, tts, root / "work", root), made


def test_a_clip_passes_only_when_what_it_must_say_is_heard(tmp_path: Path, monkeypatch) -> None:
    requests = {
        "e": [
            {
                "id": "a",
                "speaker": "A",
                "text": "Chào Mina!",
                "say": "chào mi na",
                "seed": 3,
                "out": str(tmp_path / "a.wav"),
            },
            {"id": "b", "speaker": "B", "text": "chào mi na", "out": str(tmp_path / "b.wav")},
            {
                "id": "c",
                "speaker": "C",
                "text": "chào mi nhé",
                "rivals": ["chào mi na"],
                "out": str(tmp_path / "c.wav"),
            },
        ]
    }
    heard = {"e/a": "chào minah.", "e/b": "chào mí na", "e/c": "chào mi nhé."}
    rows, made = render_with_fakes(requests, heard, tmp_path, monkeypatch)
    assert [(r["id"], r["speaker"], r["passed"], r["margin"]) for r in rows] == [
        ("a", "A", False, 5.0),
        ("b", "B", False, 5.0),
        ("c", "C", True, 0.0),
    ]
    assert rows[2]["rivals"] == {"chào mi na": 5.0} and "rivals" not in rows[0]
    assert rows[0]["say"] == "chào mi na" and rows[0]["seed"] == 3 and "seed" not in rows[1]
    assert rows[0]["seconds"] == 1.0 and made == ["a", "b", "c"]


def test_a_rerun_makes_only_the_clips_whose_making_changed(tmp_path: Path, monkeypatch) -> None:
    ref = tmp_path / "ref.wav"
    write_wav(ref, np.zeros(grid.SAMPLE_RATE_HZ))
    requests = {
        "e": [
            {"id": i, "speaker": i, "text": "x", "ref_audio": str(ref), "out": str(tmp_path / f"{i}.wav")}
            for i in "abc"
        ]
    }
    heard = {f"e/{i}": "x" for i in "abc"}
    assert render_with_fakes(requests, heard, tmp_path, monkeypatch)[1] == ["a", "b", "c"]
    assert render_with_fakes(requests, heard, tmp_path, monkeypatch)[1] == []
    (tmp_path / "b.wav").unlink()
    requests["e"][2]["text"] = "y"
    rows, made = render_with_fakes(requests, heard | {"e/c": "y"}, tmp_path, monkeypatch)
    assert made == ["b", "c"] and [r["id"] for r in rows] == ["a", "b", "c"]
    (tmp_path / "work" / "made.jsonl").write_text("")
    assert render_with_fakes(requests, heard | {"e/c": "y"}, tmp_path, monkeypatch)[1] == ["a", "b", "c"]
    (tmp_path / "projects" / "e" / "run.py").write_text("v2")
    assert render_with_fakes(requests, heard | {"e/c": "y"}, tmp_path, monkeypatch)[1] == ["a", "b", "c"]
    write_wav(ref, np.ones(grid.SAMPLE_RATE_HZ) / 2)
    assert render_with_fakes(requests, heard | {"e/c": "y"}, tmp_path, monkeypatch)[1] == ["a", "b", "c"]


def test_a_stopped_render_resumes_at_the_run_it_lost(tmp_path: Path, monkeypatch) -> None:
    requests = {"e": [{"id": i, "speaker": i, "text": "x", "out": str(tmp_path / f"{i}.wav")} for i in "abcde"]}
    heard = {f"e/{i}": "x" for i in "abcde"}
    with pytest.raises(KeyboardInterrupt):
        render_with_fakes(requests, heard, tmp_path, monkeypatch, chunk=2, stop_after=3)
    rows, made = render_with_fakes(requests, heard, tmp_path, monkeypatch, chunk=2)
    assert made == ["c", "d", "e"] and [r["id"] for r in rows] == list("abcde")


def test_a_batch_reaches_the_engine_one_voice_at_a_time(tmp_path: Path, monkeypatch) -> None:
    requests = [
        {"id": f"{v}{n}", "text": "x", "out": "o", **({"ref_audio": v} if v != "p" else {"voice": "p"})}
        for n in range(3)
        for v in ("b", "a", "p")
    ]
    monkeypatch.setattr(engines, "run", lambda *a, **k: "")
    f5 = {"checkpoint": "c", "vocoder": "v", "timing": {"min_syllable_s": 0.3, "tail_s": 0.25, "peak_dbfs": -1.0}}
    engines.synthesise("f5", requests, {"engines": {"f5": f5}}, tmp_path, tmp_path)
    sent = [json.loads(line)["id"] for line in (tmp_path / "f5_requests.jsonl").read_text().splitlines()]
    assert sent == ["a0", "a1", "a2", "b0", "b1", "b2", "p0", "p1", "p2"]


def fake_checker(monkeypatch) -> list[list[tuple[str, list[str]]]]:
    """engines.run standing in for the checker: every call's (wave name, targets) pairs, each target 1 nat below."""
    asked: list[list[tuple[str, list[str]]]] = []

    def run(name, model, batch, listing, heard, cache):
        lines = [json.loads(line) for line in Path(listing).read_text(encoding="utf-8").splitlines()]
        asked.append([(Path(c["wav"]).stem, c["targets"]) for c in lines])
        answers = [
            {"id": c["id"], "text": "x", "logp": -1.0, "targets": dict.fromkeys(c["targets"], -2.0)} for c in lines
        ]
        Path(heard).write_text("".join(json.dumps(a) + "\n" for a in answers), encoding="utf-8")

    monkeypatch.setattr(engines, "run", run)
    return asked


def test_a_clip_is_heard_again_only_for_a_new_target_or_new_bytes(tmp_path: Path, monkeypatch) -> None:
    asked = fake_checker(monkeypatch)
    tts = {"asr": {"model": "m@r", "batch": 1}, "chunk_clips": {"asr": 100}}
    for name in "ab":
        write_wav(tmp_path / f"{name}.wav", np.full(grid.SAMPLE_RATE_HZ, ord(name) / 1000))
    clip_list = [{"id": n, "wav": str(tmp_path / f"{n}.wav"), "targets": ["t"]} for n in "ab"]
    first = engines.hear(clip_list, tts, tmp_path, tmp_path)
    assert engines.hear(clip_list, tts, tmp_path, tmp_path) == first
    clip_list[0]["targets"] = ["t", "u"]
    engines.hear(clip_list, tts, tmp_path, tmp_path)
    write_wav(tmp_path / "b.wav", np.zeros(grid.SAMPLE_RATE_HZ))
    heard = engines.hear(clip_list, tts, tmp_path, tmp_path)
    assert asked == [[("a", ["t"]), ("b", ["t"])], [("a", ["u"])], [("b", ["t"])]]
    assert heard["a"]["targets"] == {"t": -2.0, "u": -2.0}


def test_clips_with_the_same_bytes_are_heard_once_for_the_targets_of_both(tmp_path: Path, monkeypatch) -> None:
    asked = fake_checker(monkeypatch)
    tts = {"asr": {"model": "m@r", "batch": 1}, "chunk_clips": {"asr": 100}}
    for name in "ab":
        write_wav(tmp_path / f"{name}.wav", np.zeros(grid.SAMPLE_RATE_HZ))
    clip_list = [{"id": n, "wav": str(tmp_path / f"{n}.wav"), "targets": [f"say {n}", "w"]} for n in "ab"]
    heard = engines.hear(clip_list, tts, tmp_path, tmp_path)
    assert asked == [[("a", ["say a", "w", "say b"])]]
    assert heard["a"]["targets"].keys() == heard["b"]["targets"].keys() == {"say a", "say b", "w"}
    assert engines.hear(clip_list, tts, tmp_path, tmp_path) == heard
    assert len(asked) == 1


def test_word_times_come_back_by_clip_from_the_aligner(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def docker(cmd, check):
        calls.append(cmd)
        data = Path(cmd[cmd.index("-v") + 3].split(":")[0])
        for folder in (data / "corpus").iterdir():
            if (folder / f"{folder.name}.lab").read_text(encoding="utf-8") != "không căn được":
                entries = [[0.1, 0.3, "trợ"], [0.3, 0.62, "lý"]]
                out = data / "aligned" / folder.name / f"{folder.name}.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps({"tiers": {"words": {"entries": entries}}}), encoding="utf-8")

    monkeypatch.setattr(engines.subprocess, "run", docker)
    tts = {"align": {"image": "mfa:v1", "acoustic": "vi", "dictionary": "vi", "version": "3.0.0", "jobs": 2}}
    for name in "abc":
        write_wav(tmp_path / f"{name}.wav", np.zeros(grid.SAMPLE_RATE_HZ))
    texts = {"a": "trợ lý", "b": "trợ lý", "c": "không căn được"}
    clips = [{"id": n, "wav": str(tmp_path / f"{n}.wav"), "text": t} for n, t in texts.items()]
    times = engines.align(clips, tts, tmp_path / "work", tmp_path)
    assert set(times) == {"a", "b"} and times["b"][1] == {"word": "lý", "start": 0.3, "end": 0.62}
    script = calls[0][-1]
    assert "mfa model download acoustic vi --version 3.0.0" in script and "mfa align /data/corpus vi vi" in script
    assert calls[0][calls[0].index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    (tmp_path / "mfa/3.0.0/pretrained_models/acoustic").mkdir(parents=True)
    (tmp_path / "mfa/3.0.0/pretrained_models/acoustic/vi.zip").write_bytes(b"model")
    assert engines.align(clips, tts, tmp_path / "work", tmp_path) == times
    assert "download acoustic" not in calls[1][-1] and "download dictionary" in calls[1][-1]


def test_the_fast_checker_hears_each_distinct_clip_once(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def run(name, *args, cache):
        listing, heard = Path(args[2]), Path(args[3])
        rows = [json.loads(line) for line in listing.read_text(encoding="utf-8").splitlines()]
        calls.append((name, args[1], len(rows)))
        heard.write_text("".join(json.dumps({"id": r["id"], "text": "trợ lý."}) + "\n" for r in rows), encoding="utf-8")

    monkeypatch.setattr(engines, "run", run)
    monkeypatch.setattr(engines, "fast_model", lambda tts, cache: tmp_path / "model")
    tts = {"asr_fast": {"model": "m@r", "quantization": "int8_float16", "converter": "c", "batch": 4}}
    write_wav(tmp_path / "a.wav", np.zeros(grid.SAMPLE_RATE_HZ))
    write_wav(tmp_path / "b.wav", np.zeros(grid.SAMPLE_RATE_HZ))
    write_wav(tmp_path / "c.wav", 0.1 * np.ones(grid.SAMPLE_RATE_HZ))
    clips = [{"id": n, "wav": str(tmp_path / f"{n}.wav")} for n in "abc"]
    heard = engines.hear_text(clips, tts, tmp_path, tmp_path)
    assert heard == {"a": "trợ lý.", "b": "trợ lý.", "c": "trợ lý."} and calls == [("asr_ct2", "4", 2)]
    assert engines.hear_text(clips, tts, tmp_path, tmp_path) == heard and len(calls) == 1


def test_a_stopped_checker_keeps_the_runs_it_finished(tmp_path: Path, monkeypatch) -> None:
    asked = fake_checker(monkeypatch)
    checker = engines.run

    def stops_on_the_second_run(*a, **k):
        if len(asked) == 1:
            raise KeyboardInterrupt
        checker(*a, **k)

    tts = {"asr": {"model": "m@r", "batch": 1}, "chunk_clips": {"asr": 2}}
    for name in "abcde":
        write_wav(tmp_path / f"{name}.wav", np.full(grid.SAMPLE_RATE_HZ, ord(name) / 1000))
    clip_list = [{"id": n, "wav": str(tmp_path / f"{n}.wav"), "targets": ["t"]} for n in "abcde"]
    monkeypatch.setattr(engines, "run", stops_on_the_second_run)
    with pytest.raises(KeyboardInterrupt):
        engines.hear(clip_list, tts, tmp_path, tmp_path)
    monkeypatch.setattr(engines, "run", checker)
    heard = engines.hear(clip_list, tts, tmp_path, tmp_path)
    assert [[stem for stem, _ in run] for run in asked] == [["a", "b"], ["c", "d"], ["e"]]
    assert sorted(heard) == list("abcde")
