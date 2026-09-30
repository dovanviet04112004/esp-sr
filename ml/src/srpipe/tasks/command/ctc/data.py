"""Split command/v<n> (KEHOACH 1.3, 3.12): screened public speech by speaker, the unseen command out of learning.

Each corpus prefix of configs/models/command_ctc.yaml gives its speakers to val and test by share, the rest to train;
a corpus without speaker ids goes to train whole. A clip whose text says an unseen command of
configs/models/command.yaml leaves train and val. Run: python -m srpipe.tasks.command.ctc.data
"""

from __future__ import annotations

import argparse
import json

from srpipe.core import corpus, screen, splits
from srpipe.core.config import data_paths, load_yaml
from srpipe.tasks import command
from srpipe.tasks.command import ctc

PUBLIC = "public"
LEARNING = ("train", "val")


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
- `train` chia một file mỗi kho; vai của file là phần tên trước dấu `_` đầu tiên.

| File | Mẩu | Giờ | Người nói |
|---|---|---|---|
{table}
"""


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    spec, paths, screening = load_yaml(ctc.CONFIG)["split"], data_paths(), load_yaml(screen.CONFIG)
    listed = json.loads(command.COMMANDS.read_text(encoding="utf-8"))
    unseen = unseen_phrases(load_yaml(command.CONFIG)["unseen"], listed)
    files, dropped = build(spec, screen.kept_clips(screening, paths, "speech"), unseen)
    seconds = screen.lengths(screening, paths, "speech")
    out = paths["splits"] / "command" / spec["version"]
    splits.write_version(out, files, notes(spec, files, dropped, seconds))
    problems = splits.check_version(out)
    print("\n".join(f"{name}: {len(rows)} rows, {splits.hours(rows, seconds):.2f} h" for name, rows in files.items()))
    print(f"left learning: {dropped}")
    print("\n".join(problems) or f"{out}: every rule of KEHOACH 1.3 holds")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
