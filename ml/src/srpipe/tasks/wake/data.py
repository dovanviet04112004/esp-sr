"""Split wake/v<n> (KEHOACH 1.3, 3.11): kept TTS clips of E11-T7 and public speech, by role and label.

split sorts TTS positives and near misses by the role of their voice, takes the extract's clips of the wake word as
real positives, public negatives from the corpora of the reference voices, test_neg from Common Voice and VIVOS test,
and near-miss families into hard files when on; simulate runs each file through the board simulation, linking a file
another version built the same way. Run: python -m srpipe.tasks.wake.data {split,simulate}
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from srpipe.core import corpus, extract, screen, splits
from srpipe.core.audio_io import ItemReader
from srpipe.core.config import CONFIGS, data_paths, load_yaml
from srpipe.generated import grid, lang_vi
from srpipe.lang import g2p
from srpipe.lang.normalize import LangError, normalize
from srpipe.scenes import device, room
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
    built = yaml.safe_load((folder / "manifest.yaml").read_text(encoding="utf-8"))["sha256"]
    shards = []
    for name in sorted(n for n in built if n.endswith(".items.jsonl")):
        listing = folder / name
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


def synth_item(folder: str, clip: dict) -> str:
    """A TTS clip's item under interim/, cut at cut_s when the split trimmed it."""
    item = f"wake/{folder}/{clip['engine']}/{clip['id']}.wav"
    return cut_item(item, 0.0, clip["cut_s"]) if "cut_s" in clip else item


def synth_rows(clips: list[dict], folder: str, roles: dict[str, str]) -> dict[str, list[splits.Row]]:
    """Kept clips of one synth manifest as rows under interim/, by the role of their voice; train when unknown."""
    rows: dict[str, list[splits.Row]] = {"train": [], "val": []}
    for c in clips:
        if c["kept"]:
            voice = voice_of(c)
            rows[roles.get(voice, "train")].append(splits.Row(synth_item(folder, c), voice, splits.ABSENT, SYNTH))
    return rows


def speech_end_s(x: np.ndarray, below_peak_db: float) -> float:
    """Seconds to the end of the last hop within below_peak_db of the loudest hop."""
    last = int(np.flatnonzero(room.active_hops(x, below_peak_db))[-1])
    return (last + 1) * grid.HOP_SAMPLES / grid.SAMPLE_RATE_HZ


def trimmed(clips: list[dict], folder: str, cfg: dict, interim: Path) -> list[dict]:
    """The clips, each kept one given cut_s at its word's end when that falls inside it."""
    below_peak_db, reader = cfg["split"]["end_below_peak_db"], ItemReader(interim)
    out = []
    for c in clips:
        if c["kept"]:
            end = speech_end_s(reader.read(synth_item(folder, c)), below_peak_db)
            c = c | ({"cut_s": round(end, 3)} if end < c["seconds"] else {})
        out.append(c)
    return out


def cut_item(item: str, start_s: float, end_s: float) -> str:
    """The item that reads only [start_s, end_s) of item (core.audio_io.ItemReader)."""
    return f"{item}@{start_s:.3f}-{end_s:.3f}"


def says_word(clip: corpus.Clip, word: list[tuple[str, ...]]) -> bool:
    """Whether the clip's text holds a phrase that sounds like word (corpus.sounds), however it is spelled."""
    return corpus.says(corpus.sounds(clip.text or ""), word)


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


