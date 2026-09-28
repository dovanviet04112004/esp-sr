"""Split wake/v<n> (KEHOACH 1.3, 3.11): kept TTS clips of E11-T7 and public speech, by role and label.

Positives and near misses by the role of their voice, public negatives from the corpora that gave the reference
voices, test_neg from Common Voice and VIVOS test; no negative says the wake word. test_pos waits for E11-T6.
Run: python -m srpipe.tasks.wake.data
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from srpipe.core import corpus, screen, splits
from srpipe.core.config import data_paths, load_yaml
from srpipe.generated import grid
from srpipe.tasks.wake import CONFIG, synth

PUBLIC, SYNTH = "public", "synth"


@dataclass
class Shard:
    """One shard of processed/wake/<split>/<file>: log-mel per hop, the per-hop target, and its items."""

    features: np.ndarray  # (hops, n_bands), natural-log mel as the device computes it
    labels: np.ndarray  # (hops,) uint8, 1 around the end of a positive's speech
    items: list[dict]
    positive: bool


def label_hops(label_s: list[float]) -> tuple[int, int]:
    """Hops before and after the end of a positive's speech that are labelled 1."""
    rate = grid.SAMPLE_RATE_HZ / grid.HOP_SAMPLES
    return round(label_s[0] * rate), round(label_s[1] * rate)


def load_set(folder: Path, positive: bool, around: tuple[int, int], dtype: str) -> list[Shard]:
    """Every shard of one processed split file, labelled, its features held as dtype; the folder must hold the
    manifest of a finished build."""
    if not (folder / "manifest.yaml").exists():
        raise FileNotFoundError(f"{folder} has no manifest.yaml: run make wake-features to the end")
    shards = []
    for listing in sorted(folder.glob("shard_*.items.jsonl")):
        stem = str(listing).removesuffix(".items.jsonl")
        features = np.load(stem + ".features.npy").astype(dtype)
        items = [json.loads(line) for line in listing.read_text(encoding="utf-8").splitlines()]
        labels = np.zeros(len(features), dtype=np.uint8)
        if positive:
            for item in items:
                end = item["frame_offset"] + item["speech_frames"][1]
                labels[max(0, end - around[0]) : end + around[1]] = 1
        shards.append(Shard(features, labels, items, positive))
    return shards


def voice_of(clip: dict) -> str:
    """The person a TTS clip sounds like: the VIVOS speaker it was cloned from, the VieNeu preset it reads with, or
    ABSENT for a clone of a corpus without speaker ids."""
    if clip["id"].startswith("preset_"):
        return "vieneu_" + "_".join(clip["id"].split("_")[:2])
    return clip["speaker"] if clip["speaker"].startswith("VIVOS") else splits.ABSENT


def synth_rows(clips: list[dict], folder: str, roles: dict[str, str]) -> dict[str, list[splits.Row]]:
    """Kept clips of one synth manifest as rows under interim/, by the role of their voice; train when unknown."""
    rows: dict[str, list[splits.Row]] = {"train": [], "val": []}
    for c in clips:
        if c["kept"]:
            voice = voice_of(c)
            item = f"wake/{folder}/{c['engine']}/{c['id']}.wav"
            rows[roles.get(voice, "train")].append(splits.Row(item, voice, splits.ABSENT, SYNTH))
    return rows


def says_word(clip: corpus.Clip, word: list[str]) -> bool:
    return corpus.says(corpus.words(clip.text or ""), word)


def draw_hours(clips: list[corpus.Clip], seconds: dict[str, float], hours: float, seed: int) -> list[corpus.Clip]:
    """Clips in a seeded random order until their length passes hours, then back in listing order."""
    order = np.random.default_rng(seed).permutation(len(clips))
    taken, total = [], 0.0
    for k in order:
        if total >= hours * splits.SECONDS_PER_HOUR:
            break
        taken.append(k)
        total += seconds[clips[k].item]
    return [clips[k] for k in sorted(taken)]


