"""Wake training: the smoothing and lockout the device mirrors, labels around a positive's end, windows that hold
the whole label past the warm-up, and a tiny run whose sweep never gains recall or false accepts as the threshold
rises."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from srpipe.core.config import load_yaml
from srpipe.tasks.wake import CONFIG, data, train
from srpipe.tasks.wake import eval as wake_eval
from srpipe.tasks.wake.postproc.smooth import smooth, triggers

BANDS = 8


def test_smoothing_averages_the_hops_so_far_and_a_lockout_swallows_repeats() -> None:
    np.testing.assert_allclose(smooth(np.array([1, 0, 1, 1, 0, 0]), 3), [1, 0.5, 2 / 3, 2 / 3, 2 / 3, 1 / 3])
    s = np.array([0, 0.9, 0.9, 0.1, 0.9, 0, 0, 0, 0.95, 0])
    assert triggers(s, 0.5, 3).tolist() == [1, 8]
    assert triggers(s, 0.5, 0).tolist() == [1, 2, 4, 8]
    assert triggers(s, 0.99, 3).tolist() == []


def processed(folder: Path, items: list[tuple[int, int, int]], rng: np.random.Generator, mark: bool) -> Path:
    """A finished build of items (n_frames, speech_first, speech_stop); positives carry a bump in band 0."""
    folder.mkdir(parents=True)
    feats, rows, offset = [], [], 0
    for n, first, stop in items:
        x = rng.normal(-8.0, 1.0, (n, BANDS)).astype(np.float32)
        if mark:
            x[first:stop, 0] += 6.0
        feats.append(x)
        rows.append({"item": f"i{offset}", "frame_offset": offset, "n_frames": n, "speech_frames": [first, stop]})
        offset += n
    np.save(folder / "shard_00000.features.npy", np.concatenate(feats))
    (folder / "shard_00000.items.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (folder / "shard_00001.items.jsonl").write_text("left over from a larger build, not in the manifest\n")
    listed = {"shard_00000.features.npy": "-", "shard_00000.items.jsonl": "-"}
    (folder / "manifest.yaml").write_text(yaml.safe_dump({"sha256": listed}))
    return folder


def test_labels_sit_around_the_end_of_a_positive_and_a_build_must_be_finished(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    folder = processed(tmp_path / "pos", [(40, 10, 25), (40, 5, 20)], rng, mark=True)
    (shard,) = data.load_set(folder, True, (2, 3), "float16")
    assert shard.features.dtype == np.float16
    assert np.flatnonzero(shard.labels).tolist() == [23, 24, 25, 26, 27, 58, 59, 60, 61, 62]
    (negative,) = data.load_set(folder, False, (2, 3), "float32")
    assert not negative.labels.any()
    (folder / "manifest.yaml").unlink()
    with pytest.raises(FileNotFoundError, match="make wake-features"):
        data.load_set(folder, True, (2, 3), "float32")


def tiny_cfg() -> dict:
    cfg = load_yaml(CONFIG)
    cfg["model"] = {"kernel": 3, "channels": 4, "dilations": [1, 2]}
    cfg["train"] |= {"window_hops": 32, "warmup_hops": 8, "batch": 16, "steps": 40, "eval_every": 20}
    cfg["train"]["label_s"] = [0.032, 0.048]
    cfg["eval"]["thresholds"] = {"first": 0.05, "last": 0.95, "step": 0.05}
    return cfg


def test_windows_hold_the_label_past_the_warm_up(tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    around = data.label_hops(tiny_cfg()["train"]["label_s"])
    pos = data.load_set(processed(tmp_path / "p", [(60, 20, 40)] * 4, rng, True), True, around, "float16")
    neg = data.load_set(processed(tmp_path / "n", [(60, 0, 60)] * 4, rng, False), False, around, "float16")
    x, y = train.Windows(pos, neg, tiny_cfg(), np.random.default_rng(2)).batch()
    assert x.shape == (16, 32, BANDS) and y.shape == (16, 32)
    assert all(y[k, 8:].any() for k in range(4)) and not y[4:].any()


def test_a_tiny_run_keeps_its_best_weights_and_its_sweep_is_monotonic(tmp_path: Path) -> None:
    rng = np.random.default_rng(3)
    cfg = tiny_cfg()
    around = data.label_hops(cfg["train"]["label_s"])
    pos = [(60, 20, 40)] * 6
    neg = [(60, 0, 60)] * 6
    sets = {
        "train_pos": data.load_set(processed(tmp_path / "tp", pos, rng, True), True, around, "float16"),
        "train_neg": data.load_set(processed(tmp_path / "tn", neg, rng, False), False, around, "float16"),
        "val_pos": data.load_set(processed(tmp_path / "vp", pos, rng, True), True, around, "float32"),
        "val_neg": data.load_set(processed(tmp_path / "vn", neg, rng, False), False, around, "float32"),
    }
    model, stats, history = train.train(cfg, sets, "cpu")
    assert [row["step"] for row in history] == [20, 40] and stats["best"]["step"] in (20, 40)
    result = wake_eval.sweep(model, sets["val_pos"], sets["val_neg"], stats["mean"], stats["std"], cfg, "cpu")
    assert result.positives == 6 and np.all(np.diff(result.recall) <= 0)
    assert np.all(np.diff(result.false_accepts_per_hour) <= 0)