def build(
    cfg: dict,
    public: list[corpus.Clip],
    seconds: dict[str, float],
    manifests: dict[str, list[dict]],
    real: tuple[str, ...] = (),
) -> dict:
    """Every split file of the version as rows, from the screened public clips, their lengths, the synth manifests by
    folder and the items of the real positives, which name no speaker and so only feed train."""
    spec, word = cfg["split"], corpus.sounds(cfg["word"])
    usable = [c for c in public if not says_word(c, word)]
    learning = [c for c in usable if c.item.startswith(tuple(spec["learning"]))]
    vivos = {c.speaker for c in learning if c.speaker}
    presets = {voice_of(c) for clips in manifests.values() for c in clips if c["id"].startswith("preset_")}
    roles = splits.speaker_roles(vivos, {"val": spec["val_speakers"]}, "train", spec["seed"])
    roles |= splits.speaker_roles(presets, {"val": spec["val_speakers"]}, "train", spec["seed"])
    pos, near = (synth_rows(manifests[synth.SETS[s]], synth.SETS[s], roles) for s in ("positives", "negatives"))
    pos["train"] += [splits.Row(item, splits.ABSENT, splits.ABSENT, PUBLIC) for item in real]
    heard = [c for c in learning if roles.get(c.speaker or "", "train") == "train"]
    val = [c for c in learning if roles.get(c.speaker or "") == "val"]
    drawn = draw_hours(heard, seconds, spec["negative_hours"], spec["seed"])
    test = [c for c in usable if c.item.startswith(tuple(spec["test_neg"]))]
    moved = moved_to_val(test, spec.get("val_from_test", {}), spec["seed"])
    kept = {c.item for c in test} - {c.item for c in moved}
    files = {
        "train_pos.txt": pos["train"],
        "train_neg.txt": near["train"] + splits.clip_rows(drawn, PUBLIC),
        "val_pos.txt": pos["val"],
        "val_neg.txt": near["val"] + splits.clip_rows(val + moved, PUBLIC),
        "test_neg.txt": splits.clip_rows([c for c in test if c.item in kept], PUBLIC),
    }
    if "hard" not in spec:
        return files
    folder = synth.SETS["hard"]
    families = synth_rows(manifests.get(folder, []), folder, roles)
    held = [corpus.words(t) for t in cfg["synth"]["hard"]["held_out"]]
    patterns = [re.compile(p) for p in spec["hard"]]
    mined = {role: [c for c in clips if is_hard(c, patterns, held)] for role, clips in (("train", heard), ("val", val))}
    for role in ("train", "val"):
        files[f"{role}_hard.txt"] = families[role] + near[role] + splits.clip_rows(mined[role], PUBLIC)
    # val scores val_neg and val_hard together, so a clip in both would count twice among its false accepts.
    counted = {r.item for r in files["val_neg.txt"]}
    files["val_hard.txt"] = [r for r in files["val_hard.txt"] if r.item not in counted]
    return files


def moved_to_val(test: list[corpus.Clip], shares: dict[str, float], seed: int) -> list[corpus.Clip]:
    """The test clips of the speakers drawn with the seed into val, a share of each corpus's speakers taken whole."""
    moved = []
    for prefix, share in shares.items():
        clips = [c for c in test if c.item.startswith(prefix)]
        roles = splits.speaker_roles({c.speaker for c in clips if c.speaker}, {"val": share}, "test", seed)
        moved += [c for c in clips if roles.get(c.speaker or "") == "val"]
    return moved


def is_hard(clip: corpus.Clip, patterns: list[re.Pattern], held: list[list[str]]) -> bool:
    """Whether the clip's words hold a near-miss family and no held-out phrase."""
    said = corpus.words(clip.text or "")
    text = " ".join(said)
    return any(p.search(text) for p in patterns) and not any(corpus.says(said, h) for h in held)


def sentence_units(clips: list[corpus.Clip], items: set[str], dialect: str) -> dict[str, list[int]]:
    """lang_vi unit ids of every clip among items, read in dialect; a text lang_vi refuses, or none, is left out."""
    region = lang_vi.DIALECTS.index(dialect)
    units = {}
    for clip in clips:
        if clip.item not in items or not clip.text:
            continue
        try:
            said = g2p.g2p(normalize(clip.text, region), region)
        except LangError:
            continue
        if said:
            units[clip.item] = said
    return units