def build(cfg: dict, public: list[corpus.Clip], seconds: dict[str, float], manifests: dict[str, list[dict]]) -> dict:
    """Every split file of the version as rows, from the screened public clips, their lengths and the synth
    manifests by folder."""
    spec, word = cfg["split"], corpus.words(cfg["word"])
    usable = [c for c in public if not says_word(c, word)]
    learning = [c for c in usable if c.item.startswith(tuple(spec["learning"]))]
    vivos = {c.speaker for c in learning if c.speaker}
    presets = {voice_of(c) for clips in manifests.values() for c in clips if c["id"].startswith("preset_")}
    roles = splits.speaker_roles(vivos, {"val": spec["val_speakers"]}, "train", spec["seed"])
    roles |= splits.speaker_roles(presets, {"val": spec["val_speakers"]}, "train", spec["seed"])
    pos, near = (synth_rows(manifests[synth.SETS[s]], synth.SETS[s], roles) for s in ("positives", "negatives"))
    heard = [c for c in learning if roles.get(c.speaker or "", "train") == "train"]
    val = [c for c in learning if roles.get(c.speaker or "") == "val"]
    drawn = draw_hours(heard, seconds, spec["negative_hours"], spec["seed"])
    test = [c for c in usable if c.item.startswith(tuple(spec["test_neg"]))]
    return {
        "train_pos.txt": pos["train"],
        "train_neg.txt": near["train"] + splits.clip_rows(drawn, PUBLIC),
        "val_pos.txt": pos["val"],
        "val_neg.txt": near["val"] + splits.clip_rows(val, PUBLIC),
        "test_neg.txt": splits.clip_rows(test, PUBLIC),
    }


def notes(cfg: dict, files: dict[str, list[splits.Row]], seconds: dict[str, float]) -> str:
    """SPLIT.md ahead of the checksums: rules, seed, command and each file's size."""
    spec = cfg["split"]
    table = "\n".join(
        f"| `{name}` | {len(rows)} | {splits.hours(rows, seconds):.2f} | {sum(r.origin == SYNTH for r in rows)} |"
        for name, rows in files.items()
    )
    return f"""# wake/{spec["version"]}

Dựng bằng `python -m srpipe.tasks.wake.data` (`make splits`), seed {spec["seed"]}, cấu hình mục `split` của
`ml/configs/models/wake.yaml`. Luật ở KẾ HOẠCH §1.3:

- Chỉ mẩu qua sàng lọc (`interim/screen/rejects.tsv`); không âm bản nào có lời chứa "{cfg["word"]}".
- Dương: mẩu TTS có `kept` của `interim/wake/synth_pos`; âm bản gần âm: của `interim/wake/synth_neg`.
- {spec["val_speakers"]:.0%} người nói VIVOS train và {spec["val_speakers"]:.0%} giọng có sẵn của VieNeu vào `val`
  trọn vẹn, cùng mọi giọng nhân bản từ họ; giọng nhân bản từ kho không có mã người nói chỉ vào `train`.
- `train_neg` rút {spec["negative_hours"]} giờ lời nói ngẫu nhiên từ {", ".join(spec["learning"])}.
- `test_neg` là {", ".join(spec["test_neg"])}: không kho nào đã làm giọng mẫu cho TTS. Đủ 24 giờ khi thêm nền phòng
  thu qua board (E11-T6); `test_pos` cũng chờ bản thu ấy.

| File | Mẩu | Giờ | Mẩu TTS |
|---|---|---|---|
{table}
"""


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    cfg, paths, screening = load_yaml(CONFIG), data_paths(), load_yaml(screen.CONFIG)
    public = screen.kept_clips(screening, paths, "speech")
    seconds = screen.lengths(screening, paths, "speech")
    manifests = {}
    for folder in (synth.SETS["positives"], synth.SETS["negatives"]):
        clips = yaml.safe_load((paths["interim"] / "wake" / folder / "manifest.yaml").read_text(encoding="utf-8"))[
            "clips"
        ]
        manifests[folder] = clips
        seconds |= {f"wake/{folder}/{c['engine']}/{c['id']}.wav": c["seconds"] for c in clips}
    files = build(cfg, public, seconds, manifests)
    out = paths["splits"] / "wake" / cfg["split"]["version"]
    splits.write_version(out, files, notes(cfg, files, seconds))
    problems = splits.check_version(out)
    print("\n".join(f"{name}: {len(rows)} rows, {splits.hours(rows, seconds):.2f} h" for name, rows in files.items()))
    print("\n".join(problems) or f"{out}: every rule of KEHOACH 1.3 holds")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
