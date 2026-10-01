"""Split command_kws/v<n> and its features (KEHOACH 1.3, 3.12, ADR-0012): the learned commands, other and silence.

split writes one file a role, class and source: TTS clips of E11-T7 by the role of their voice, real clips of the
extract and of kws_vi_command into train, ordinary speech of the command split for other, stretches of noise for
silence; simulate runs each file through the board simulation with pitch, padded so a window ends where vad does.
Run: python -m srpipe.tasks.command.kws.data {split,simulate}
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf
import yaml

from srpipe.core import corpus, extract, screen, splits
from srpipe.core.config import CONFIGS, apply_overrides, data_paths, load_yaml
from srpipe.scenes import device
from srpipe.tasks import command
from srpipe.tasks.command import kws
from srpipe.tasks.command.synth import SETS

PUBLIC, SYNTH = "public", "synth"
SOURCES = ("tts", "real", "speech", "noise")
ROLES = ("train", "val")
SPEECH_STREAM, SILENCE_STREAM, SPEECH_COMMANDS_STREAM = 1, 2, 3
Rows = dict[str, list[splits.Row]]


def file_name(role: str, cls: str, source: str) -> str:
    return f"{role}_{cls}_{source}.txt"


def class_of(name: str, names: list[str]) -> str:
    """The class a split file holds, from its name role_class_source.txt."""
    role, _, rest = Path(name).stem.partition("_")
    cls, _, source = rest.rpartition("_")
    if role not in splits.ROLES or cls not in names or source not in SOURCES:
        raise ValueError(f"{name} is no <role>_<class>_<source>.txt of classes {names}")
    return cls


def source_of(name: str) -> str:
    return Path(name).stem.rpartition("_")[2]


def voice_of(clip: dict) -> str:
    """The person a TTS clip sounds like: its VieNeu preset, the VIVOS speaker it was cloned from, or ABSENT for a
    clone of a corpus without speaker ids."""
    if clip["id"].startswith("preset_"):
        return "vieneu_" + "_".join(clip["id"].split("_")[:2])
    return clip["speaker"] if clip["speaker"].startswith("VIVOS") else splits.ABSENT


def tts_rows(clips: list[dict], interim: Path, roles: dict[str, str], command_of: dict[str, str]) -> dict:
    """Kept clips of a synth manifest by (role, class): a positive's class is the command it says, a negative's is
    other; the role is its voice's, train for a voice that names nobody."""
    rows: dict[tuple[str, str], list[splits.Row]] = defaultdict(list)
    for c in clips:
        if c.get("kept"):
            voice = voice_of(c)
            cls = command_of[c["say"]] if c.get("say") else kws.OTHER
            item = str(Path(c["wav"]).relative_to(interim))
            rows[(roles.get(voice, "train"), cls)].append(splits.Row(item, voice, splits.ABSENT, SYNTH))
    return rows


def hf_rows(raw: Path, name: str, command_of: dict[str, str]) -> tuple[Rows, dict[str, float]]:
    """The extract's clips of real voices by the command they say, however spelled, and their seconds as written; any
    other phrase, a source tagged synthetic and an empty file are left out."""
    folder = extract.clips_folder(raw, name)
    by_sound = {tuple(corpus.sounds(text)): cid for text, cid in command_of.items()}
    rows: Rows = defaultdict(list)
    seconds = {}
    for c in extract.read_index(folder):
        cid = by_sound.get(tuple(corpus.sounds(c["phrase"])))
        if c["origin"] == PUBLIC and cid is not None:
            info = sf.info(str(folder / c["file"]))
            if info.frames:
                item = str((folder / c["file"]).relative_to(raw))
                rows[cid].append(splits.Row(item, splits.ABSENT, splits.ABSENT, PUBLIC))
                seconds[item] = info.frames / info.samplerate
    return rows, seconds