def notes(cfg: dict, files: dict[str, list[splits.Row]], seconds: dict[str, float]) -> str:
    """SPLIT.md ahead of the checksums: rules, seed, command and each file's size."""
    spec = cfg["split"]
    table = "\n".join(
        f"| `{name}` | {len(rows)} | {splits.hours(rows, seconds):.2f} | {sum(r.origin == SYNTH for r in rows)} |"
        for name, rows in files.items()
    )
    real = (
        f", và mẩu người thật nói từ ấy của kho trích `{spec['real_pos']}` (`raw/speech/{spec['real_pos']}/`), cắt"
        "\n  đúng hai tiếng; kho ấy không có mã người nói nên mẩu chỉ vào `train`"
        if "real_pos" in spec
        else ""
    )
    moved = "".join(
        f"\n- {share:.0%} người nói của {prefix} rời `test_neg` sang `val_neg` trọn vẹn, rút theo seed, để mục tiêu báo"
        " nhầm của\n  `val` dựa trên nhiều lần vượt (KẾ HOẠCH §3.11)."
        for prefix, share in spec.get("val_from_test", {}).items()
    )
    hard = ""
    if "hard" in spec:
        held = ", ".join(f'"{t}"' for t in cfg["synth"]["hard"]["held_out"])
        hard = (
            "\n- `train_hard`, `val_hard` (KẾ HOẠCH §3.11): mẩu TTS có `kept` của `interim/wake/synth_hard`, âm bản"
            " gần âm của\n  `interim/wake/synth_neg`, và câu của kho học có lời khớp một mẫu của `split.hard`; không"
            f" câu nào chứa\n  {held}, để phiên gần âm thu qua board đo được mô hình tổng quát hoá. `val_hard` bỏ"
            " mẩu đã có trong\n  `val_neg`, vì `val` chấm báo nhầm trên cả hai file và mỗi mẩu chỉ được đếm một lần."
        )
    return f"""# wake/{spec["version"]}

Dựng bằng `python -m srpipe.tasks.wake.data` (`make splits`), seed {spec["seed"]}, cấu hình mục `split` của
`ml/configs/models/wake.yaml`. Luật ở KẾ HOẠCH §1.3:

- Chỉ mẩu qua sàng lọc (`interim/screen/rejects.tsv`); không âm bản nào có lời đọc như "{cfg["word"]}" theo giọng
  Bắc, dù viết cách nào.
- Dương: mẩu TTS có `kept` của `interim/wake/synth_pos`, cắt bỏ khoảng lặng sau từ (`@0-cuối`){real}; âm bản gần
  âm: của `interim/wake/synth_neg`. Mẩu dương nào cũng chỉ có từ đánh thức và dừng ở âm cuối của nó.
- {spec["val_speakers"]:.0%} người nói VIVOS train và {spec["val_speakers"]:.0%} giọng có sẵn của VieNeu vào `val`
  trọn vẹn, cùng mọi giọng nhân bản từ họ; giọng nhân bản từ kho không có mã người nói chỉ vào `train`.
- `train_neg` rút {spec["negative_hours"]} giờ lời nói ngẫu nhiên từ {", ".join(spec["learning"])}.
- `test_neg` là {", ".join(spec["test_neg"])}: không kho nào đã làm giọng mẫu cho TTS. Đủ 24 giờ khi thêm nền phòng
  thu qua board (E11-T6); `test_pos` cũng chờ bản thu ấy.{moved}{hard}

| File | Mẩu | Giờ | Mẩu TTS |
|---|---|---|---|
{table}
"""


def built_as(out: Path, device_cfg: dict, split_file: Path, repeats: int) -> bool:
    """Whether out holds a finished simulation of split_file with this config and number of passes."""
    if not (out / "manifest.yaml").exists():
        return False
    body = yaml.safe_load((out / "manifest.yaml").read_text(encoding="utf-8"))
    same_split = body["split"]["sha256"] == splits.sha256_of(split_file)
    return body["config"] == device_cfg and same_split and body.get("repeats", 1) == repeats


