"""Split command/v<n> (KEHOACH 1.3, 3.12): screened public speech by speaker, the unseen command out of learning.

Each corpus prefix of configs/models/command_ctc.yaml gives its speakers to val and test by share, the rest to train
(down to its hours cap); a corpus without speaker ids goes to train whole; a clip saying an unseen command leaves
train and val. simulate runs each file through the board simulation with pitch and no clean samples, resumably.
Run: python -m srpipe.tasks.command.ctc.data [split|simulate]"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import yaml

from srpipe.core import corpus, screen, splits
from srpipe.core.config import apply_overrides, data_paths, load_device, load_yaml
from srpipe.scenes import device
from srpipe.tasks import command
from srpipe.tasks.command import ctc

PUBLIC = "public"
LEARNING = ("train", "val")
HOURS_STREAM = 4


def unseen_phrases(unseen: list[str], commands: dict) -> dict[str, list[str]]:
    """The syllables of each unseen command, by its text."""
    texts = {c["id"]: c["text"] for c in commands["commands"]}
    if missing := [i for i in unseen if i not in texts]:
        raise ValueError(f"unseen commands {missing} are not in {command.COMMANDS.name}")
    return {texts[i]: corpus.words(texts[i]) for i in unseen}


def roles_of(clips: list[corpus.Clip], spec: dict) -> dict[str, str]:
    """The role of every clip by its corpus prefix and speaker; a prefix without shares is all train."""
    roles: dict[str, str] = {}
    for prefix, shares in spec["corpora"].items():
        mine = [c for c in clips if c.item.startswith(prefix)]
        speakers = {c.speaker for c in mine if c.speaker}
        if shares and not all(c.speaker for c in mine):
            raise ValueError(f"{prefix}: shares need a speaker on every clip")
        by_speaker = splits.speaker_roles(speakers, shares, "train", spec["seed"]) if shares else {}
        roles |= {c.item: by_speaker.get(c.speaker or "", "train") for c in mine}
    return roles


def build(spec: dict, clips: list[corpus.Clip], unseen: dict[str, list[str]]) -> tuple[dict, dict[str, int]]:
    """Every split file as rows, train one file per corpus so none grows past what a reader opens at once, and how
    many clips of each unseen command left learning."""
    roles = roles_of(clips, spec)
    dropped = dict.fromkeys(unseen, 0)
    files: dict[str, list[splits.Row]] = {}
    for c in clips:
        if c.item not in roles:
            continue
        role = roles[c.item]
        said = corpus.words(c.text or "")
        heard = [text for text, phrase in unseen.items() if corpus.says(said, phrase)]
        if role in LEARNING and heard:
            for text in heard:
                dropped[text] += 1
            continue
        name = f"train_{c.item.split('/')[1]}.txt" if role == "train" else f"{role}.txt"
        files.setdefault(name, []).extend(splits.clip_rows([c], PUBLIC))
    return dict(sorted(files.items())), dropped


def capped(files: dict, seconds: dict[str, float], spec: dict) -> dict:
    """The files with each train file of a corpus under an hours cap drawn down to it, rows in a seeded order until
    their length passes the cap, then back in listing order."""
    out = dict(files)
    for prefix, hours in spec["hours"].items():
        name = f"train_{prefix.strip('/').split('/')[1]}.txt"
        rows = files[name]
        order = np.random.default_rng([spec["seed"], HOURS_STREAM]).permutation(len(rows))
        total, taken = 0.0, []
        for k in order:
            if total >= hours * splits.SECONDS_PER_HOUR:
                break
            taken.append(k)
            total += seconds[rows[k].item]
        out[name] = [rows[k] for k in sorted(taken)]
    return out


def notes(spec: dict, files: dict, dropped: dict[str, int], seconds: dict[str, float]) -> str:
    """SPLIT.md ahead of the checksums: rules, seed, command and each file's size."""
    speakers = {name: len({r.spk for r in rows} - {splits.ABSENT}) for name, rows in files.items()}
    table = "\n".join(
        f"| `{name}` | {len(rows)} | {splits.hours(rows, seconds):.2f} | {speakers[name]} |"
        for name, rows in files.items()
    )
    shares = "\n".join(f"  - `{prefix}`: {shares or 'chỉ train'}" for prefix, shares in spec["corpora"].items())
    gone = ", ".join(f'"{text}" {count} mẩu' for text, count in dropped.items())
    return f"""# command/{spec["version"]}

Dựng bằng `python -m srpipe.tasks.command.ctc.data` (`make splits`), seed {spec["seed"]}, cấu hình mục `split` của
`ml/configs/models/command_ctc.yaml` và `unseen` của `ml/configs/models/command.yaml`. Luật ở KẾ HOẠCH §1.3:

- Chỉ mẩu qua sàng lọc (`interim/screen/rejects.tsv`).
- Theo người nói: mỗi kho trao cho `val` và `test` phần người nói của nó, còn lại vào `train`; kho không có mã người
  nói chỉ vào `train`.
{shares}
- Lệnh chưa học (E11-T13) không có trong `train` và `val`: bỏ {gone} có lời chứa lệnh ấy. Ở `test` thì giữ.
- Kho có trần giờ chỉ giữ phần rút theo seed tới trần: {spec["hours"] or "không kho nào"}.
- `train` chia một file mỗi kho; vai của file là phần tên trước dấu `_` đầu tiên.

| File | Mẩu | Giờ | Người nói |
|---|---|---|---|
{table}
"""


