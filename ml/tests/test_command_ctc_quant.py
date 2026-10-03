"""The ctc ladder: features padded and normalised as training reads them, calibration drawn only from sentences the
graph holds and stacked for a batch, the Gate 3 row, the rows of every step kept in one file, the best calibration
within the tie, a norm left unfused refused, and rung 4 training a batch graph the graph of one then carries."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

torch = pytest.importorskip("torch")

from srpipe.compress.quant import esp_ppq_patches, ptq_espdl, qat_espdl  # noqa: E402
from srpipe.core.config import load_yaml  # noqa: E402
from srpipe.tasks.command import ctc  # noqa: E402
from srpipe.tasks.command import eval as gate  # noqa: E402
from srpipe.tasks.command.ctc import probe, qat, quant, train  # noqa: E402
from srpipe.tasks.command.ctc.model import encoder  # noqa: E402
from srpipe.tasks.command.ctc.postproc import ctc_score  # noqa: E402

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


def test_the_gate_row_counts_best_accepted_and_false_accepts() -> None:
    heard = iter(
        [
            gate.Heard("bat_den", 900, 100, 100),
            gate.Heard("bat_den", 900, 10, 100),
            gate.Heard("tat_den", 900, 100, 100),
            gate.Heard("bat_den", 900, 100, 100, whole=False),
            gate.Heard("bat_den", 900, 100, 200),
            gate.Heard("bat_den", 900, 100, 900),
        ]
    )
    windows = [
        gate.Scored("a", "cmd", "100", "bật đèn", "bat_den", [0, 1, 2, 5]),
        gate.Scored("b", "neg", "100", "bật điện", gate.REJECT, [3, 4]),
    ]
    row = quant.gate_row(None, windows, 300, 50, heard_by=lambda net, x: next(heard))
    assert row == {"best_right": "3/4", "accepted_right": "1/4", "false_accepts": "1/2"}


def test_calibration_stacks_into_batches_and_leaves_a_short_one_out() -> None:
    calib = [torch.full((1, DIMS, 4), float(k)) for k in range(5)]
    out = quant.batched(calib, 2)
    assert [tuple(t.shape) for t in out] == [(2, DIMS, 4)] * 2 and float(out[1][0, 0, 0]) == 2.0


def gate_of(accepted: int, error: float, false_accepts: int = 3) -> dict:
    return {"accepted_right": f"{accepted}/112", "false_accepts": f"{false_accepts}/86", "unit_error_rate": error}


def test_each_step_keeps_the_rows_of_the_others(tmp_path: Path) -> None:
    quant.recorded(tmp_path, {"rungs": 1}, {"float": gate_of(79, 0.34)})
    quant.recorded(tmp_path, {"qat": 2}, {"qat": gate_of(75, 0.35)})
    kept = yaml.safe_load(quant.ladder_file(tmp_path).read_text(encoding="utf-8"))
    assert list(kept["rows"]) == ["float", "qat"] and (kept["rungs"], kept["qat"]) == (1, 2)


def test_calibrations_within_the_tie_go_to_the_lowest_error(tmp_path: Path) -> None:
    quant.recorded(tmp_path, {}, {"a": gate_of(65, 0.30), "b": gate_of(73, 0.36), "c": gate_of(74, 0.40)})
    assert quant.best_calibration(tmp_path, ["a", "b", "c"], 5) == "b"
    assert quant.best_calibration(tmp_path, ["a", "b", "c"], 0) == "c"
    assert quant.best_calibration(tmp_path, ["a", "b", "c"], 9) == "a"
    with pytest.raises(ValueError, match="ptq step first"):
        quant.best_calibration(tmp_path, ["a", "d"], 5)


def sentences(rng: np.random.Generator, hops: list[int]) -> train.Sentences:
    first = np.cumsum([0, *hops[:-1]])
    units = [np.array([1 + k % 5, 1 + (k + 1) % 5]) for k in range(len(hops))]
    return train.Sentences(rng.normal(size=(sum(hops), DIMS)).astype(np.float32), first, np.array(hops), units)


def pooled(folder: Path, rng: np.random.Generator, hops: list[int]) -> train.Pool:
    """The sentences of sentences() as a finished build of one shard, through the trainer's pool."""
    folder.mkdir(parents=True)
    data = sentences(rng, hops)
    np.save(folder / "shard_00000.features.npy", data.features[:, : DIMS - 3].astype(np.float16))
    np.save(folder / "shard_00000.pitch.npy", data.features[:, DIMS - 3 :].astype(np.float16))
    rows = [
        {"item": f"s{k}", "frame_offset": int(a), "n_frames": int(n)}
        for k, (a, n) in enumerate(zip(data.first, hops, strict=True))
    ]
    (folder / "shard_00000.items.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    listed = {f"shard_00000{s}": "-" for s in (".features.npy", ".pitch.npy", ".items.jsonl")}
    (folder / "manifest.yaml").write_text(yaml.safe_dump({"pitch": True, "sha256": listed}))
    units = {f"s{k}": (u - 1).tolist() for k, u in enumerate(data.units)}
    return train.Pool(train.shards_of([folder], units, 64), DIMS, 1 << 20, 1, 0)


def test_rung_4_trains_a_batch_graph_and_the_graph_of_one_carries_it(tmp_path: Path) -> None:
    cfg = load_yaml(ctc.CONFIG)
    cfg["quant"]["qat"] |= {"steps": 2, "eval_every": 1}
    torch.manual_seed(0)
    model = probe.draw_norm_scales(encoder.build(cfg), *cfg["probe"]["norm_scale"]).eval()
    rng = np.random.default_rng(4)
    calib = [torch.from_numpy(rng.normal(size=(1, DIMS, 64)).astype(np.float32)) for _ in range(4)]
    rungs = ptq_espdl.ladder(quant.LADDER) | {"equalization": None, "bias_correction": False, "calibration": "minmax"}
    wide = quant.quantized(model, quant.batched(calib, 2), tmp_path / "b", rungs, cfg["esp_ppq_patches"])
    sets = {"train": pooled(tmp_path / "t", rng, [48, 40, 56, 32]), "val": sentences(rng, [40, 48, 32])}
    stats = (np.zeros(DIMS, np.float32), np.ones(DIMS, np.float32))
    with esp_ppq_patches.applied(cfg["esp_ppq_patches"]):
        history = qat.fit(wide, sets, stats, cfg, cfg, model, "cpu")
    assert [row["step"] for row in history] == [0, 1, 2] and all(row["unit_error_rate"] >= 0 for row in history)
    one = quant.quantized(model, calib, tmp_path / "1", rungs, cfg["esp_ppq_patches"])
    with esp_ppq_patches.applied(cfg["esp_ppq_patches"]):
        qat_espdl.carry(wide, one, calib[0].numpy())


def test_the_int8_net_decides_a_board_window_as_the_float_net_is_called(tmp_path: Path) -> None:
    cfg = load_yaml(ctc.CONFIG)
    torch.manual_seed(0)
    model = probe.draw_norm_scales(encoder.build(cfg), *cfg["probe"]["norm_scale"]).eval()
    rng = np.random.default_rng(6)
    calib = [torch.from_numpy(rng.normal(size=(1, DIMS, 64)).astype(np.float32)) for _ in range(2)]
    rungs = ptq_espdl.ladder(quant.LADDER) | {"equalization": None, "bias_correction": False, "calibration": "minmax"}
    graph = quant.quantized(model, calib, tmp_path, rungs, cfg["esp_ppq_patches"])
    stats = (np.zeros(DIMS, np.float32), np.ones(DIMS, np.float32))
    int8 = quant.Int8Net(graph, 64, *stats, model, cfg["esp_ppq_patches"])
    lexicon = [[np.array([0, 1], np.uint8)], [np.array([2], np.uint8)]]
    heard = gate.ctc_heard(gate.Ctc(int8, *stats, ["a", "b"], lexicon, cfg), rng.normal(size=(50, DIMS)).astype("f4"))
    assert heard.command in ("a", "b", gate.REJECT) and 0 <= heard.gap <= ctc_score.CAP


def test_a_net_whose_norms_stay_an_int8_chain_is_refused(tmp_path: Path) -> None:
    cfg = load_yaml(ctc.CONFIG)
    torch.manual_seed(0)
    rng = np.random.default_rng(5)
    calib = [torch.from_numpy(rng.normal(size=(1, DIMS, 64)).astype(np.float32)) for _ in range(4)]
    rungs = ptq_espdl.ladder(quant.LADDER) | {"equalization": None, "bias_correction": False, "calibration": "minmax"}
    with pytest.raises(ValueError, match="fused 0 of 6 norms"):
        quant.quantized(encoder.build(cfg).eval(), calib, tmp_path, rungs, cfg["esp_ppq_patches"])
