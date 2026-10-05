"""Split command/v<n> (KEHOACH 1.3, 3.12): screened public speech by speaker, the unseen command out of learning.

Each corpus prefix of configs/models/command_ctc.yaml gives its speakers to val and test by share, the rest to train
(down to its hours cap); a corpus without speaker ids goes to train whole; a clip saying an unseen command leaves train
and val. simulate runs each file through the board simulation with pitch, resumably, and cuts the board sessions given
to train as Gate 3 does. Run: python -m srpipe.tasks.command.ctc.data [split|simulate]"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import signal
from pathlib import Path

import numpy as np
import yaml

from srpipe.core import corpus, screen, splits
from srpipe.core.config import apply_overrides, data_paths, device_of, load_yaml
from srpipe.generated import grid
from srpipe.scenes import device
from srpipe.tasks import command
from srpipe.tasks.command import ctc
from srpipe.tasks.wake.data import sentence_units

PUBLIC = "public"
LEARNING = ("train", "val")
HOURS_STREAM = 4
BOARD = "board"  # under processed/command/<version>, one shard
BOARD_SHARD = "shard_00000"
HOPS_PER_S = grid.SAMPLE_RATE_HZ / grid.HOP_SAMPLES


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


def notes(spec: dict, files: dict, dropped: dict[str, int], seconds: dict[str, float], sessions: list[str]) -> str:
    """SPLIT.md ahead of the checksums: rules, seed, command, the board sessions given to train and each file's size."""
    speakers = {name: len({r.spk for r in rows} - {splits.ABSENT}) for name, rows in files.items()}
    table = "\n".join(
        f"| `{name}` | {len(rows)} | {splits.hours(rows, seconds):.2f} | {speakers[name]} |"
        for name, rows in files.items()
    )
    shares = "\n".join(f"  - `{prefix}`: {shares or 'chỉ train'}" for prefix, shares in spec["corpora"].items())
    gone = ", ".join(f'"{text}" {count} mẩu' for text, count in dropped.items())
    on_board = (
        f"- Phiên thu qua board vào `train` (KẾ HOẠCH §1.3, `eval.board.train` của `command.yaml`): "
        f"{', '.join(sessions)}; cắt như Cửa 3, giữ câu từ {board['min_s']} đến {board['max_s']} s, shard lặp "
        f"{board['repeat']} lần trong thứ tự shard.\n"
        if (board := spec.get("board"))
        else ""
    )
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
{on_board}
| File | Mẩu | Giờ | Người nói |
|---|---|---|---|
{table}
"""


def options_of(cfg: dict, split_file: Path) -> dict:
    """How a file of the split is simulated: every file cut as simulate.cut asks (KEHOACH 1.2), train at
    simulate.speeds and stored as simulate.train_dtype, val and test at speed one and in float32."""
    spec = cfg["simulate"]
    cut = spec.get("cut", "pads")
    if splits.role_of(split_file.name) != "train":
        return {"speeds": (), "dtype": "float32", "cut": cut}
    return {"speeds": tuple(spec.get("speeds", ())), "dtype": spec.get("train_dtype", "float32"), "cut": cut}


def built_as(out: Path, device_cfg: dict, split_file: Path, options: dict) -> bool:
    """Whether out holds a finished simulation of split_file with this config and these options of options_of."""
    if not (out / "manifest.yaml").exists():
        return False
    body = yaml.safe_load((out / "manifest.yaml").read_text(encoding="utf-8"))
    same_split = body["split"]["sha256"] == splits.sha256_of(split_file)
    same_options = (
        body.get("speeds", []) == list(options["speeds"])
        and body.get("dtype", "float32") == options["dtype"]
        and body.get("cut", "pads") == options["cut"]
        and body.get("cut_vad") == (device.LISTEN_CUT_VAD if options["cut"] == "listen" else None)
    )
    return body["config"] == device_cfg and same_split and same_options


def board_rows(paths: dict) -> list[dict]:
    """The manifest rows of the board sessions Gate 3's spec gives to train (KEHOACH 1.3)."""
    spec = load_yaml(command.CONFIG)["eval"]["board"]
    wanted = spec.get("train", [])
    with (paths["manifests"] / spec["manifest"]).open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["session"] in wanted]
    if missing := sorted(set(wanted) - {r["session"] for r in rows}):
        raise ValueError(f"{', '.join(missing)}: not in {spec['manifest']}")
    if shifts := sorted({r["pcm_shift"] for r in rows} - {str(spec["pcm_shift"])}):
        raise ValueError(f"train sessions at pcm_shift {shifts}; the product keeps {spec['pcm_shift']}")
    return rows


