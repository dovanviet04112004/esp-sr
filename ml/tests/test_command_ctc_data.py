"""Command split: an unseen command leaves learning but stays in test, speakers keep one role, a corpus without
speaker ids trains whole, and train is one file per corpus; features simulated otherwise than the config asks are
named before any training reads them, a board session saying an unseen command is refused, and a board cut's units
leave out the runs its config names noise."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
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


def test_board_units_leave_out_the_runs_named_noise_and_refuse_a_name_no_utterance_holds(tmp_path: Path) -> None:
    rows = [{"item": f"{data.BOARD}/s1#{k}", "text": "bật đèn"} for k in (0, 2, 3)]
    (tmp_path / "shard_00000.items.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    (tmp_path / "manifest.yaml").write_text(yaml.safe_dump({"said": {"s1": 4}}), encoding="utf-8")
    every = data.board_units(tmp_path, "north", [])
    kept = data.board_units(tmp_path, "north", ["s1#2"])
    assert sorted(every) == [f"{data.BOARD}/s1#{k}" for k in (0, 2, 3)]
    assert (
        sorted(kept) == [f"{data.BOARD}/s1#0", f"{data.BOARD}/s1#3"]
        and kept[f"{data.BOARD}/s1#0"] == every[f"{data.BOARD}/s1#0"]
    )
    assert data.board_units(tmp_path, "north", ["s1#1"]) == every
    with pytest.raises(ValueError, match="s1#4"):
        data.board_units(tmp_path, "north", ["s1#4"])


def test_a_board_cut_of_another_listen_yaml_or_chain_run_is_stale(tmp_path: Path) -> None:
    device_cfg = load_device("scenes/device.yaml")
    rows, board = [{"session": "s1"}], {"min_s": 0.8, "max_s": 2.4, "repeat": 75}
    body = {"config": device_cfg, "sessions": ["s1"], "board": data.board_cut(board), "chain": data.BOARD_CHAIN}
    for listen_hash, fresh in ((device.cut_marks("listen")["listen_hash"], True), ("0x0", False), (None, False)):
        written = body | ({"listen_hash": listen_hash} if listen_hash else {})
        (tmp_path / "manifest.yaml").write_text(yaml.safe_dump(written), encoding="utf-8")
        assert data.board_built_as(tmp_path, device_cfg, rows, board) is fresh
    per_session = body | {"listen_hash": device.cut_marks("listen")["listen_hash"], "chain": None}
    (tmp_path / "manifest.yaml").write_text(yaml.safe_dump(per_session), encoding="utf-8")
    assert not data.board_built_as(tmp_path, device_cfg, rows, board)


def test_a_board_session_given_to_train_that_says_an_unseen_command_is_refused(tmp_path: Path, monkeypatch) -> None:
    board = {"manifest": "b.csv", "pcm_shift": 13, "train": ["s1", "s2"]}
    monkeypatch.setattr(data, "load_yaml", lambda _path: {"unseen": ["chup_anh"], "eval": {"board": board}})
    for prompt, refused in (("mở quạt", False), ("Chụp ảnh!", True)):
        (tmp_path / "b.csv").write_text(f"session,pcm_shift,prompt\ns1,13,bật đèn\ns2,13,{prompt}\n", encoding="utf-8")
        if refused:
            with pytest.raises(ValueError, match="s2"):
                data.board_rows({"manifests": tmp_path})
        else:
            assert [r["session"] for r in data.board_rows({"manifests": tmp_path})] == ["s1", "s2"]


def test_val_commands_hold_real_clips_heard_saying_just_a_learned_command_at_most_per_command(tmp_path: Path) -> None:
    folder = tmp_path / "speech" / "ext"
    folder.mkdir(parents=True)
    rows = [
        ("bat_den/a.wav", "bật đèn", "bật đèn.", "public"),
        ("bat_den/b.wav", "bật đèn", "bật đèn lên", "public"),
        ("bat_den/c.wav", "bật đèn", "Bật đèn!", "public"),
        ("bat_den/d.wav", "bật đèn", "bật đèn", "public"),
        ("bat_den/s.wav", "bật đèn", "bật đèn", "synth"),
        ("chup_anh/e.wav", "chụp ảnh", "chụp ảnh", "public"),
    ]
    head = "file\tphrase\tseconds\torigin\ttext\tsource\trevision\tkey\theard\n"
    body = "".join(f"{f}\t{p}\t0.8\t{o}\t-\t-\t-\t-\t{h}\n" for f, p, h, o in rows)
    (folder / "clips.tsv").write_text(head + body, encoding="utf-8")
    learned = [{"id": "bat_den", "text": "bật đèn"}, {"id": "tat_den", "text": "tắt đèn"}]
    got = data.command_rows({"extract": "ext", "per_command": 2, "seed": 3}, tmp_path, learned)
    assert len(got) == 2 and len({r.item for r in got}) == 2
    assert {r.item for r in got} <= {f"speech/ext/bat_den/{n}.wav" for n in "acd"}
    assert all(r.spk == splits.ABSENT and r.origin == "public" for r in got)
    assert got == data.command_rows({"extract": "ext", "per_command": 2, "seed": 3}, tmp_path, learned)
    every = data.command_rows({"extract": "ext", "per_command": 10, "seed": 3}, tmp_path, learned)
    assert {r.item for r in every} == {f"speech/ext/bat_den/{n}.wav" for n in "acd"}


def test_the_board_cut_keeps_windows_holding_one_utterance_alone_named_by_it(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("torch")
    from srpipe.tasks.command import eval as gate

    row = {"session": "s1", "board": "b", "prompt": "bật đèn"}
    on, alone = np.zeros(1100, dtype=bool), np.zeros(1100, dtype=bool)
    for first, stop in ((60, 130), (200, 260), (320, 400), (460, 520), (560, 620), (700, 760), (880, 950)):
        on[first:stop] = True
    for first, stop in ((62, 128), (322, 350), (380, 398), (462, 618), (705, 755), (860, 1020)):
        alone[first:stop] = True
    features, pitch = np.zeros((1100, 80), np.float32), np.zeros((1100, 3), np.float32)
    monkeypatch.setattr(data, "board_rows", lambda _paths: [row])
    monkeypatch.setattr(gate, "heard_rows", lambda *_: iter([(row, None, on, features, pitch)]))
    monkeypatch.setattr(gate, "said_alone", lambda *_: device.utterances(alone))
    cfg = {
        "features": "scenes/device.yaml",
        "split": {"board": {"min_s": 0.8, "max_s": 2.4, "repeat": 1}},
        "simulate": {"train_dtype": "float32"},
    }
    said = data.cut_board(cfg, {"raw": tmp_path}, tmp_path / "board")
    items = [json.loads(line) for line in (tmp_path / "board" / "shard_00000.items.jsonl").read_text().splitlines()]
    assert [i["item"] for i in items] == [f"{data.BOARD}/s1#0", f"{data.BOARD}/s1#4"]
    assert all(i["text"] == "bật đèn" for i in items) and "1 outside 0.8-2.4 s, 4 not one utterance alone" in said
    assert yaml.safe_load((tmp_path / "board" / "manifest.yaml").read_text())["said"] == {"s1": 6}