def built_as(out: Path, device_cfg: dict, split_file: Path) -> bool:
    """Whether out holds a finished simulation of split_file with this config."""
    if not (out / "manifest.yaml").exists():
        return False
    body = yaml.safe_load((out / "manifest.yaml").read_text(encoding="utf-8"))
    return body["config"] == device_cfg and body["split"]["sha256"] == splits.sha256_of(split_file)


def simulate(cfg: dict, paths: dict) -> None:
    """Every file of the split into processed/command/<version>/<file>, smallest first, with pitch and without the
    clean samples; a file stopped part way goes on from its finished shards."""
    spec, version = cfg["simulate"], cfg["split"]["version"]
    device_cfg = load_device(cfg["features"])
    folder = paths["splits"] / "command" / version
    for split_file in sorted(folder.glob("*.txt"), key=lambda f: f.stat().st_size):
        out = paths["processed"] / "command" / version / split_file.stem
        if built_as(out, device_cfg, split_file):
            print(f"{out}: already built", flush=True)
            continue
        raw, interim = paths["raw"], paths["interim"]
        print(device.build(device_cfg, split_file, raw, interim, out, spec["workers"], pitch=True, keep_pcm=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", nargs="?", default="split", choices=["split", "simulate"])
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    args = parser.parse_args(argv)
    cfg, paths = apply_overrides(load_yaml(ctc.CONFIG), args.overrides), data_paths()
    if args.step == "simulate":
        simulate(cfg, paths)
        return 0
    spec, screening = cfg["split"], load_yaml(screen.CONFIG)
    listed = json.loads(command.COMMANDS.read_text(encoding="utf-8"))
    unseen = unseen_phrases(load_yaml(command.CONFIG)["unseen"], listed)
    seconds = screen.lengths(screening, paths, "speech")
    files, dropped = build(spec, screen.kept_clips(screening, paths, "speech"), unseen)
    files = capped(files, seconds, spec)
    out = paths["splits"] / "command" / spec["version"]
    splits.write_version(out, files, notes(spec, files, dropped, seconds))
    problems = splits.check_version(out)
    print("\n".join(f"{name}: {len(rows)} rows, {splits.hours(rows, seconds):.2f} h" for name, rows in files.items()))
    print(f"left learning: {dropped}")
    print("\n".join(problems) or f"{out}: every rule of KEHOACH 1.3 holds")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
