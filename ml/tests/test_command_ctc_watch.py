"""srpipe.tasks.command.ctc.watch: the words tone tells apart, never an open rhyme's; Gate 3 and the owner check in
figures; a checkpoint left alone is taken at once."""

from __future__ import annotations

import os
import time
from collections import Counter
from pathlib import Path

import pytest

pytest.importorskip("torch")
pytest.importorskip("parselmouth")

from srpipe.tasks.command import eval as gate
from srpipe.tasks.command.ctc import watch

NORTH = 0


@pytest.mark.parametrize(
    ("text", "word"), [("tắt đèn", "tắt"), ("bật quạt", "bật"), ("mở cửa", None), ("chụp ảnh", "chụp")]
)
def test_a_command_is_split_by_its_first_word_when_tone_tells_it_apart(text: str, word: str | None) -> None:
    assert watch.checked_word(text, NORTH) == word


def heard(command: str, gap: int) -> gate.Heard:
    return gate.Heard(command, 900, 100, gap)


def test_gate_3_counts_commands_rejections_and_each_checked_word() -> None:
    results = [
        gate.Scored("s1", "cmd", "100", "tắt đèn", "tat_den", [heard("tat_den", 100), heard("bat_den", 100)]),
        gate.Scored("s2", "cmd", "100", "mở cửa", "mo_cua", [heard("mo_cua", 500)]),
        gate.Scored("s3", "near", "100", "tắt điện", gate.REJECT, [heard("tat_den", 100), heard(gate.REJECT, 0)]),
    ]
    word_of = {"tat_den": "tắt", "bat_den": "bật", "mo_cua": None}
    got = watch.gate_entry(results, word_of, 200, 50)
    assert (got["commands"], got["right"], got["accepted"]) == (3, 2, 1)
    assert (got["to_reject"], got["rejected"]) == (2, 1)
    assert got["words"] == {"tắt": {"utterances": 2, "right": 1, "accepted": 1}}


def test_the_owner_check_sums_each_day_and_word_as_recorded() -> None:
    every = {"right": 3, "accepted": 1}
    report = {
        "recorded": [
            {"day": "07/10", "cm": "100", "word": "tắt", "utterances": 4} | every,
            {"day": "07/10", "cm": "300", "word": "tắt", "utterances": 5} | every,
        ],
        "flips": {"tắt": {"kept": 6, "turned": 2}},
    }
    report["held"] = {
        "voicing": [{"day": "07/10", "cm": "100", "word": "tắt", "utterances": 4, "right": 0, "accepted": 0}]
    }
    got = watch.owner_entry(report)
    assert got["recorded"] == {"07/10 tắt": dict(Counter(utterances=9, right=6, accepted=2))}
    assert got["turned"] == {"tắt": {"kept": 6, "turned": 2}}
    assert got["held"] == {"voicing": {"07/10 tắt": {"utterances": 4, "right": 0, "accepted": 0}}}


def test_a_checkpoint_left_alone_is_taken_at_once(tmp_path: Path) -> None:
    path = tmp_path / "step_002000.pt"
    path.write_bytes(b"weights")
    old = time.time() - 60.0
    os.utime(path, (old, old))
    started = time.monotonic()
    watch.landed(path, poll_s=30.0, settled_s=5.0)
    assert time.monotonic() - started < 1.0
