"""Wake split: voices follow their speaker's role, clones of speakerless corpora train, negatives never say the wake
word, test_neg comes only from its corpora less the speakers moved whole into val_neg, train_neg stops at its hours,
and the extract's clips of the word join the train positives; a build's items get their clip's median F0 once; the
owner's board sessions saying the word are cut into real positives that end where the word does."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from srpipe.core import corpus, extract, splits
from srpipe.core.audio_io import write_wav
from srpipe.core.config import load_device, load_yaml
from srpipe.generated import grid
from srpipe.tasks.wake import CONFIG, data, synth


def synth_clip(kind: str, speaker: str, n: int) -> dict:
    ident = f"preset_{n:02d}_t0_s0" if kind == "preset" else f"clone_{speaker}_t0_s{n}"
    return {"engine": "f5", "id": ident, "speaker": speaker, "kept": n != 3, "seconds": 1.0}


def pinned() -> dict:
    """wake.yaml with the word and hard near-miss settings these tests were written around, whatever word is chosen."""
    cfg = load_yaml(CONFIG)
    cfg["word"] = "chào mi na"
    cfg["synth"]["hard"] = {"held_out": ["chào mẹ", "chào minh", "chào bạn"]}
    cfg["split"]["hard"] = ["\\bchào [mnlbv]", "\\bmươi (lăm|năm|ba|nhăm)\\b", "i n[aàáảãạ]\\b"]
    cfg["split"].pop("val_from_test", None)
    return cfg


def test_the_wake_split_keeps_speakers_words_and_sources_apart() -> None:
    cfg = pinned()
    cfg["split"] |= {"val_speakers": 0.5, "negative_hours": 3 / 3600}
    speakers = [f"VIVOSSPK{k:02d}" for k in range(1, 5)]
    public = [
        corpus.Clip(f"speech/vivos/train/waves/{s}/{s}_{n}.wav", s, "một câu") for s in speakers for n in range(2)
    ]
    public += [corpus.Clip(f"speech/bud500/data/train-0.parquet#{n}", None, "câu khác") for n in range(6)]
    public += [corpus.Clip("speech/bud500/data/train-0.parquet#6", None, "chào mi na nhé")]
    public += [corpus.Clip("speech/common_voice_vi/c/vi/clips/a.mp3", "cv1", "câu thử")]
    public += [corpus.Clip("speech/common_voice_vi/c/vi/clips/b.mp3", "cv2", "Chào Mi Na!")]
    seconds = dict.fromkeys((c.item for c in public), 1.0)
    clones = [synth_clip("clone", s, n) for s in [*speakers, "bud500_00000_001_00002"] for n in range(4)]
    manifests = {synth.SETS["positives"]: clones, synth.SETS["negatives"]: [synth_clip("preset", "Adam", 0)]}
    files = data.build(cfg, public, seconds, manifests)
    role_of = {r.spk: name.split("_")[0] for name, rows in files.items() for r in rows if r.spk != splits.ABSENT}
    val_speakers = {s for s in speakers if role_of.get(s) == "val"}
    assert len(val_speakers) == 2
    assert {r.spk for r in files["val_pos.txt"]} == val_speakers
    assert all(r.spk == splits.ABSENT for r in files["train_pos.txt"] if "bud500" in r.item)
    assert len(files["train_pos.txt"]) + len(files["val_pos.txt"]) == 5 * 3
    negatives = [r.item for name in ("train_neg.txt", "val_neg.txt", "test_neg.txt") for r in files[name]]
    assert "speech/bud500/data/train-0.parquet#6" not in negatives
    assert [r.item for r in files["test_neg.txt"]] == ["speech/common_voice_vi/c/vi/clips/a.mp3"]
    assert sum(r.origin == "public" for r in files["train_neg.txt"]) == 3


def test_a_share_of_test_speakers_moves_whole_into_val_neg() -> None:
    cfg = pinned()
    cfg["split"] |= {"val_speakers": 0.5, "negative_hours": 1.0, "val_from_test": {"speech/common_voice_vi/": 0.5}}
    people = [f"cv{k}" for k in range(6)]
    public = [
        corpus.Clip(f"speech/common_voice_vi/c/vi/clips/{p}_{n}.mp3", p, "câu thử") for p in people for n in range(3)
    ]
    public += [corpus.Clip("speech/vivos/test/waves/VIVOSDEV01/VIVOSDEV01_1.wav", "VIVOSDEV01", "câu khác")]
    public += [corpus.Clip("speech/vivos/train/waves/VIVOSSPK01/VIVOSSPK01_1.wav", "VIVOSSPK01", "một câu")]
    seconds = dict.fromkeys((c.item for c in public), 1.0)
    manifests = {synth.SETS["positives"]: [], synth.SETS["negatives"]: []}
    files = data.build(cfg, public, seconds, manifests)
    in_val = {r.spk for r in files["val_neg.txt"] if "common_voice" in r.item}
    in_test = {r.spk for r in files["test_neg.txt"] if "common_voice" in r.item}
    assert len(in_val) == 3 and in_val | in_test == set(people) and not in_val & in_test
    assert sum(r.spk in in_val for r in files["val_neg.txt"]) == 3 * 3
    assert any("vivos/test" in r.item for r in files["test_neg.txt"])
    without = {**cfg, "split": {k: v for k, v in cfg["split"].items() if k != "val_from_test"}}
    assert {r.spk for r in data.build(without, public, seconds, manifests)["test_neg.txt"]} >= set(people)


def test_hard_files_mine_the_families_by_role_and_leave_the_other_five_as_they_were() -> None:
    cfg = pinned()
    cfg["split"] |= {"val_speakers": 0.5, "negative_hours": 1.0}
    speakers = [f"VIVOSSPK{k:02d}" for k in range(1, 5)]
    texts = ["sáu mươi lăm", "chào mọi người", "chào mẹ đi", "một câu"]
    public = [
        corpus.Clip(f"speech/vivos/train/waves/{s}/{s}_{n}.wav", s, t) for s in speakers for n, t in enumerate(texts)
    ]
    seconds = dict.fromkeys((c.item for c in public), 1.0)
    clones = [synth_clip("clone", s, n) for s in speakers for n in range(2)]
    hard = [synth_clip("clone", s, 7) | {"id": f"clone_{s}_t9_s0"} for s in speakers]
    manifests = {synth.SETS["positives"]: clones, synth.SETS["negatives"]: [synth_clip("preset", "Adam", 0)]}
    without = {**cfg, "split": {k: v for k, v in cfg["split"].items() if k != "hard"}}
    before = data.build(without, public, seconds, manifests)
    after = data.build(cfg, public, seconds, manifests | {synth.SETS["hard"]: hard})
    assert {name: after[name] for name in before} == before
    mined = {r.item.rsplit("_", 1)[1] for name in ("train_hard.txt", "val_hard.txt") for r in after[name]}
    assert mined >= {"0.wav", "1.wav"} and "2.wav" not in mined and "3.wav" not in mined
    val_voices = {r.spk for r in after["val_pos.txt"]}
    assert not {r.spk for r in after["train_hard.txt"] if r.origin == "public"} & val_voices
    assert not {r.item for r in after["val_hard.txt"]} & {r.item for r in after["val_neg.txt"]}
    for role in ("train", "val"):
        assert sum(r.item.startswith("wake/synth_hard/") for r in after[f"{role}_hard.txt"]) == 2


def test_tts_positives_end_just_after_their_word(tmp_path) -> None:
    cfg = pinned()
    hop, fs = grid.HOP_SAMPLES, grid.SAMPLE_RATE_HZ
    word = np.zeros(fs)
    word[10 * hop : 30 * hop] = 0.5 * np.sin(np.arange(20 * hop) * 0.3)
    assert data.speech_end_s(word, 40.0) == 30 * hop / fs
    write_wav(tmp_path / "wake/synth_pos/f5/a.wav", word)
    write_wav(tmp_path / "wake/synth_pos/f5/b.wav", word[: 30 * hop])
    clips = [{"engine": "f5", "id": i, "speaker": "S", "kept": i != "c", "seconds": 1.0} for i in ("a", "b", "c")]
    clips[1]["seconds"] = 30 * hop / fs
    out = data.trimmed(clips, "synth_pos", cfg, tmp_path)
    assert out[0]["cut_s"] == round(30 * hop / fs, 3) and "cut_s" not in out[1] and "cut_s" not in out[2]
    rows = data.synth_rows(out, "synth_pos", {})["train"]
    assert rows[0].item == f"wake/synth_pos/f5/a.wav@0.000-{out[0]['cut_s']:.3f}"
    assert rows[1].item == "wake/synth_pos/f5/b.wav" and len(rows) == 2


def test_simulate_links_a_twin_build_instead_of_running_it_again(tmp_path, monkeypatch) -> None:
    cfg = load_yaml(CONFIG)
    device_cfg = load_device(cfg["features"])
    lines = "wake/synth_pos/f5/a.wav\tx\t-\tsynth\n"
    split_v1, split_v2 = tmp_path / "splits" / "wake" / "v1", tmp_path / "splits" / "wake" / "v2"
    for folder in (split_v1, split_v2):
        folder.mkdir(parents=True)
        (folder / "val_pos.txt").write_text(lines, encoding="utf-8")
    built = tmp_path / "processed" / "wake" / "v1" / "val_pos"
    built.mkdir(parents=True)
    (built / "shard_00000.features.npy").write_bytes(b"features")
    manifest = {"config": device_cfg, "split": {"sha256": splits.sha256_of(split_v1 / "val_pos.txt")}, "repeats": 1}
    (built / "manifest.yaml").write_text(data.yaml.safe_dump(manifest), encoding="utf-8")
    monkeypatch.setattr(data.device, "build", lambda *a, **k: (_ for _ in ()).throw(AssertionError("simulated")))
    paths = {"splits": tmp_path / "splits", "processed": tmp_path / "processed", "raw": tmp_path, "interim": tmp_path}
    unboarded = cfg["eval"] | {"board": cfg["eval"]["board"] | {"train": []}}
    data.simulate(cfg | {"simulate": {"split": "v2", "workers": 1, "repeats": {}}, "eval": unboarded}, paths)
    linked = tmp_path / "processed" / "wake" / "v2" / "val_pos" / "shard_00000.features.npy"
    assert linked.stat().st_ino == (built / "shard_00000.features.npy").stat().st_ino


def test_the_extracts_clips_of_the_word_join_the_train_positives(tmp_path: Path) -> None:
    folder = tmp_path / "speech" / "x"
    folder.mkdir(parents=True)
    hop, fs = grid.HOP_SAMPLES, grid.SAMPLE_RATE_HZ
    clip = np.zeros(round(0.52 * fs))
    clip[5 * hop : 20 * hop] = 0.5 * np.sin(np.arange(15 * hop) * 0.3)
    write_wav(folder / "tro_ly" / "a__0.wav", clip)
    rows = [
        ["tro_ly/a__0.wav", "trợ lí", "0.52", "public", "nhờ trợ lí", "o/d", "rev0", "a", "Trợ lí."],
        ["tro_ly/c__0.wav", "trợ lý", "0.5", "synth", "gọi trợ lý", "t/s", "rev0", "c", "Trợ lý."],
        ["mo_cua/b__0.wav", "mở cửa", "0.4", "public", "mở cửa ra", "o/d", "rev0", "b", "Mở cửa."],
    ]
    lines = [extract.INDEX_FIELDS, *rows]
    (folder / "clips.tsv").write_text("".join("\t".join(r) + "\n" for r in lines), encoding="utf-8")
    cfg = pinned()
    cfg["word"] = "trợ lý"
    cfg["split"] |= {"val_speakers": 0.5, "negative_hours": 1.0, "real_pos": "x"}
    real = data.real_positives(cfg, tmp_path)
    end = round(20 * hop / fs, 3)
    assert real == {f"speech/x/tro_ly/a__0.wav@0.000-{end:.3f}": end}
    assert data.says_word(corpus.Clip("a", None, "nhờ trợ lí nhé"), corpus.sounds("trợ lý"))
    assert not data.says_word(corpus.Clip("a", None, "hỗ trợ lực lượng"), corpus.sounds("trợ lý"))
    public = [corpus.Clip(f"speech/vlsp/a_{n}.wav", None, "một câu") for n in range(3)]
    public += [corpus.Clip("speech/vlsp/a_3.wav", None, "theo các trợ lý tổng thống")]
    seconds = dict.fromkeys((c.item for c in public), 1.0) | real
    manifests = {synth.SETS["positives"]: [], synth.SETS["negatives"]: []}
    files = data.build(cfg, public, seconds, manifests, tuple(real))
    assert [(r.item, r.spk, r.origin) for r in files["train_pos.txt"]] == [(*real, "-", "public")]
    assert not files["val_pos.txt"]
    negatives = [r.item for name in ("train_neg.txt", "val_neg.txt", "test_neg.txt") for r in files[name]]
    assert "speech/vlsp/a_3.wav" not in negatives and len(negatives) == 3


def test_each_items_clean_clip_gets_its_median_f0_once(tmp_path: Path) -> None:
    pytest.importorskip("parselmouth")
    raw, fs = tmp_path / "raw", grid.SAMPLE_RATE_HZ
    t = np.arange(fs) / fs
    voice = sum(np.sin(2 * np.pi * 150.0 * k * t) / k for k in range(1, 20))
    write_wav(raw / "speech" / "x" / "voice.wav", 0.3 * voice / np.abs(voice).max())
    write_wav(raw / "speech" / "x" / "noise.wav", 0.1 * np.random.default_rng(0).standard_normal(fs))
    build = tmp_path / "train_pos"
    build.mkdir()
    rows = [{"item": f"speech/x/{name}.wav", "origin": "public"} for name in ("voice", "noise", "voice")]
    (build / "shard_00000.items.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    roots, spec = {"raw": raw, "interim": tmp_path}, {"time_step_s": 0.01, "f0_range_hz": [60.0, 600.0]}
    hz = json.loads(data.f0_table(build, roots, spec, 1).read_text(encoding="utf-8"))
    assert set(hz) == {"speech/x/voice.wav", "speech/x/noise.wav"} and hz["speech/x/noise.wav"] is None
    assert hz["speech/x/voice.wav"] == pytest.approx(150.0, rel=0.02)
    (build / data.F0_TABLE).write_text("{}", encoding="utf-8")
    assert data.f0_table(build, roots, spec, 1).read_text(encoding="utf-8") == "{}"


def test_a_build_counts_as_built_only_in_the_dtype_asked(tmp_path: Path) -> None:
    split_file = tmp_path / "train_neg.txt"
    split_file.write_text("speech/x/a.wav\t-\t-\tpublic\n", encoding="utf-8")
    device_cfg = {"seed": 1}
    out = tmp_path / "train_neg"
    out.mkdir()
    body = {"config": device_cfg, "split": {"sha256": splits.sha256_of(split_file)}, "repeats": 2}
    for store, built in (({"dtype": "float16", "pcm": False}, True), ({"dtype": "float32", "pcm": False}, False)):
        (out / "manifest.yaml").write_text(yaml.safe_dump(body | store), encoding="utf-8")
        assert data.built_as(out, device_cfg, split_file, 2, "float16") is built
    assert not data.built_as(out, device_cfg, split_file, 1, "float32")


def test_a_pilot_drawing_its_train_negatives_leaves_out_the_word_and_the_kept_speakers(tmp_path: Path) -> None:
    """Fresh train negatives of split.learning: a sentence saying the word, a speaker of the kept val negatives and a
    corpus outside learning stay out; the val and test negatives come over as they are."""
    root, hop = tmp_path / "speech" / "sc", grid.HOP_SAMPLES
    tone = 0.3 * np.sin(2 * np.pi * 300.0 * np.arange(20 * hop) / grid.SAMPLE_RATE_HZ)
    for word, stem in (("on", "aa_nohash_0"), ("off", "bb_nohash_0")):
        write_wav(root / word / f"{stem}.wav", np.concatenate([tone, np.zeros(30 * hop)]))
    (root / "testing_list.txt").write_text("", encoding="utf-8")
    (root / "validation_list.txt").write_text("", encoding="utf-8")
    earlier = tmp_path / "splits" / "wake" / "v4"
    earlier.mkdir(parents=True)
    for name, spk in zip(data.NEGATIVES, ("BUD", "VIVOSSPK01", "VIVOSSPK02"), strict=True):
        (earlier / name).write_text(f"speech/x/{name}.wav\t{spk}\t-\tpublic\n", encoding="utf-8")
    pilot = {"dir": "speech/sc", "negatives_from": "v4", "hard_clips": {"train": 5, "val": 5}, "draw_train_neg": True}
    cfg = load_yaml(CONFIG)
    learning = ["speech/vivos/train/", "speech/fpt_open/"]
    split = cfg["split"] | {"speech_commands": pilot, "learning": learning, "negative_hours": 1.0}
    cfg = cfg | {"word": "on", "split": split}
    public = [
        corpus.Clip("speech/vivos/train/VIVOSSPK01_001.wav", "VIVOSSPK01", "một câu"),
        corpus.Clip("speech/vivos/train/VIVOSSPK03_001.wav", "VIVOSSPK03", "một câu"),
        corpus.Clip("speech/fpt_open/a.mp3", None, "câu khác"),
        corpus.Clip("speech/fpt_open/b.mp3", None, "hát on nhé"),
        corpus.Clip("speech/vlsp/c.wav", None, "câu ngoài"),
    ]
    heard_s = dict.fromkeys((c.item for c in public), 2.0)
    paths = {"raw": tmp_path, "splits": tmp_path / "splits"}
    files, seconds = data.speech_commands_files(cfg, paths, public, heard_s)
    drawn = [r.item for r in files["train_neg.txt"]]
    assert drawn == ["speech/vivos/train/VIVOSSPK03_001.wav", "speech/fpt_open/a.mp3"]
    assert files["val_neg.txt"] == splits.read_split(earlier / "val_neg.txt")
    assert files["test_neg.txt"] == splits.read_split(earlier / "test_neg.txt")
    assert seconds["speech/fpt_open/a.mp3"] == 2.0
    copying = split | {"speech_commands": pilot | {"draw_train_neg": False}}
    kept, _ = data.speech_commands_files(cfg | {"split": copying}, paths)
    assert kept["train_neg.txt"] == splits.read_split(earlier / "train_neg.txt")


def test_a_speech_commands_pilot_cuts_the_word_reuses_public_negatives_and_draws_hard_ones(tmp_path: Path) -> None:
    root, hop = tmp_path / "speech" / "sc", grid.HOP_SAMPLES
    tone = 0.3 * np.sin(2 * np.pi * 300.0 * np.arange(20 * hop) / grid.SAMPLE_RATE_HZ)
    said = {"yes": ["aa_nohash_0", "bb_nohash_0", "cc_nohash_0"], "no": ["aa_nohash_0", "dd_nohash_0"]}
    for word, stems in said.items():
        for stem in stems:
            write_wav(root / word / f"{stem}.wav", np.concatenate([tone, np.zeros(30 * hop)]))
    write_wav(root / "_background_noise_" / "white.wav", np.zeros(hop))
    (root / "testing_list.txt").write_text("yes/bb_nohash_0.wav\nno/dd_nohash_0.wav\n", encoding="utf-8")
    (root / "validation_list.txt").write_text("yes/cc_nohash_0.wav\n", encoding="utf-8")
    earlier = tmp_path / "splits" / "wake" / "v4"
    earlier.mkdir(parents=True)
    for name in data.NEGATIVES:
        public = f"speech/vivos/{name}.wav\tVIVOSSPK01\t-\tpublic\n"
        tts = f"wake/synth_neg/f5/{name}.wav\tvieneu_preset_02\t-\tsynth\n"
        (earlier / name).write_text(public + tts, encoding="utf-8")
    pilot = {"dir": "speech/sc", "negatives_from": "v4", "hard_clips": {"train": 5, "val": 5}}
    cfg = load_yaml(CONFIG)
    cfg = cfg | {"word": "yes", "split": cfg["split"] | {"speech_commands": pilot}}
    files, seconds = data.speech_commands_files(cfg, {"raw": tmp_path, "splits": tmp_path / "splits"})
    assert set(files) == {"train_pos.txt", "val_pos.txt", "train_hard.txt", "val_hard.txt", *data.NEGATIVES}
    assert [r.item for r in files["train_pos.txt"]] == ["speech/sc/yes/aa_nohash_0.wav@0.000-0.320"]
    assert [r.spk for r in files["val_pos.txt"]] == ["bb"] and [r.spk for r in files["val_hard.txt"]] == ["dd"]
    assert [r.spk for r in files["train_hard.txt"]] == ["aa"]
    assert files["train_neg.txt"] == splits.read_split(earlier / "train_neg.txt")[:1]
    assert seconds["speech/sc/yes/aa_nohash_0.wav@0.000-0.320"] == 0.32


def test_hard_negatives_drawn_after_val_add_to_train_and_keep_every_earlier_draw(tmp_path: Path) -> None:
    root, hop = tmp_path / "speech" / "sc", grid.HOP_SAMPLES
    tone = 0.3 * np.sin(2 * np.pi * 300.0 * np.arange(20 * hop) / grid.SAMPLE_RATE_HZ)
    said = {"on": ["aa_nohash_0"], "no": ["aa_nohash_0", "dd_nohash_0"], "up": ["ee_nohash_0", "ff_nohash_0"]}
    for word, stems in said.items():
        for stem in stems:
            write_wav(root / word / f"{stem}.wav", np.concatenate([tone, np.zeros(30 * hop)]))
    (root / "testing_list.txt").write_text("no/dd_nohash_0.wav\nup/ff_nohash_0.wav\n", encoding="utf-8")
    (root / "validation_list.txt").write_text("", encoding="utf-8")
    earlier = tmp_path / "splits" / "wake" / "v4"
    earlier.mkdir(parents=True)
    for name in data.NEGATIVES:
        (earlier / name).write_text(f"speech/vivos/{name}.wav\tVIVOSSPK01\t-\tpublic\n", encoding="utf-8")
    cfg = load_yaml(CONFIG)
    paths = {"raw": tmp_path, "splits": tmp_path / "splits"}

    def cut(**more: int) -> dict:
        pilot = {"dir": "speech/sc", "negatives_from": "v4", "hard_clips": {"train": 1, "val": 1, **more}}
        return data.speech_commands_files(
            cfg | {"word": "on", "split": cfg["split"] | {"speech_commands": pilot}}, paths
        )[0]

    base, more = cut(), cut(train_after=5)
    assert more["val_hard.txt"] == base["val_hard.txt"] and len(base["train_hard.txt"]) == 1
    assert {r.item for r in base["train_hard.txt"]} < {r.item for r in more["train_hard.txt"]}
    assert len(more["train_hard.txt"]) == 2


def board_manifest(folder: Path, rows: list[tuple[str, str, int, str]]) -> dict:
    """A board manifest b.csv of (session, kind, pcm_shift, prompt) rows on board_b, and eval.board pointing at it."""
    lines = ["session,board,pcm_shift,kind,prompt"] + [f"{s},board_b,{p},{k},{t}" for s, k, p, t in rows]
    (folder / "b.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"manifest": "b.csv", "pcm_shift": 13}


def test_board_rows_take_only_wake_sessions_saying_the_word_at_the_products_shift(tmp_path: Path) -> None:
    cfg = load_yaml(CONFIG) | {"word": "vừng ơi"}
    rows = [("s1", "wake", 13, "Vừng ơi!"), ("s2", "wake", 13, "vừng ơi giọng nhỏ"), ("s3", "wake", 13, "trợ lý")]
    rows += [("s4", "wake", 16, "vừng ơi"), ("s5", "neg", 13, "mừng ơi | vừng")]
    board, paths = board_manifest(tmp_path, rows), {"manifests": tmp_path}
    cfg["eval"] = cfg["eval"] | {"board": board | {"train": ["s2", "s1"]}}
    assert [r["session"] for r in data.board_rows(cfg, paths)] == ["s1", "s2"]
    for wrong, refusal in (("s3", "no wake session"), ("s5", "no wake session"), ("s4", "pcm_shift"), ("s9", "b.csv")):
        cfg["eval"]["board"]["train"] = [wrong]
        with pytest.raises(ValueError, match=refusal):
            data.board_rows(cfg, paths)
    cfg["eval"]["board"]["train"] = []
    assert data.board_rows(cfg, paths) == []


def test_a_board_utterance_ends_after_its_last_hop_near_its_loudest() -> None:
    clean = np.zeros((130, grid.HOP_SAMPLES), dtype=np.int16)
    vad = np.zeros(130, dtype=bool)
    clean[20:40], clean[40:44], clean[44:50] = 8000, 200, 20
    vad[20:60] = True
    clean[100:110], vad[100:125] = 3000, True
    assert data.word_spans(clean, vad, 40.0) == [(20, 44), (100, 110)]
    assert data.word_spans(clean, vad, 20.0) == [(20, 40), (100, 110)]


def test_the_board_cut_labels_each_utterance_at_its_word_end_and_records_its_sessions(tmp_path, monkeypatch) -> None:
    cfg = load_yaml(CONFIG) | {"word": "vừng ơi"}
    board = board_manifest(tmp_path, [("s1", "wake", 13, "vừng ơi"), ("s2", "wake", 13, "vừng ơi")])
    cfg["eval"] = cfg["eval"] | {"board": board | {"train": ["s1", "s2"]}}
    paths = {"manifests": tmp_path, "raw": tmp_path / "raw"}

    def chain(session: Path, chain_cfg, mel) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        hops = 120 if session.name == "s1" else 90
        clean, figures = np.zeros((hops, grid.HOP_SAMPLES), np.int16), np.zeros((hops, 3), np.int8)
        clean[20:40], figures[20:60, 0] = 8000, 1
        return clean, figures, np.full((hops, 4), -8.0, np.float32)

    monkeypatch.setattr(data, "session_chain", chain)
    out = tmp_path / data.BOARD_SET
    assert data.cut_board(cfg, paths, out).endswith("2 utterances of 2 sessions")
    (shard,) = data.load_set(out, True, (2, 3), "float16")
    assert [(i["item"], i["origin"]) for i in shard.items] == [("board/s1#0", "board"), ("board/s2#0", "board")]
    assert np.flatnonzero(shard.labels).tolist() == [38, 39, 40, 41, 42, 158, 159, 160, 161, 162]
    rows, device_cfg, dtype = data.board_rows(cfg, paths), load_device(cfg["features"]), cfg["simulate"]["train_dtype"]
    assert data.board_built_as(out, device_cfg, rows, cfg["split"]["end_below_peak_db"], dtype)
    assert not data.board_built_as(out, device_cfg, rows[:1], cfg["split"]["end_below_peak_db"], dtype)
    assert not data.board_built_as(out, device_cfg, rows, 30.0, dtype)
