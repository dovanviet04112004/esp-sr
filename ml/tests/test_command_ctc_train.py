"""ctc training: sentences load with their units and nothing too long, batches pad to the net's chunk, the best path
merges repeats and drops blanks, and a tiny run evaluates, saves each evaluated net and keeps the last."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from srpipe.core.config import load_yaml
from srpipe.tasks.command import ctc

torch = pytest.importorskip("torch")

from srpipe.tasks.command.ctc import train  # noqa: E402
from srpipe.tasks.command.ctc.model import encoder  # noqa: E402

DIMS = encoder.n_dims(load_yaml(ctc.CONFIG))


def processed(folder: Path, hops: list[int], rng: np.random.Generator) -> Path:
    """A finished build with pitch of sentences s0, s1, ... of the given hops, one shard."""
    folder.mkdir(parents=True)
    rows, offset = [], 0
    for k, n in enumerate(hops):
        rows.append({"item": f"s{k}", "frame_offset": offset, "n_frames": n, "speech_frames": [0, n]})
        offset += n
    np.save(folder / "shard_00000.features.npy", rng.normal(size=(offset, DIMS - 3)).astype(np.float32))
    np.save(folder / "shard_00000.pitch.npy", rng.normal(size=(offset, 3)).astype(np.float32))
    (folder / "shard_00000.items.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    listed = {f"shard_00000{s}": "-" for s in (".features.npy", ".pitch.npy", ".items.jsonl")}
    (folder / "manifest.yaml").write_text(yaml.safe_dump({"pitch": True, "sha256": listed}))
    return folder


def test_sentences_keep_their_units_past_the_blank_and_leave_out_the_unread_and_the_long(tmp_path: Path) -> None:
    folder = processed(tmp_path / "a", [40, 90, 30], np.random.default_rng(0))
    data = train.load_role([folder], {"s0": [0, 5], "s1": [3], "s2": []}, 64, "float16")
    assert data.first.tolist() == [0] and data.hops.tolist() == [40] and data.units[0].tolist() == [1, 6]
    assert data.features.dtype == np.float16 and data.features.shape == (160, DIMS)
    with pytest.raises(ValueError, match="no sentence"):
        train.load_role([folder], {}, 64, "float16")


def test_a_batch_pads_to_the_chunk_and_holds_each_sentence_from_its_start(tmp_path: Path) -> None:
    data = train.load_role(
        [processed(tmp_path / "a", [10, 21], np.random.default_rng(1))], {"s0": [1], "s1": [2]}, 64, "float32"
    )
    x, hops, units = train.batch_of(data, np.array([1, 0]), 8)
    assert x.shape == (2, 24, DIMS) and hops.tolist() == [21, 10] and [u.tolist() for u in units] == [[3], [2]]
    np.testing.assert_array_equal(x[1, :10], data.features[:10])
    assert not x[1, 10:].any() and train.frames_of(hops, 2).tolist() == [11, 5]


def test_the_best_path_merges_repeats_and_drops_blanks() -> None:
    path = [0, 3, 3, 0, 3, 5, 5, 0]
    log_probs = np.log(np.full((7, len(path)), 0.01))
    log_probs[path, np.arange(len(path))] = 0.0
    assert train.best_path(log_probs) == [3, 3, 5]
    assert train.edit_distance([3, 3, 5], [3, 5]) == 1 and train.edit_distance([], [1, 2]) == 2


def test_a_tiny_run_evaluates_saves_each_evaluated_net_and_keeps_the_last(tmp_path: Path) -> None:
    rng = np.random.default_rng(2)
    units = {f"s{k}": [k % 5, (k + 1) % 5] for k in range(6)}
    sets = {
        "train": train.load_role([processed(tmp_path / "t", [48, 40, 56, 32, 48, 40], rng)], units, 64, "float16"),
        "val": train.load_role([processed(tmp_path / "v", [40, 48], rng)], units, 64, "float32"),
    }
    cfg = load_yaml(ctc.CONFIG)
    cfg["train"] |= {"batch": 2, "steps": 4, "eval_every": 2}
    net, (mean, std), history = train.train(cfg, sets, "cpu", tmp_path / "run")
    assert [row["step"] for row in history] == [2, 4] and len(mean) == len(std) == DIMS
    assert all(row["loss"] > 0 and row["unit_error_rate"] >= 0 for row in history)
    last = torch.load(train.checkpoint(tmp_path / "run", 4))
    assert all(torch.equal(last[k], v) for k, v in net.state_dict().items())


def test_a_stopped_run_resumed_from_its_last_checkpoint_ends_as_an_unbroken_one(tmp_path: Path, monkeypatch) -> None:
    rng = np.random.default_rng(3)
    units = {f"s{k}": [k % 5, (k + 1) % 5] for k in range(6)}
    sets = {
        "train": train.load_role([processed(tmp_path / "t", [48, 40, 56, 32, 48, 40], rng)], units, 64, "float16"),
        "val": train.load_role([processed(tmp_path / "v", [40, 48], rng)], units, 64, "float32"),
    }
    cfg = load_yaml(ctc.CONFIG)
    cfg["train"] |= {"batch": 2, "steps": 4, "eval_every": 2}
    whole, _, unbroken = train.train(cfg, sets, "cpu", tmp_path / "whole")
    evaluate, calls = train.evaluate, []

    def stop_at_the_second(*args):
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return evaluate(*args)

    monkeypatch.setattr(train, "evaluate", stop_at_the_second)
    with pytest.raises(KeyboardInterrupt):
        train.train(cfg, sets, "cpu", tmp_path / "stopped")
    monkeypatch.setattr(train, "evaluate", evaluate)
    assert torch.load(train.checkpoint(tmp_path / "stopped"), weights_only=False)["step"] == 2
    resumed, _, history = train.train(cfg, sets, "cpu", tmp_path / "stopped", resume=True)
    assert history == unbroken
    assert all(torch.equal(resumed.state_dict()[k], v) for k, v in whole.state_dict().items())
