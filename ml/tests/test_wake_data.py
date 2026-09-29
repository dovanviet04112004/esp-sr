"""Wake split: voices follow their speaker's role, clones of speakerless corpora train, negatives never say the wake
word, test_neg comes only from its corpora, and train_neg stops at its hours."""

from __future__ import annotations

from srpipe.core import corpus, splits
from srpipe.core.config import load_yaml
from srpipe.tasks.wake import CONFIG, data, synth


def synth_clip(kind: str, speaker: str, n: int) -> dict:
    ident = f"preset_{n:02d}_t0_s0" if kind == "preset" else f"clone_{speaker}_t0_s{n}"
    return {"engine": "f5", "id": ident, "speaker": speaker, "kept": n != 3, "seconds": 1.0}


def test_the_wake_split_keeps_speakers_words_and_sources_apart() -> None:
    cfg = load_yaml(CONFIG)
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


def test_hard_files_mine_the_families_by_role_and_leave_the_other_five_as_they_were() -> None:
    cfg = load_yaml(CONFIG)
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


def test_simulate_links_a_twin_build_instead_of_running_it_again(tmp_path, monkeypatch) -> None:
    cfg = load_yaml(CONFIG)
    device_cfg = load_yaml(data.CONFIGS / cfg["features"])
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
    data.simulate(cfg | {"simulate": {"split": "v2", "workers": 1, "repeats": {}}}, paths)
    linked = tmp_path / "processed" / "wake" / "v2" / "val_pos" / "shard_00000.features.npy"
    assert linked.stat().st_ino == (built / "shard_00000.features.npy").stat().st_ino
