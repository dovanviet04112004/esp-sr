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