def wav_rows(raw: Path, folder: Path) -> tuple[list[splits.Row], dict[str, float]]:
    """Every wav of folder under raw/ as a row of no speaker, with its seconds."""
    rows, seconds = [], {}
    for f in sorted(folder.glob("*.wav")):
        item = str(f.relative_to(raw))
        rows.append(splits.Row(item, splits.ABSENT, splits.ABSENT, PUBLIC))
        info = sf.info(str(f))
        seconds[item] = info.frames / info.samplerate
    return rows, seconds


def kws_vi_rows(raw: Path, spec: dict, ids: set[str]) -> tuple[Rows, dict[str, float]]:
    """kws_vi_command's clips by class: a folder of a learned command, its phrases outside the set as other, its
    room noise as silence."""
    rows: Rows = defaultdict(list)
    seconds: dict[str, float] = {}
    for folder in spec["folders"]:
        for sub in sorted(p for p in (raw / folder).iterdir() if p.is_dir()):
            cls = sub.name if sub.name in ids else kws.OTHER if sub.name in spec["other"] else None
            if cls is not None:
                found, lengths = wav_rows(raw, sub)
                rows[cls] += found
                seconds |= lengths
    found, lengths = wav_rows(raw, raw / spec["silence"])
    rows[kws.SILENCE] += found
    return rows, seconds | lengths


def draw_hours(rows: list[splits.Row], seconds: dict[str, float], hours: float, seed: int) -> list[splits.Row]:
    """Rows in a seeded random order until their length passes hours, then back in listing order."""
    order = np.random.default_rng([seed, SPEECH_STREAM]).permutation(len(rows))
    taken, total = [], 0.0
    for k in order:
        if total >= hours * splits.SECONDS_PER_HOUR:
            break
        taken.append(k)
        total += seconds[rows[k].item]
    return [rows[k] for k in sorted(taken)]


def speech_rows(spec: dict, paths: dict, texts: list[str], excluded: set[str]) -> tuple[Rows, dict[str, float]]:
    """Ordinary speech for other: the command split's train files for train and its val.txt for val, no sentence
    that says a learned command and no speaker in excluded, drawn to other_hours."""
    screening = load_yaml(screen.CONFIG)
    text = {c.item: c.text for c in screen.kept_clips(screening, paths, "speech")}
    seconds = screen.lengths(screening, paths, "speech")
    phrases = [corpus.sounds(t) for t in texts]

    def usable(row: splits.Row) -> bool:
        said = corpus.sounds(text.get(row.item) or "")
        return row.item in text and row.spk not in excluded and not any(corpus.says(said, p) for p in phrases)

    folder = paths["splits"] / spec["command_split"]
    pools = {
        "train": [r for f in sorted(folder.glob("train_*.txt")) for r in splits.read_split(f) if usable(r)],
        "val": [r for r in splits.read_split(folder / "val.txt") if usable(r)],
    }
    drawn = {role: draw_hours(rows, seconds, spec["other_hours"][role], spec["seed"]) for role, rows in pools.items()}
    return drawn, seconds


def silence_rows(spec: dict, raw: Path, rejected: set[str], seed: int) -> tuple[Rows, dict[str, float]]:
    """Seeded stretches of the silence pools' files, count a role, each file in one role only."""
    rng = np.random.default_rng([seed, SILENCE_STREAM])
    pools = []
    for pool in spec["pools"]:
        files = sorted(str(f.relative_to(raw)) for f in (raw / pool["dir"]).glob(pool["glob"]))
        files = [f for f in files if f not in rejected]
        role_of = splits.speaker_roles(set(files), {"val": spec["val_files"]}, "train", seed)
        pools.append({role: [f for f in files if role_of[f] == role] for role in ROLES})
    weights = np.array([p["weight"] for p in spec["pools"]], dtype=np.float64)
    rows: Rows = {role: [] for role in ROLES}
    seconds: dict[str, float] = {}
    length: dict[str, float] = {}
    for role in ROLES:
        have = np.array([len(p[role]) > 0 for p in pools])
        if not have.any():
            raise FileNotFoundError(f"no silence file holds role {role}")
        for _ in range(spec["count"][role]):
            p = pools[int(rng.choice(len(pools), p=weights * have / (weights * have).sum()))][role]
            name = p[int(rng.integers(len(p)))]
            if name not in length:
                info = sf.info(str(raw / name))
                length[name] = info.frames / info.samplerate
            span = min(float(rng.uniform(*spec["seconds"])), length[name])
            start = float(rng.uniform(0.0, length[name] - span))
            item = f"{name}@{start:.3f}-{start + span:.3f}"
            rows[role].append(splits.Row(item, splits.ABSENT, splits.ABSENT, PUBLIC))
            seconds[item] = span
    return rows, seconds


