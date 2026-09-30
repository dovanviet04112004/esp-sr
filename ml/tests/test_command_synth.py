"""Command synthesis: positives read every learned command and never the unseen one, clones come only from speakers the
split keeps in train, negatives never sound like a whole command, and the threshold works on the nearest command."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
import yaml

from srpipe.core import corpus, phrases
from srpipe.core.config import load_yaml
from srpipe.tasks.command import CONFIG, synth
from srpipe.tts import clips

PRESETS = [{"label": "Một", "id": "v1"}, {"label": "Hai", "id": "v2"}]


def refs(root: Path) -> list[clips.Reference]:
    return [clips.Reference(s, root / f"{s}.wav", f"câu của {s}") for s in ("A", "B", "C")]


def stream_of(texts: list[str]) -> tuple[np.ndarray, list[str]]:
    vocab: dict[str, int] = {}
    ids: list[int] = []
    for text in texts:
        ids += [vocab.setdefault(w, len(vocab)) for w in corpus.words(text)]
        ids.append(phrases.SEPARATOR)
    return np.array(ids, dtype=np.int32), list(vocab)


def test_positives_read_every_learned_command_in_every_form_and_never_the_unseen_one(tmp_path: Path) -> None:
    cfg = load_yaml(CONFIG)
    commands = synth.learned(cfg)
    assert "chup_anh" not in {c["id"] for c in commands} and len(commands) == 9
    spec = cfg["synth"]["positives"]
    requests = synth.positive_requests(cfg, commands, spec, PRESETS, refs(tmp_path), tmp_path)
    vieneu, f5 = spec["vieneu"], spec["f5"]
    per_vieneu = len(PRESETS) * len(vieneu["preset_seeds"]) + 3 * len(vieneu["clone_seeds"])
    assert len(requests["vieneu"]) == len(commands) * len(vieneu["forms"]) * per_vieneu
    assert len(requests["f5"]) == len(commands) * len(f5["forms"]) * 3 * len(f5["seeds"]) * len(f5["speeds"])
    for reqs in requests.values():
        assert len({r["id"] for r in reqs}) == len({r["out"] for r in reqs}) == len(reqs)
        assert {r["say"] for r in reqs} == {c["text"] for c in commands}
        assert all(clips.spelled(r["text"]) == clips.spelled(r["say"]) for r in reqs)
    assert "chụp ảnh" not in {r["say"] for reqs in requests.values() for r in reqs}


def test_a_form_that_does_not_spell_the_command_is_refused(tmp_path: Path) -> None:
    cfg = load_yaml(CONFIG)
    cfg["synth"]["forms"] = ["{text}", "{text} nhé"]
    with pytest.raises(ValueError, match="does not spell the command"):
        synth.positive_requests(cfg, synth.learned(cfg), cfg["synth"]["positives"], PRESETS, refs(tmp_path), tmp_path)


def test_an_unseen_id_missing_from_the_command_set_is_refused() -> None:
    cfg = load_yaml(CONFIG)
    cfg["split"]["unseen"] = ["chup_anh", "bay_len"]
    with pytest.raises(ValueError, match="bay_len"):
        synth.learned(cfg)


def test_clones_come_only_from_speakers_the_split_keeps_in_train(tmp_path: Path) -> None:
    for speaker in ("VIVOSSPK01", "VIVOSSPK02", "VIVOSSPK03", "VIVOSSPK04"):
        (tmp_path / "vivos" / "waves" / speaker).mkdir(parents=True)
    split = tmp_path / "split"
    split.mkdir()
    (split / "val.txt").write_text("speech/vivos/train/waves/VIVOSSPK02/x.wav\tVIVOSSPK02\t-\tpublic\n")
    (split / "test.txt").write_text("speech/vivos/train/waves/VIVOSSPK04/y.wav\tVIVOSSPK04\t-\tpublic\n")
    (split / "train_vivos.txt").write_text("speech/vivos/train/waves/VIVOSSPK01/z.wav\tVIVOSSPK01\t-\tpublic\n")
    assert synth.clone_speakers(tmp_path / "vivos", split) == ["VIVOSSPK01", "VIVOSSPK03"]


def test_halves_are_every_shorter_run_of_words() -> None:
    assert synth.halves("tăng âm lượng") == ["tăng âm", "âm lượng", "tăng", "âm", "lượng"]
    assert synth.halves("bật đèn") == ["bật", "đèn"]


def test_negatives_never_sound_like_a_whole_command_and_keep_longer_phrases_holding_one() -> None:
    cfg = load_yaml(CONFIG)
    commands = [c for c in synth.learned(cfg) if c["id"] in ("bat_den", "tat_den", "mo_cua")]
    cfg["synth"]["negatives"] |= {"misses": 1, "neighbours": 5, "openings": 2, "phrases": ["mở cửa sổ", "bật đèn"]}
    stream, vocab = stream_of(
        ["tắt đèn đi", "bật đèn", "bặt đèn", "mở của", "mở cửa sổ", "bật lên", "bật lên", "mở ra"]
    )
    texts = synth.negative_texts(cfg, commands, stream, vocab, *phrases.component_codes(vocab))
    found = {t["text"]: t["kind"] for t in texts}
    assert found["bặt đèn"] == "near" and found["mở của"] == "near"
    assert found["bật lên"] == "opening" and found["mở ra"] == "opening"
    assert found["đèn"] == "half" and found["cửa"] == "half"
    assert found["mở cửa sổ"] == "phrase"
    assert not {"bật đèn", "tắt đèn", "mở cửa"} & set(found)
    assert len(texts) == len(found)


def test_each_negative_is_read_by_distinct_voices_with_every_command_as_rival(tmp_path: Path) -> None:
    cfg = load_yaml(CONFIG)
    commands = synth.learned(cfg)
    texts = [{"text": "bật điện", "kind": "phrase"}, {"text": "đèn", "kind": "half"}]
    requests = synth.negative_requests(
        commands, texts, PRESETS, refs(tmp_path), np.random.default_rng(0), tmp_path, {"vieneu": 4, "f5": 2}, 18
    )
    assert len(requests["vieneu"]) == 8 and len(requests["f5"]) == 4
    for reqs in requests.values():
        assert len({r["id"] for r in reqs}) == len(reqs)
        assert all("_t18_" in r["id"] or "_t19_" in r["id"] for r in reqs)
        for t in texts:
            said = [r for r in reqs if r["text"] == t["text"]]
            assert len({r["speaker"] for r in said}) == len(said)
    assert all(r["rivals"] == [c["text"] for c in commands] for reqs in requests.values() for r in reqs)


def clip(n: int, passed: bool, margin: float, rivals: dict[str, float] | None, sha: str) -> dict:
    return {"engine": "e", "id": f"clone_{n}", "passed": passed, "margin": margin, "sha256": sha} | (
        {"rivals": rivals} if rivals is not None else {}
    )


def test_the_threshold_uses_the_nearest_command_and_no_clip_repeats(tmp_path: Path) -> None:
    cfg = load_yaml(CONFIG)
    cfg["synth"]["false_accept"] = 0.1
    negatives = [clip(n, True, 0.0, {"bật đèn": 50.0, "tắt đèn": float(n + 1)}, f"n{n}") for n in range(10)]
    negatives += [clip(10, False, 3.0, {"bật đèn": 0.5, "tắt đèn": 9.0}, "n10")]
    positives = [clip(0, True, 0.0, None, "p0"), clip(1, False, 1.5, None, "p1"), clip(2, False, 4.0, None, "p2")]
    positives += [clip(3, True, 0.0, None, "p0")]
    for name, rows in (("positives", positives), ("negatives", negatives)):
        (tmp_path / synth.SETS[name]).mkdir()
        clips.write_manifest(tmp_path / synth.SETS[name], {"clips": rows})
    assert synth.select(cfg, tmp_path) == pytest.approx(1.9)
    kept = {
        s: [c["kept"] for c in yaml.safe_load((tmp_path / synth.SETS[s] / "manifest.yaml").read_text())["clips"]]
        for s in synth.SETS_KEPT
    }
    assert kept["positives"] == [True, True, False, False]
    assert kept["negatives"] == [False, *[True] * 9, False]


def test_edges_find_the_silence_around_a_tone_and_full_scale_samples(tmp_path: Path) -> None:
    rate = 16000
    t = np.arange(rate // 2) / rate
    tone = 0.5 * np.sin(2 * np.pi * 440.0 * t)
    x = np.concatenate([np.zeros(rate // 5), tone, np.zeros(rate // 10)]).astype(np.float32)
    sf.write(tmp_path / "a.wav", x, rate, subtype="PCM_16")
    e = clips.edges(tmp_path / "a.wav", 0.01, 40.0)
    assert e["lead_s"] == pytest.approx(0.2, abs=0.011) and e["tail_s"] == pytest.approx(0.1, abs=0.011)
    assert e["peak_dbfs"] == pytest.approx(-6.0, abs=0.1) and e["full_scale"] == 0
    sf.write(tmp_path / "b.wav", np.clip(4.0 * x, -1.0, 1.0), rate, subtype="PCM_16")
    assert clips.edges(tmp_path / "b.wav", 0.01, 40.0)["full_scale"] > 0
