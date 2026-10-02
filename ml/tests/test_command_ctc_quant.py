"""ctc int8 export: features padded and normalised as training reads them, calibration drawn only from sentences the
graph holds, and the Gate 3 row counting best, accepted and falsely accepted utterances."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("torch")

from srpipe.tasks.command import eval as gate
from srpipe.tasks.command.ctc import quant

DIMS = 43


def test_a_sentence_pads_with_raw_zeros_then_normalises_like_a_training_batch() -> None:
    mean, std = np.full(DIMS, 2.0, np.float32), np.full(DIMS, 4.0, np.float32)
    x = np.ones((3, DIMS), np.float32) * 6.0
    out = quant.padded(x, 8, mean, std)
    assert out.shape == (1, DIMS, 8) and out.dtype == np.float32
    assert np.all(out[0, :, :3] == 1.0) and np.all(out[0, :, 3:] == -0.5)


def built(root: Path, lengths: list[int]) -> Path:
    folder = root / "train_a"
    folder.mkdir(parents=True)
    total = sum(lengths)
    np.save(folder / "shard_00000.features.npy", np.zeros((total, DIMS - 3), np.float32))
    np.save(folder / "shard_00000.pitch.npy", np.zeros((total, 3), np.float32))
    rows, at = [], 0
    for k, n in enumerate(lengths):
        rows.append(json.dumps({"item": f"s{k}", "frame_offset": at, "n_frames": n}))
        at += n
    (folder / "shard_00000.items.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return root


def test_calibration_draws_only_sentences_the_graph_holds(tmp_path: Path) -> None:
    root = built(tmp_path, [10, 40, 12, 9, 16])
    spec = {"seed": 1, "calib_sentences": 4, "hops": 16}
    calib = quant.calibration({}, spec, np.zeros(DIMS, np.float32), np.ones(DIMS, np.float32), root)
    assert len(calib) == 4 and all(tuple(c.shape) == (1, DIMS, 16) for c in calib)
    with pytest.raises(ValueError):
        quant.calibration({}, spec | {"calib_sentences": 5}, np.zeros(DIMS), np.ones(DIMS), root)


def test_the_gate_row_counts_best_accepted_and_false_accepts(monkeypatch) -> None:
    heard = iter(
        [
            gate.Heard("bat_den", 900, 100, 100),
            gate.Heard("bat_den", 900, 10, 100),
            gate.Heard("tat_den", 900, 100, 100),
            gate.Heard("bat_den", 900, 100, 200),
            gate.Heard("bat_den", 900, 100, 900),
        ]
    )
    monkeypatch.setattr(gate, "ctc_heard", lambda net, x: next(heard))
    windows = [
        gate.Scored("a", "cmd", "100", "bật đèn", "bat_den", [0, 1, 2]),
        gate.Scored("b", "neg", "100", "bật điện", gate.REJECT, [3, 4]),
    ]
    row = quant.gate_row(None, windows, 300, 50)
    assert row == {"best_right": "2/3", "accepted_right": "1/3", "false_accepts": "1/2"}