def speech_commands_files(raw: Path, spec: dict, seed: int) -> tuple[Rows, dict[str, float]]:
    """A Speech Commands pilot's files but silence's: each keyword, and the other words as other, drawn to their
    counts a role; val from the corpus's testing list, train from neither of its lists, so speakers stay apart as the
    corpus keeps them."""
    root = raw / spec["dir"]
    held = {n: set((root / f"{n}_list.txt").read_text(encoding="utf-8").split()) for n in ("testing", "validation")}
    found: dict[tuple[str, str], list[str]] = defaultdict(list)
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        cls = folder.name if folder.name in spec["keywords"] else kws.OTHER
        for f in sorted(folder.glob("*.wav")):
            name = f"{folder.name}/{f.name}"
            if name not in held["validation"]:
                found[("val" if name in held["testing"] else "train", cls)].append(name)
    rng = np.random.default_rng([seed, SPEECH_COMMANDS_STREAM])
    files: Rows = {}
    seconds: dict[str, float] = {}
    for (role, cls), names in sorted(found.items()):
        count = spec["other_clips" if cls == kws.OTHER else "clips"][role]
        drawn = sorted(rng.choice(names, size=min(count, len(names)), replace=False).tolist())
        speaker = [n.split("/")[1].split("_nohash_")[0] for n in drawn]
        rows = [splits.Row(f"{spec['dir']}/{n}", s, splits.ABSENT, PUBLIC) for n, s in zip(drawn, speaker, strict=True)]
        files[file_name(role, cls, "real")] = rows
        for r in rows:
            info = sf.info(str(raw / r.item))
            seconds[r.item] = info.frames / info.samplerate
    return files, seconds


def command_files(cfg: dict, command_cfg: dict, paths: dict) -> tuple[Rows, dict[str, float]]:
    """The command set's files but silence's: TTS clips, real clips, and ordinary speech for other."""
    spec = cfg["split"]
    names = kws.classes(cfg, command_cfg)
    # A learned command the net leaves out is other to it.
    command_of = {c["text"]: c["id"] if c["id"] in names else kws.OTHER for c in command.learned(command_cfg)}
    manifests = [
        yaml.safe_load((paths["interim"] / "command" / SETS[s] / "manifest.yaml").read_text(encoding="utf-8"))["clips"]
        for s in ("positives", "negatives")
        if spec["tts"]
    ]
    voices = {voice_of(c) for clips in manifests for c in clips if c.get("kept")} - {splits.ABSENT}
    roles = splits.speaker_roles(voices, {"val": spec["val_voices"]}, "train", spec["seed"])
    held = {v for v, r in roles.items() if r == "val"}
    files: Rows = {}
    seconds = {str(Path(c["wav"]).relative_to(paths["interim"])): c["seconds"] for m in manifests for c in m}
    for clips in manifests:
        for (role, cls), rows in tts_rows(clips, paths["interim"], roles, command_of).items():
            files.setdefault(file_name(role, cls, "tts"), []).extend(rows)
    real, lengths = hf_rows(paths["raw"], spec["hf_extract"], command_of) if spec["hf_extract"] else ({}, {})
    seconds |= lengths
    theirs, lengths = kws_vi_rows(paths["raw"], spec["kws_vi_command"], set(names[: names.index(kws.OTHER)]))
    seconds |= lengths
    held_folders = spec["kws_vi_command"]["val_folders"]
    for cls in names:
        rows = real.get(cls, []) + theirs.get(cls, [])
        val = [r for r in rows if any(r.item.startswith(f + "/") for f in held_folders)]
        if train := [r for r in rows if r not in val]:
            files[file_name("train", cls, "real")] = train
        if val:
            files[file_name("val", cls, "real")] = val
    speech, lengths = speech_rows(spec, paths, list(command_of), held)
    for role in ROLES:
        files[file_name(role, kws.OTHER, "speech")] = speech[role]
    return files, seconds | lengths


