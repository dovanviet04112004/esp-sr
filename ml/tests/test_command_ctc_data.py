"""Command split: an unseen command leaves learning but stays in test, speakers keep one role, a corpus without
speaker ids trains whole, and train is one file per corpus; features simulated otherwise than the config asks are
named before any training reads them, and a board cut's units leave out the runs its config names noise."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from srpipe.core import corpus, splits
from srpipe.core.config import load_device
from srpipe.scenes import device
from srpipe.tasks.command.ctc import data

COMMANDS = {"commands": [{"id": "bat_den", "text": "bật đèn"}, {"id": "chup_anh", "text": "chụp ảnh"}]}


def spec(**corpora: dict) -> dict:
    return {"seed": 3, "corpora": corpora}


def test_an_unseen_command_leaves_learning_and_stays_in_test() -> None:
    clips = [
        corpus.Clip(f"speech/cv/{s}_{n}.mp3", s, "anh chụp ảnh đi") for s in ("a", "b", "c", "d") for n in range(2)
    ]
    clips += [corpus.Clip(f"speech/bud500/data/x.parquet#{n}", None, "bật đèn lên") for n in range(3)]
    clips += [corpus.Clip("speech/bud500/data/x.parquet#3", None, "Chụp Ảnh!")]
    layout = spec(**{"speech/cv/": {"test": 0.5}, "speech/bud500/": {}})
    files, dropped = data.build(layout, clips, data.unseen_phrases(["chup_anh"], COMMANDS))
    assert sorted(files) == ["test.txt", "train_bud500.txt"]
    assert [r.item for r in files["train_bud500.txt"]] == [f"speech/bud500/data/x.parquet#{n}" for n in range(3)]
    assert len(files["test.txt"]) == 4
    assert len({r.spk for r in files["test.txt"]}) == 2
    assert dropped == {"chụp ảnh": 5}


def test_shares_need_speakers_and_unseen_commands_must_exist() -> None:
    clips = [corpus.Clip("speech/bud500/data/x.parquet#0", None, "bật đèn")]
    with pytest.raises(ValueError, match="need a speaker"):
        data.build(spec(**{"speech/bud500/": {"val": 0.1}}), clips, {})
    with pytest.raises(ValueError, match="not in"):
        data.unseen_phrases(["chup_hinh"], COMMANDS)


def test_an_hours_cap_draws_a_corpus_down_and_leaves_the_others_whole() -> None:
    rows = {
        "train_bud500.txt": [
            splits.Row(f"speech/bud500/{k}.wav", splits.ABSENT, splits.ABSENT, "public") for k in range(10)
        ],
        "train_vivos.txt": [splits.Row("speech/vivos/train/a.wav", "S1", splits.ABSENT, "public")],
    }
    seconds = {r.item: 1800.0 for rs in rows.values() for r in rs}
    spec = {"seed": 1, "hours": {"speech/bud500/": 2.0}}
    capped = data.capped(rows, seconds, spec)
    kept = capped["train_bud500.txt"]
    assert len(kept) == 4 and kept == sorted(kept, key=lambda r: int(r.item.split("/")[-1].split(".")[0]))
    assert capped["train_vivos.txt"] == rows["train_vivos.txt"] and data.capped(rows, seconds, spec) == capped


def built(
    paths: dict,
    name: str,
    config: dict,
    speeds: list[float] | None = None,
    dtype: str = "float32",
    cut: str = "pads",
    marks: dict | None = None,
) -> None:
    """A finished build of split file name under paths, as device.build's manifest records it; marks in place of
    the cut's own, as a build of another vad or listen.yaml recorded them."""
    split = paths["splits"] / "command" / "v9" / f"{name}.txt"
    split.parent.mkdir(parents=True, exist_ok=True)
    split.write_text(f"speech/x/{name}.wav\tA\t-\tpublic\n", encoding="utf-8")
    out = paths["processed"] / "command" / "v9" / name
    out.mkdir(parents=True)
    body = {"config": config, "split": {"file": split.name, "sha256": splits.sha256_of(split)}}
    body |= ({"speeds": speeds} if speeds else {}) | ({"dtype": dtype} if dtype != "float32" else {})
    body |= device.cut_marks(cut) if marks is None else marks
    (out / "manifest.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


def test_features_simulated_otherwise_than_the_config_asks_are_named(tmp_path: Path) -> None:
    simulated = {"speeds": [0.9, 1.0, 1.1], "train_dtype": "float16"}
    cfg = {"features": "scenes/device.yaml", "split": {"version": "v9"}, "simulate": simulated}
    device_cfg = load_device(cfg["features"])
    paths = {"splits": tmp_path / "splits", "processed": tmp_path / "processed"}
    built(paths, "train_x", device_cfg, [0.9, 1.0, 1.1], "float16")
    built(paths, "val", device_cfg)
    assert data.unbuilt(cfg, paths) == []
    older = {k: v for k, v in device_cfg.items() if k != "microphone"} | {"microphone": {"pcm_shift": 13}}
    built(paths, "test", older)
    built(paths, "train_w", device_cfg, [0.9, 1.0, 1.1])
    built(paths, "train_y", device_cfg, dtype="float16")
    (paths["splits"] / "command" / "v9" / "train_z.txt").write_text("", encoding="utf-8")
    stale = [p.name for p in data.unbuilt(cfg, paths)]
    assert stale == ["test", "train_w", "train_y", "train_z"]
    assert data.options_of(cfg, paths["splits"] / "val.txt") == {"speeds": (), "dtype": "float32", "cut": "pads"}


def test_every_file_is_cut_as_simulate_cut_asks_and_another_cut_is_stale(tmp_path: Path) -> None:
    cfg = {"features": "scenes/device.yaml", "split": {"version": "v9"}, "simulate": {"cut": "listen"}}
    device_cfg = load_device(cfg["features"])
    paths = {"splits": tmp_path / "splits", "processed": tmp_path / "processed"}
    built(paths, "train_x", device_cfg, cut="listen")
    built(paths, "train_y", device_cfg, cut="listen", marks={"cut": "listen"})
    built(paths, "train_z", device_cfg, cut="listen", marks=device.cut_marks("listen") | {"listen_hash": "0x0"})
    built(paths, "val", device_cfg, cut="listen")
    built(paths, "test", device_cfg)
    assert [p.name for p in data.unbuilt(cfg, paths)] == ["test", "train_y", "train_z"]
    for name in ("train_x.txt", "val.txt"):
        assert data.options_of(cfg, paths["splits"] / name)["cut"] == "listen"


def test_board_units_leave_out_the_runs_named_noise_and_refuse_a_name_the_cut_lacks(tmp_path: Path) -> None:
    rows = [{"item": f"{data.BOARD}/s1#{k}", "text": "bật đèn"} for k in (0, 2, 3)]
    (tmp_path / "shard_00000.items.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    every = data.board_units(tmp_path, "north", [])
    kept = data.board_units(tmp_path, "north", ["s1#2"])
    assert sorted(every) == [f"{data.BOARD}/s1#{k}" for k in (0, 2, 3)]
    assert (
        sorted(kept) == [f"{data.BOARD}/s1#0", f"{data.BOARD}/s1#3"]
        and kept[f"{data.BOARD}/s1#0"] == every[f"{data.BOARD}/s1#0"]
    )
    with pytest.raises(ValueError, match="s1#1"):
        data.board_units(tmp_path, "north", ["s1#1"])


def test_a_board_cut_of_another_listen_yaml_is_stale(tmp_path: Path) -> None:
    device_cfg = load_device("scenes/device.yaml")
    rows, board = [{"session": "s1"}], {"min_s": 0.8, "max_s": 2.4, "repeat": 75}
    body = {"config": device_cfg, "sessions": ["s1"], "board": data.board_cut(board)}
    for listen_hash, fresh in ((device.cut_marks("listen")["listen_hash"], True), ("0x0", False), (None, False)):
        written = body | ({"listen_hash": listen_hash} if listen_hash else {})
        (tmp_path / "manifest.yaml").write_text(yaml.safe_dump(written), encoding="utf-8")
        assert data.board_built_as(tmp_path, device_cfg, rows, board) is fresh
