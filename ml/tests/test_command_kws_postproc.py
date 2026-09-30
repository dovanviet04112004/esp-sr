"""The kws decision: each rejection rule decides where it should, the softmax survives logits a naive exp overflows,
and the committed golden set is what emit writes, its negative control one decision away from its positive case."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from srpipe.golden.gold import read_gold
from srpipe.tasks.command.kws.postproc import decide

N_COMMANDS = 3


def logits(**at: float) -> np.ndarray:
    row = np.zeros(N_COMMANDS + 2, dtype=np.float32)
    for index, value in at.items():
        row[int(index.removeprefix("c"))] = value
    return row


def test_a_confident_command_is_accepted_and_other_or_silence_winning_is_rejected() -> None:
    assert decide.decide(logits(c1=6.0), N_COMMANDS, 500, 100)[0] == 1
    assert decide.decide(logits(c3=6.0), N_COMMANDS, 0, 0)[0] == decide.REJECTED
    assert decide.decide(logits(c4=6.0), N_COMMANDS, 0, 0)[0] == decide.REJECTED


def test_a_low_winner_or_a_narrow_lead_is_rejected_and_the_thresholds_are_inclusive() -> None:
    row = logits(c0=2.0, c2=1.5)
    _, score, margin, rest = decide.decide(row, N_COMMANDS, 0, 0)
    assert rest == decide.permille(np.float32(decide.softmax(row)[3] + decide.softmax(row)[4]))
    assert decide.decide(row, N_COMMANDS, score, margin)[0] == 0
    assert decide.decide(row, N_COMMANDS, score + 1, 0)[0] == decide.REJECTED
    assert decide.decide(row, N_COMMANDS, 0, margin + 1)[0] == decide.REJECTED


def test_the_softmax_holds_for_logits_a_naive_exp_overflows() -> None:
    p = decide.softmax(np.array([200.0, -200.0, 199.0, 0.0, 0.0], dtype=np.float32))
    assert p.dtype == np.float32 and np.all(np.isfinite(p))
    assert abs(float(p.sum()) - 1.0) < 1e-6
    assert decide.decide(np.array([200.0, -200.0, 199.0, 0.0, 0.0]), N_COMMANDS, 0, 0)[0] == 0


def test_committed_cases_match_a_fresh_emit(tmp_path: Path) -> None:
    for path in decide.emit(tmp_path):
        committed = decide.GOLDEN_ROOT / path.relative_to(tmp_path)
        assert committed.read_bytes() == path.read_bytes(), f"{committed} is stale: rerun {decide.__name__}"


def test_the_negative_control_differs_from_its_positive_case_in_one_decision() -> None:
    root = decide.GOLDEN_ROOT / decide.BLOCK
    right, wrong = read_gold(root / "case_002.gold"), read_gold(root / "case_neg_000.gold")
    assert np.array_equal(right["logits"], wrong["logits"])
    differs = np.flatnonzero(np.any(right["decision"] != wrong["decision"], axis=1))
    assert len(differs) == 1
    assert right["decision"][differs[0], 0] >= 0 and wrong["decision"][differs[0], 0] == decide.REJECTED