def build(cfg: dict, command_cfg: dict, paths: dict) -> tuple[Rows, dict[str, float]]:
    """Every split file of the version by name, and the seconds of every item in them."""
    spec = cfg["split"]
    if cfg["speech_commands"]:
        files, seconds = speech_commands_files(paths["raw"], cfg["speech_commands"], spec["seed"])
    else:
        files, seconds = command_files(cfg, command_cfg, paths)
    rejected = set(screen.rejected(paths["interim"]))
    noise, lengths = silence_rows(spec["silence"], paths["raw"], rejected, spec["seed"])
    for role in ROLES:
        files[file_name(role, kws.SILENCE, "noise")] = noise[role]
    return dict(sorted(files.items())), seconds | lengths


def notes(cfg: dict, command_cfg: dict, files: Rows, seconds: dict[str, float]) -> str:
    """SPLIT.md ahead of the checksums: rules, seed, and each file's class, rows and hours."""
    spec = cfg["split"]
    names = kws.classes(cfg, command_cfg)
    table = "\n".join(
        f"| `{name}` | `{class_of(name, names)}` | {len(rows)} | {splits.hours(rows, seconds):.2f} |"
        for name, rows in files.items()
    )
    if pilot := cfg["speech_commands"]:
        sources = f"""\
- `real`: mẩu của Speech Commands v0.02 ở `raw/{pilot["dir"]}`, mỗi từ khoá {pilot["clips"]} mẩu một vai, các từ còn
  lại vào `other`, {pilot["other_clips"]} mẩu một vai; `val` lấy từ `testing_list.txt`, `train` từ mẩu không nằm ở danh
  sách nào của bộ, nên người nói tách như bộ tách. Pilot kiểm đường kws trên kho nhiều người nói."""
    else:
        sources = f"""\
- `tts`: mẩu TTS có `kept` của `interim/command/synth_pos` (lệnh nó nói) và `synth_neg` (`other`: cụm gần âm, cụm mở
  đầu, nửa lệnh, cụm tay). {spec["val_voices"]:.0%} giọng có sẵn của VieNeu và {spec["val_voices"]:.0%} người nói VIVOS
  làm giọng mẫu vào `val` trọn vẹn cùng mọi giọng nhân bản từ họ; giọng nhân bản từ kho không mã người nói chỉ vào
  `train`.
- `real`: mẩu người thật của kho trích `{spec["hf_extract"]}` và của `kws_vi_command` (lệnh của bộ; "bật hết", "tắt
  hết" vào `other`; nhiễu phòng của họ vào `silence`). Không kho nào có mã người nói nên chỉ vào `train`.
- `speech`: lời nói thường cho `other`, từ các file `train_*` của `{spec["command_split"]}` cho `train` và `val.txt` cho
  `val`, không câu nào nói một lệnh đã học; người nói có giọng nhân bản ở `val` không vào `train`."""
    return f"""# command_kws/{spec["version"]}

Dựng bằng `python -m srpipe.tasks.command.kws.data split`, seed {spec["seed"]}, mục `split` của
`ml/configs/models/command_kws.yaml` (KẾ HOẠCH §1.3, §3.12). Mỗi file một vai, một lớp, một nguồn:
`<vai>_<lớp>_<nguồn>.txt`; lớp theo thứ tự đầu ra của mạng: {", ".join(f"`{n}`" for n in names)}.

{sources}
- `noise`: đoạn {spec["silence"]["seconds"][0]:g} tới {spec["silence"]["seconds"][1]:g} s của nhiễu MUSAN và DEMAND cho
  `silence`; mỗi file nhiễu chỉ ở một vai.
- Tập thử là phiên thu qua board (E11-T6), chưa có.

| File | Lớp | Mẩu | Giờ |
|---|---|---|---|
{table}
"""