def simulate(cfg: dict, paths: dict[str, Path]) -> None:
    """Every file of the split through the board simulation into processed/wake/<split>/<file>, smallest first,
    each as many passes as simulate.repeats asks; a file already built the same way is left as it is."""
    spec = cfg["simulate"]
    device_cfg = load_yaml(CONFIGS / cfg["features"])
    for split_file in sorted((paths["splits"] / "wake" / spec["split"]).glob("*.txt"), key=lambda f: f.stat().st_size):
        out = paths["processed"] / "wake" / spec["split"] / split_file.stem
        repeats = spec["repeats"].get(split_file.stem, 1)
        if built_as(out, device_cfg, split_file, repeats):
            print(f"{out}: already built", flush=True)
            continue
        twins = [d for d in sorted(out.parent.parent.glob(f"*/{split_file.stem}")) if d != out]
        twin = next((d for d in twins if built_as(d, device_cfg, split_file, repeats)), None)
        if twin is not None:
            print(f"{out}: linked from {device.link_build(twin, out)}", flush=True)
            continue
        print(device.build(device_cfg, split_file, paths["raw"], paths["interim"], out, spec["workers"], repeats))


def real_positives(cfg: dict, raw: Path) -> dict[str, float]:
    """Items under raw/ of the extract's clips of real voices that say the wake word, however spelled, each cut at the
    end of its speech as the TTS positives are, and their lengths in seconds."""
    folder, word = extract.clips_folder(raw, cfg["split"]["real_pos"]), corpus.sounds(cfg["word"])
    clips = [c for c in extract.read_index(folder) if c["origin"] == PUBLIC and corpus.sounds(c["phrase"]) == word]
    reader, items = ItemReader(raw), {}
    for c in clips:
        item = str((folder / c["file"]).relative_to(raw))
        end = round(speech_end_s(reader.read(item), cfg["split"]["end_below_peak_db"]), 3)
        items[cut_item(item, 0.0, end) if end < c["seconds"] else item] = min(end, c["seconds"])
    return items


def cut_split(cfg: dict, paths: dict[str, Path]) -> int:
    screening = load_yaml(screen.CONFIG)
    public = screen.kept_clips(screening, paths, "speech")
    seconds = screen.lengths(screening, paths, "speech")
    manifests = {}
    real = real_positives(cfg, paths["raw"]) if "real_pos" in cfg["split"] else {}
    seconds |= real
    for folder in (synth.SETS["positives"], synth.SETS["negatives"], synth.SETS["hard"]):
        listing = paths["interim"] / "wake" / folder / "manifest.yaml"
        if folder == synth.SETS["hard"] and not listing.exists():
            continue
        clips = yaml.safe_load(listing.read_text(encoding="utf-8"))["clips"]
        if folder == synth.SETS["positives"]:
            clips = trimmed(clips, folder, cfg, paths["interim"])
        manifests[folder] = clips
        seconds |= {synth_item(folder, c): c.get("cut_s", c["seconds"]) for c in clips}
    files = build(cfg, public, seconds, manifests, tuple(real))
    out = paths["splits"] / "wake" / cfg["split"]["version"]
    splits.write_version(out, files, notes(cfg, files, seconds))
    problems = splits.check_version(out)
    print("\n".join(f"{name}: {len(rows)} rows, {splits.hours(rows, seconds):.2f} h" for name, rows in files.items()))
    print("\n".join(problems) or f"{out}: every rule of KEHOACH 1.3 holds")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["split", "simulate"])
    step = parser.parse_args(argv).step
    cfg, paths = load_yaml(CONFIG), data_paths()
    if step == "simulate":
        simulate(cfg, paths)
        return 0
    return cut_split(cfg, paths)


if __name__ == "__main__":
    raise SystemExit(main())
