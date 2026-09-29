"""Split wake/v<n> (KEHOACH 1.3, 3.11): kept TTS clips of E11-T7 and public speech, by role and label.

corpus cuts learning clips that say the wake word around it as real positives; split sorts positives and near misses
by the role of their voice, public negatives from the corpora of the reference voices, test_neg from Common Voice and
VIVOS test, and near-miss families into hard files when on; simulate runs each file through the board simulation,
linking a file another version built the same way. Run: python -m srpipe.tasks.wake.data {corpus,split,simulate}
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from srpipe.core import corpus, screen, splits
from srpipe.core.audio_io import ItemReader, write_wav
from srpipe.core.config import CONFIGS, data_paths, load_yaml
from srpipe.generated import grid, lang_vi
from srpipe.lang import g2p
from srpipe.lang.normalize import LangError, normalize
from srpipe.scenes import device
from srpipe.tasks.wake import CONFIG, synth
from srpipe.tts import CONFIG as TTS_CONFIG
from srpipe.tts import engines

PUBLIC, SYNTH = "public", "synth"
CORPUS_POS = "corpus_pos"


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


def synth_rows(clips: list[dict], folder: str, roles: dict[str, str]) -> dict[str, list[splits.Row]]:
    """Kept clips of one synth manifest as rows under interim/, by the role of their voice; train when unknown."""
    rows: dict[str, list[splits.Row]] = {"train": [], "val": []}
    for c in clips:
        if c["kept"]:
            voice = voice_of(c)
            item = f"wake/{folder}/{c['engine']}/{c['id']}.wav"
            rows[roles.get(voice, "train")].append(splits.Row(item, voice, splits.ABSENT, SYNTH))
    return rows


def corpus_rows(clips: list[dict], roles: dict[str, str]) -> dict[str, list[splits.Row]]:
    """Kept cuts of the corpus positives as public rows, by the role of their speaker; train when it has none."""
    rows: dict[str, list[splits.Row]] = {"train": [], "val": []}
    for c in clips:
        if c["kept"]:
            rows[roles.get(c["speaker"], "train")].append(splits.Row(c["cut"], c["speaker"], splits.ABSENT, PUBLIC))
    return rows


def spoken_at(words: list[dict], phrase: list[str]) -> tuple[float, float] | None:
    """Start and end in seconds of the first run of heard words whose syllables spell phrase, or None."""
    heard = [(syllable, w) for w in words for syllable in corpus.words(w["word"])]
    for k in range(len(heard) - len(phrase) + 1):
        if [syllable for syllable, _ in heard[k : k + len(phrase)]] == phrase:
            return heard[k][1]["start"], heard[k + len(phrase) - 1][1]["end"]
    return None


def cut_item(item: str, start_s: float, end_s: float) -> str:
    """The item that reads only [start_s, end_s) of item (core.audio_io.ItemReader)."""
    return f"{item}@{start_s:.3f}-{end_s:.3f}"


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
    tts = [clips for folder, clips in manifests.items() if folder != CORPUS_POS]
    presets = {voice_of(c) for clips in tts for c in clips if c["id"].startswith("preset_")}
    roles = splits.speaker_roles(vivos, {"val": spec["val_speakers"]}, "train", spec["seed"])
    roles |= splits.speaker_roles(presets, {"val": spec["val_speakers"]}, "train", spec["seed"])
    pos, near = (synth_rows(manifests[synth.SETS[s]], synth.SETS[s], roles) for s in ("positives", "negatives"))
    real = corpus_rows(manifests.get(CORPUS_POS, []), roles)
    pos = {role: pos[role] + real[role] for role in pos}
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
        ", và câu kho nói từ đánh thức cắt quanh từ ấy (`interim/wake/corpus_pos`,"
        "\n  item kết thúc bằng `@đầu-cuối` giây)"
        if "corpus_pos" in spec
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

- Chỉ mẩu qua sàng lọc (`interim/screen/rejects.tsv`); không âm bản nào có lời chứa "{cfg["word"]}".
- Dương: mẩu TTS có `kept` của `interim/wake/synth_pos`{real}; âm bản gần âm: của `interim/wake/synth_neg`.
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
            print(f"{out}: linked from {link(twin, out)}", flush=True)
            continue
        print(device.build(device_cfg, split_file, paths["raw"], paths["interim"], out, spec["workers"], repeats))


def link(built: Path, out: Path) -> Path:
    """Hard-link every file of a finished build into out, which must hold none of them: no copy, no second run."""
    out.mkdir(parents=True, exist_ok=True)
    for f in sorted(built.iterdir()):
        os.link(f, out / f.name)
    return built


def corpus_positives(cfg: dict, paths: dict[str, Path]) -> Path:
    """Cut every learning clip that says the wake word from context_s before the word to tail_s after it, by the
    checker's word times, then hear each cut back: kept when it still says the word. The audio stays in raw/, a cut
    being its item with @<start>-<end>; interim/wake/corpus_pos/manifest.yaml lists every clip found (KEHOACH 3.11)."""
    spec, word = cfg["split"]["corpus_pos"], corpus.words(cfg["word"])
    tts, reader = load_yaml(TTS_CONFIG), ItemReader(paths["raw"])
    public = screen.kept_clips(load_yaml(screen.CONFIG), paths, "speech")
    found = [c for c in public if c.item.startswith(tuple(cfg["split"]["learning"])) and says_word(c, word)]
    out = paths["interim"] / "wake" / CORPUS_POS
    work, seconds = out / "work", {}
    for k, c in enumerate(found):
        x = reader.read(c.item)
        seconds[c.item] = len(x) / grid.SAMPLE_RATE_HZ
        write_wav(work / "whole" / f"{k:05d}.wav", x)
    asked = [{"id": str(k), "wav": str(work / "whole" / f"{k:05d}.wav")} for k in range(len(found))]
    times = engines.words(asked, tts, work, paths["cache"])
    rows = []
    for k, c in enumerate(found):
        row = {"item": c.item, "speaker": c.speaker or splits.ABSENT, "text": c.text, "kept": False}
        if span := spoken_at(times[str(k)], word):
            start, end = max(0.0, span[0] - spec["context_s"]), min(seconds[c.item], span[1] + spec["tail_s"])
            row |= {"word_s": [round(t, 3) for t in span], "cut": cut_item(c.item, start, end)}
            row["seconds"] = round(end - start, 3)
            write_wav(work / "cut" / f"{k:05d}.wav", reader.read(row["cut"]))
        rows.append(row)
    cuts = [
        {"id": f"cut/{k}", "wav": str(work / "cut" / f"{k:05d}.wav"), "targets": [cfg["word"]]}
        for k, row in enumerate(rows)
        if "cut" in row
    ]
    heard = engines.hear(cuts, tts, work, paths["cache"])
    for k, row in enumerate(rows):
        if "cut" in row:
            row["heard"] = heard[f"cut/{k}"]["text"]
            row["kept"] = corpus.says(corpus.words(row["heard"]), word)
    manifest = out / "manifest.yaml"
    body = {"word": cfg["word"], "corpus_pos": spec, "clips": rows}
    manifest.write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return manifest


def cut_split(cfg: dict, paths: dict[str, Path]) -> int:
    screening = load_yaml(screen.CONFIG)
    public = screen.kept_clips(screening, paths, "speech")
    seconds = screen.lengths(screening, paths, "speech")
    manifests = {}
    listing = paths["interim"] / "wake" / CORPUS_POS / "manifest.yaml"
    if "corpus_pos" in cfg["split"]:
        manifests[CORPUS_POS] = yaml.safe_load(listing.read_text(encoding="utf-8"))["clips"]
        seconds |= {c["cut"]: c["seconds"] for c in manifests[CORPUS_POS] if c["kept"]}
    for folder in (synth.SETS["positives"], synth.SETS["negatives"], synth.SETS["hard"]):
        listing = paths["interim"] / "wake" / folder / "manifest.yaml"
        if folder == synth.SETS["hard"] and not listing.exists():
            continue
        clips = yaml.safe_load(listing.read_text(encoding="utf-8"))["clips"]
        manifests[folder] = clips
        seconds |= {f"wake/{folder}/{c['engine']}/{c['id']}.wav": c["seconds"] for c in clips}
    files = build(cfg, public, seconds, manifests)
    out = paths["splits"] / "wake" / cfg["split"]["version"]
    splits.write_version(out, files, notes(cfg, files, seconds))
    problems = splits.check_version(out)
    print("\n".join(f"{name}: {len(rows)} rows, {splits.hours(rows, seconds):.2f} h" for name, rows in files.items()))
    print("\n".join(problems) or f"{out}: every rule of KEHOACH 1.3 holds")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["corpus", "split", "simulate"])
    step = parser.parse_args(argv).step
    cfg, paths = load_yaml(CONFIG), data_paths()
    if step == "corpus":
        manifest = corpus_positives(cfg, paths)
        clips = yaml.safe_load(manifest.read_text(encoding="utf-8"))["clips"]
        print(f"{manifest}: {sum(c['kept'] for c in clips)} of {len(clips)} clips that say the word kept")
        return 0
    if step == "simulate":
        simulate(cfg, paths)
        return 0
    return cut_split(cfg, paths)


if __name__ == "__main__":
    raise SystemExit(main())