def cut_split(cfg: dict, command_cfg: dict, paths: dict) -> int:
    files, seconds = build(cfg, command_cfg, paths)
    out = paths["splits"] / "command_kws" / cfg["split"]["version"]
    splits.write_version(out, files, notes(cfg, command_cfg, files, seconds))
    problems = splits.check_version(out)
    print("\n".join(f"{name}: {len(rows)} rows, {splits.hours(rows, seconds):.2f} h" for name, rows in files.items()))
    print("\n".join(problems) or f"{out}: every rule of KEHOACH 1.3 holds")
    return 1 if problems else 0


def built_as(out: Path, device_cfg: dict, split_file: Path, repeats: int, pads_s: list[float]) -> bool:
    """Whether out holds a finished simulation of split_file with this config, passes, pads and pitch."""
    if not (out / "manifest.yaml").exists():
        return False
    body = yaml.safe_load((out / "manifest.yaml").read_text(encoding="utf-8"))
    return (
        body["config"] == device_cfg
        and body["split"]["sha256"] == splits.sha256_of(split_file)
        and body.get("repeats", 1) == repeats
        and body.get("pads_s") == list(pads_s)
        and body.get("pitch", False)
    )


def simulate(cfg: dict, paths: dict) -> None:
    """Every file of the split through the board simulation, one utterance a session, with pitch and the window's
    pads, into processed/command_kws/<version>/<file>, smallest first; a file already built the same way is left as
    it is, or hard-linked from the version that built it."""
    spec, version = cfg["simulate"], cfg["split"]["version"]
    base = load_yaml(CONFIGS / cfg["features"])
    device_cfg = base | {"session": base["session"] | {"items": spec["session_items"]}}
    pads = list(spec["pads_s"])
    folder = paths["splits"] / "command_kws" / version
    for split_file in sorted(folder.glob("*.txt"), key=lambda f: f.stat().st_size):
        out = paths["processed"] / "command_kws" / version / split_file.stem
        repeats = spec["repeats"].get(source_of(split_file.name), 1)
        if built_as(out, device_cfg, split_file, repeats, pads):
            print(f"{out}: already built", flush=True)
            continue
        twins = [d for d in sorted(out.parent.parent.glob(f"*/{split_file.stem}")) if d != out]
        twin = next((d for d in twins if built_as(d, device_cfg, split_file, repeats, pads)), None)
        if twin is not None:
            print(f"{out}: linked from {device.link_build(twin, out)}", flush=True)
            continue
        raw, interim, workers = paths["raw"], paths["interim"], spec["workers"]
        print(device.build(device_cfg, split_file, raw, interim, out, workers, repeats, tuple(pads), pitch=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["split", "simulate"])
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    args = parser.parse_args(argv)
    cfg, paths = apply_overrides(load_yaml(kws.CONFIG), args.overrides), data_paths()
    if args.step == "simulate":
        simulate(cfg, paths)
        return 0
    return cut_split(cfg, load_yaml(command.CONFIG), paths)


if __name__ == "__main__":
    raise SystemExit(main())
