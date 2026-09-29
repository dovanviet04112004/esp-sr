"""Wake training: the smoothing and lockout the device mirrors, labels around a positive's end, windows that hold
the whole label past the warm-up, sentences for the CTC side task, a tiny run that keeps its last weights and saves each
evaluated one, a board table whose rows keep their columns, and a run scored with the network it was trained as."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import torch
import yaml

from srpipe.core.config import load_yaml
from srpipe.generated import grid
from srpipe.tasks.wake import CONFIG, candidates, data, train
from srpipe.tasks.wake import eval as wake_eval
from srpipe.tasks.wake.model.tcn import Tcn
from srpipe.tasks.wake.postproc.smooth import smooth, triggers

BANDS = 8


def test_smoothing_averages_the_hops_so_far_and_a_lockout_swallows_repeats() -> None:
    np.testing.assert_allclose(smooth(np.array([1, 0, 1, 1, 0, 0]), 3), [1, 0.5, 2 / 3, 2 / 3, 2 / 3, 1 / 3])
    s = np.array([0, 0.9, 0.9, 0.1, 0.9, 0, 0, 0, 0.95, 0])
    assert triggers(s, 0.5, 3).tolist() == [1, 8]
    assert triggers(s, 0.5, 0).tolist() == [1, 2, 4, 8]
    assert triggers(s, 0.99, 3).tolist() == []


def processed(
    folder: Path, items: list[tuple[int, int, int]], rng: np.random.Generator, mark: bool, origin: str = "public"
) -> Path:
    """A finished build of items (n_frames, speech_first, speech_stop); positives carry a bump in band 0."""
    folder.mkdir(parents=True)
    feats, rows, offset = [], [], 0
    for n, first, stop in items:
        x = rng.normal(-8.0, 1.0, (n, BANDS)).astype(np.float32)
        if mark:
            x[first:stop, 0] += 6.0
        feats.append(x)
        rows.append(
            {
                "item": f"i{offset}",
                "origin": origin,
                "frame_offset": offset,
                "n_frames": n,
                "speech_frames": [first, stop],
            }
        )
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


def test_sentences_carry_the_lead_before_them_their_units_and_nothing_too_long(tmp_path: Path) -> None:
    rng = np.random.default_rng(8)
    cfg = tiny_cfg()
    cfg["train"]["aux"] |= {"batch": 64, "max_s": 50 * grid.HOP_SAMPLES / grid.SAMPLE_RATE_HZ}
    around = data.label_hops(cfg["train"]["label_s"])
    (shard,) = data.load_set(
        processed(tmp_path / "n", [(30, 0, 30), (50, 0, 50), (60, 0, 60)], rng, False), False, around, "float32"
    )
    units = {"i0": [0, 5], "i30": [1, 2, 3]}
    sentences = train.Sentences([shard], units | {"i80": [4]}, cfg, np.random.default_rng(9))
    assert sorted(i[1] for i in sentences.items) == [0, 30]
    x, lengths, targets, counts = sentences.batch()
    lead = cfg["train"]["warmup_hops"]
    assert x.shape == (64, lead + lengths.max(), BANDS) and set(lengths.tolist()) == {30, 50}
    assert counts.sum() == len(targets) and set(targets.tolist()) <= {1, 2, 3, 4, 6}
    k = int(np.flatnonzero(lengths == 50)[0])
    np.testing.assert_array_equal(x[k, : lead + 50], shard.features[30 - lead : 80])
    j = int(np.flatnonzero(lengths == 30)[0])
    np.testing.assert_array_equal(x[j, lead : lead + 30], shard.features[:30])
    with pytest.raises(ValueError, match=r"aux\.max_s"):
        train.Sentences([shard], {"i80": [4]}, cfg, np.random.default_rng(9))


def test_a_tiny_run_keeps_its_last_weights_saves_each_evaluated_one_and_its_sweep_is_monotonic(tmp_path: Path) -> None:
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
    units = {i["item"]: [3, 7, 3] for s in sets["train_neg"] for i in s.items}
    model, stats, history = train.train(cfg, sets, "cpu", units, tmp_path / "run")
    assert [row["step"] for row in history] == [20, 40] and stats["kept"] == history[-1]
    assert all(row["ctc_loss"] > 0 for row in history)
    last = torch.load(wake_eval.checkpoint(tmp_path / "run", 40))
    assert last.keys() == model.state_dict().keys() and wake_eval.checkpoint(tmp_path / "run", 20).exists()
    assert all(torch.equal(last[k], v) for k, v in model.state_dict().items())
    result = wake_eval.sweep(model, sets["val_pos"], sets["val_neg"], stats["mean"], stats["std"], cfg, "cpu")
    assert result.positives == 6 and np.all(np.diff(result.recall) <= 0)
    assert np.all(np.diff(result.false_accepts_per_hour) <= 0)
    _, _, plain = train.train(cfg, sets, "cpu")
    assert "ctc_loss" not in plain[-1]


def test_hard_windows_take_their_share_hold_the_phrase_and_carry_no_label(tmp_path: Path) -> None:
    rng = np.random.default_rng(4)
    cfg = tiny_cfg()
    cfg["train"]["hard_share"] = 0.25
    around = data.label_hops(cfg["train"]["label_s"])
    pos = data.load_set(processed(tmp_path / "p", [(60, 20, 40)] * 4, rng, True), True, around, "float16")
    neg = data.load_set(processed(tmp_path / "n", [(60, 0, 60)] * 4, rng, False), False, around, "float16")
    tts = data.load_set(processed(tmp_path / "h", [(60, 20, 40)] * 4, rng, True, "synth"), False, around, "float16")
    windows = train.Windows(pos, neg, cfg, np.random.default_rng(5), tts)
    assert windows.counts() == (4, 4, 8)
    x, y = windows.batch()
    assert x.shape == (16, 32, BANDS) and not y[4:].any()
    after, warmup = around[1], cfg["train"]["warmup_hops"]
    for _ in range(50):
        _, item = windows.hard_items[int(windows.rng.integers(len(windows.hard_items)))]
        stop = windows.hard_stop(item)
        end = item["frame_offset"] + item["speech_frames"][1]
        assert end + after <= stop <= end + after + windows.slack
        assert stop - cfg["train"]["window_hops"] + warmup <= end
    corpus = data.load_set(processed(tmp_path / "c", [(60, 10, 50)] * 2, rng, False), False, around, "float16")
    spoken = train.Windows(pos, neg, cfg, np.random.default_rng(6), corpus)
    for _, item in spoken.hard_items:
        first = item["frame_offset"] + item["speech_frames"][0]
        end = item["frame_offset"] + item["speech_frames"][1]
        assert all(first < spoken.hard_stop(item) <= end + after for _ in range(20))
    assert train.Windows(pos, neg, cfg, np.random.default_rng(7)).counts() == (4, 0, 12)


def test_board_rows_keep_their_columns_when_a_prompt_lists_phrases() -> None:
    results = [
        wake_eval.BoardSession("s1", "wake", "chào mi na", 40.0, [0.9, 0.2], 1, 0),
        wake_eval.BoardSession("s2", "neg", "chào mi | mi na | chào mẹ", 60.0, [0.1], 0, 0),
    ]
    rows = [line for line in wake_eval.board_table(results, 0.5).splitlines() if line.startswith("| s")]
    header = wake_eval.board_table([], 0.5).splitlines()[2]
    assert len(rows) == 2
    assert all(row.replace("\\|", "").count("|") == header.count("|") for row in rows)


def test_a_run_is_scored_with_its_own_network_and_the_current_scoring_rules(tmp_path: Path) -> None:
    cfg = load_yaml(CONFIG)
    trained = cfg | {"model": cfg["model"] | {"channels": cfg["model"]["channels"] // 4}, "features": "trained.yaml"}
    (tmp_path / "config.resolved.yaml").write_text(yaml.safe_dump(trained), encoding="utf-8")
    net = Tcn(BANDS, **trained["model"])
    torch.save(net.state_dict(), tmp_path / "model.pt")
    np.savez(tmp_path / "band_stats.npz", mean=np.zeros(BANDS), std=np.ones(BANDS))
    history = [{"step": 10, "threshold": 0.6}, {"step": 20, "threshold": 0.8}]
    (tmp_path / "metrics.yaml").write_text(yaml.safe_dump({"val": history[-1], "history": history}), encoding="utf-8")
    earlier = Tcn(BANDS, **trained["model"])
    wake_eval.checkpoint(tmp_path, 10).parent.mkdir()
    torch.save(earlier.state_dict(), wake_eval.checkpoint(tmp_path, 10))
    scoring = cfg | {"eval": cfg["eval"] | {"smooth_hops": 3}}
    model, stats, used, threshold = wake_eval.load_run(tmp_path, scoring)
    assert threshold == 0.8 and len(stats["mean"]) == BANDS
    assert model.inp.out_channels == trained["model"]["channels"]
    assert used["model"] == trained["model"] and used["features"] == "trained.yaml"
    assert used["eval"]["smooth_hops"] == 3
    model, _, _, threshold = wake_eval.load_run(tmp_path, scoring, 10)
    assert threshold == 0.6 and torch.equal(model.inp.weight, earlier.inp.weight)
    with pytest.raises(ValueError, match="no step 30"):
        wake_eval.load_run(tmp_path, scoring, 30)


def test_a_wake_session_for_another_word_is_scored_as_a_negative() -> None:
    word = candidates.sounds("trợ lý")
    assert wake_eval.session_kind("wake", "Trợ lý!", word) == "wake"
    assert wake_eval.session_kind("wake", "trợ lí", word) == "wake"
    assert wake_eval.session_kind("wake", "chào mi na", word) == "neg"
    assert wake_eval.session_kind("cmd", "trợ lý bật đèn", word) == "cmd"