def board_cut(board: dict) -> dict:
    """What of split.board shapes the cut; repeat only weighs it in training."""
    return {"min_s": board["min_s"], "max_s": board["max_s"]}


def board_built_as(out: Path, device_cfg: dict, rows: list[dict], board: dict) -> bool:
    """Whether out holds the cut of these sessions with this device config and this split.board."""
    if not (out / "manifest.yaml").exists():
        return False
    body = yaml.safe_load((out / "manifest.yaml").read_text(encoding="utf-8"))
    same_sessions = body["sessions"] == [r["session"] for r in rows]
    return body["config"] == device_cfg and same_sessions and body["board"] == board_cut(board)


def cut_board(cfg: dict, paths: dict, out: Path) -> str:
    """The board sessions given to train as one shard in train's dtype: each utterance whose vad run lasts
    split.board's min_s to max_s, as its Gate 3 command window of log-mel and pitch, its session's prompt its text."""
    # Gate 3 reads a session beside the nets that score it, which need torch; only the board cut pays for that.
    from srpipe.tasks.command import eval as gate

    board, rows = cfg["split"]["board"], board_rows(paths)
    device_cfg = device_of(cfg)
    n_bands = device_cfg["features"]["n_bands"]
    shortest, longest = (round(board[k] * HOPS_PER_S) for k in ("min_s", "max_s"))
    items, mels, pitches, offset, dropped = [], [], [], 0, 0
    for r, _clean, vad, features, pitch in gate.heard_rows(cfg, rows, paths):
        spans = device.utterances(vad)
        windows = gate.ctc_windows(features, pitch, spans) if spans else []
        for k, ((first, last), (start, _), x) in enumerate(zip(spans, device.command_cut(spans), windows, strict=True)):
            if not shortest <= last + 1 - first <= longest:
                dropped += 1
                continue
            items.append(
                {
                    "item": f"{BOARD}/{r['session']}#{k}",
                    "text": r["prompt"],
                    "frame_offset": offset,
                    "n_frames": len(x),
                    "speech_frames": [first - start, last + 1 - start],
                }
            )
            mels.append(x[:, :n_bands])
            pitches.append(x[:, n_bands:])
            offset += len(x)
    dtype = cfg["simulate"].get("train_dtype", "float32")
    out.mkdir(parents=True, exist_ok=True)
    np.save(out / f"{BOARD_SHARD}.features.npy", np.concatenate(mels).astype(dtype))
    np.save(out / f"{BOARD_SHARD}.pitch.npy", np.concatenate(pitches).astype(dtype))
    lines = "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items)
    (out / f"{BOARD_SHARD}.items.jsonl").write_text(lines, encoding="utf-8")
    names = [f"{BOARD_SHARD}{s}" for s in (".features.npy", ".pitch.npy", ".items.jsonl")]
    head = {
        "pitch": True,
        "config": device_cfg,
        "sessions": [r["session"] for r in rows],
        "board": board_cut(board),
        "dtype": dtype,
        "sha256": {n: hashlib.sha256((out / n).read_bytes()).hexdigest() for n in names},
    }
    (out / "manifest.yaml").write_text(yaml.safe_dump(head, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return (
        f"{out}: {len(items)} utterances of {len(rows)} sessions, {dropped} outside {board['min_s']}-{board['max_s']} s"
    )


def board_units(folder: Path, dialect: str, noise: list[str]) -> dict[str, list[int]]:
    """lang_vi unit ids of every item of a board cut, its session's prompt read in dialect, but the <session>#<run>
    utterances noise names; a name the cut lacks is refused."""
    items = [
        json.loads(line) for f in sorted(folder.glob("*.items.jsonl")) for line in f.read_text("utf-8").splitlines()
    ]
    runs = {i["item"].removeprefix(f"{BOARD}/") for i in items}
    if absent := sorted(set(noise) - runs):
        raise ValueError(f"{', '.join(absent)}: not utterances of the board cut {folder}")
    said = [i for i in items if i["item"].removeprefix(f"{BOARD}/") not in noise]
    return sentence_units([corpus.Clip(i["item"], None, i["text"]) for i in said], {i["item"] for i in said}, dialect)


def unbuilt(cfg: dict, paths: dict) -> list[Path]:
    """Every file's processed folder that is missing, unfinished, or simulated with another device config or other
    options than cfg now asks: features of a board the simulation no longer is; with split.board, its cut too."""
    device_cfg = device_of(cfg)
    folder = paths["splits"] / "command" / cfg["split"]["version"]
    outs = {f: paths["processed"] / "command" / cfg["split"]["version"] / f.stem for f in sorted(folder.glob("*.txt"))}
    stale = [out for f, out in outs.items() if not built_as(out, device_cfg, f, options_of(cfg, f))]
    if board := cfg["split"].get("board"):
        out = paths["processed"] / "command" / cfg["split"]["version"] / BOARD
        stale += [] if board_built_as(out, device_cfg, board_rows(paths), board) else [out]
    return stale


def simulate(cfg: dict, paths: dict) -> None:
    """Every file of the split into processed/command/<version>/<file>, smallest first, with pitch and without the
    clean samples, train's items each spoken at a speed of simulate.speeds and stored as simulate.train_dtype; a file
    stopped part way goes on from its finished shards."""
    spec, version = cfg["simulate"], cfg["split"]["version"]
    device_cfg = device_of(cfg)
    folder = paths["splits"] / "command" / version
    for split_file in sorted(folder.glob("*.txt"), key=lambda f: f.stat().st_size):
        out = paths["processed"] / "command" / version / split_file.stem
        options = options_of(cfg, split_file)
        if built_as(out, device_cfg, split_file, options):
            print(f"{out}: already built", flush=True)
            continue
        raw, interim, workers = paths["raw"], paths["interim"], spec["workers"]
        print(device.build(device_cfg, split_file, raw, interim, out, workers, pitch=True, keep_pcm=False, **options))
    if board := cfg["split"].get("board"):
        out = paths["processed"] / "command" / version / BOARD
        built = board_built_as(out, device_cfg, board_rows(paths), board)
        print(f"{out}: already built" if built else cut_board(cfg, paths, out), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", nargs="?", default="split", choices=["split", "simulate"])
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    args = parser.parse_args(argv)
    cfg, paths = apply_overrides(load_yaml(ctc.CONFIG), args.overrides), data_paths()
    if args.step == "simulate":
        try:
            simulate(cfg, paths)
        except KeyboardInterrupt:
            return 128 + signal.SIGINT
        return 0
    spec, screening = cfg["split"], load_yaml(screen.CONFIG)
    listed = json.loads(command.COMMANDS.read_text(encoding="utf-8"))
    unseen = unseen_phrases(load_yaml(command.CONFIG)["unseen"], listed)
    seconds = screen.lengths(screening, paths, "speech")
    files, dropped = build(spec, screen.kept_clips(screening, paths, "speech"), unseen)
    files = capped(files, seconds, spec)
    out = paths["splits"] / "command" / spec["version"]
    sessions = [r["session"] for r in board_rows(paths)] if spec.get("board") else []
    splits.write_version(out, files, notes(spec, files, dropped, seconds, sessions))
    problems = splits.check_version(out)
    print("\n".join(f"{name}: {len(rows)} rows, {splits.hours(rows, seconds):.2f} h" for name, rows in files.items()))
    print(f"left learning: {dropped}")
    print("\n".join(problems) or f"{out}: every rule of KEHOACH 1.3 holds")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
