"""Command split: an unseen command leaves learning but stays in test, speakers keep one role, a corpus without
speaker ids trains whole, and train is one file per corpus."""

from __future__ import annotations

import pytest

from srpipe.core import corpus
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
