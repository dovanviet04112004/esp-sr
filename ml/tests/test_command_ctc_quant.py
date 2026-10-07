"""The ctc ladder: features padded and normalised as training reads them, calibration whole spans of real hops from
sentence starts, stacked for a batch, the Gate 3 row, an int8 window decided as the chip's record is, the rows of every
step kept in one file, the best calibration within the tie, a norm left unfused refused, rung 4 training a batch graph
the graph of one then carries, and no deploy of a run cut by another listen.yaml or of a row whose input or output is
not int8; a pitch dim folded into the front reads as held at its mean and stays folded through rung 4, and a ladder
heard on Kaldi keeps its own folder and reads its mel_from build only when that is the contract's front."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

torch = pytest.importorskip("torch")

from srpipe.compress.quant import esp_ppq_patches, ptq_espdl, qat_espdl  # noqa: E402
from srpipe.core.config import contract_front, load_yaml  # noqa: E402
from srpipe.generated import listen  # noqa: E402
from srpipe.tasks.command import ctc  # noqa: E402
from srpipe.tasks.command import eval as gate  # noqa: E402
from srpipe.tasks.command.ctc import probe, qat, quant, train  # noqa: E402
from srpipe.tasks.command.ctc.model import encoder  # noqa: E402
from srpipe.tasks.command.ctc.postproc import ctc_score  # noqa: E402

DIMS = encoder.n_dims(load_yaml(ctc.CONFIG))


def test_a_sentence_pads_with_raw_zeros_then_normalises_like_a_training_batch() -> None:
    mean, std = np.full(DIMS, 2.0, np.float32), np.full(DIMS, 4.0, np.float32)
    x = np.ones((3, DIMS), np.float32) * 6.0
    out = quant.padded(x, 8, mean, std)
    assert out.shape == (1, DIMS, 8) and out.dtype == np.float32
    assert np.all(out[0, :, :3] == 1.0) and np.all(out[0, :, 3:] == -0.5)


def built(root: Path, lengths: list[int]) -> Path:
    """A train shard of sentences of the given hops back to back, every feature of a hop its index."""
    folder = root / "train_a"
    folder.mkdir(parents=True)
    total = sum(lengths)
    hop = np.arange(total, dtype=np.float32)[:, None]
    np.save(folder / "shard_00000.features.npy", np.repeat(hop, DIMS - 3, axis=1))
    np.save(folder / "shard_00000.pitch.npy", np.repeat(hop, 3, axis=1))
    rows, at = [], 0
    for k, n in enumerate(lengths):
        rows.append(json.dumps({"item": f"s{k}", "frame_offset": at, "n_frames": n}))
        at += n
    (folder / "shard_00000.items.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return root


def test_calibration_takes_whole_spans_of_real_hops_from_sentence_starts_never_padding(tmp_path: Path) -> None:
    root = built(tmp_path, [10, 40, 12, 9, 16])
    spec = {"seed": 1, "calib_sentences": 3, "hops": 30}
    calib = quant.calibration(spec, np.zeros(DIMS, np.float32), np.ones(DIMS, np.float32), root)
    assert sorted(int(c[0, 0, 0]) for c in calib) == [0, 10, 50]
    for c in calib:
        assert tuple(c.shape) == (1, DIMS, 30)
        start = float(c[0, 0, 0])
        np.testing.assert_array_equal(c[0].numpy(), np.arange(start, start + 30)[None].repeat(DIMS, 0))
    with pytest.raises(ValueError):
        quant.calibration(spec | {"calib_sentences": 4}, np.zeros(DIMS), np.ones(DIMS), root)


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


@pytest.mark.parametrize("hold", [(), (0,)])
def test_rung_4_trains_a_batch_graph_and_the_graph_of_one_carries_it(tmp_path: Path, hold: tuple) -> None:
    cfg = load_yaml(ctc.CONFIG)
    cfg["quant"]["qat"] |= {"steps": 2, "eval_every": 1}
    torch.manual_seed(0)
    model = probe.draw_norm_scales(encoder.build(cfg), *cfg["probe"]["norm_scale"]).eval()
    stats = (np.zeros(DIMS, np.float32), np.ones(DIMS, np.float32))
    model = quant.folded(gate.Ctc(model, *stats, [], [], cfg), hold).model
    rng = np.random.default_rng(4)
    calib = [torch.from_numpy(rng.normal(size=(1, DIMS, 64)).astype(np.float32)) for _ in range(4)]
    rungs = ptq_espdl.ladder(quant.LADDER) | {"equalization": None, "bias_correction": False, "calibration": "minmax"}
    wide = quant.quantized(model, quant.batched(calib, 2), tmp_path / "b", rungs, cfg["esp_ppq_patches"])
    sets = {"train": pooled(tmp_path / "t", rng, [48, 40, 56, 32]), "val": sentences(rng, [40, 48, 32])}
    with esp_ppq_patches.applied(cfg["esp_ppq_patches"]):
        history = qat.fit(wide, sets, stats, cfg, cfg, model, "cpu", hold)
    assert [row["step"] for row in history] == [0, 1, 2] and all(row["unit_error_rate"] >= 0 for row in history)
    weight = next(op for name, op in wide.operations.items() if "front/proj" in name).inputs[1].value
    held = weight[:, [weight.shape[1] - 3 + d for d in hold]]
    assert torch.all(held == 0) and bool(torch.any(weight[:, weight.shape[1] - 3 :] != 0))
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


@pytest.mark.parametrize("recorded", [{}, {"listen_hash": listen.HASH ^ 1}])
def test_a_run_without_this_listen_hash_is_not_deployed(
    recorded: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gate, "load_ctc", lambda _run: SimpleNamespace(cfg=recorded))
    with pytest.raises(ValueError, match="would leave its command off"):
        quant.step_deploy(load_yaml(ctc.CONFIG), tmp_path, "row")


def test_the_ends_of_a_graph_are_int8_unless_a_layer_there_goes_int16(tmp_path: Path) -> None:
    cfg = load_yaml(ctc.CONFIG)
    torch.manual_seed(0)
    model = probe.draw_norm_scales(encoder.build(cfg), *cfg["probe"]["norm_scale"]).eval()
    rng = np.random.default_rng(7)
    calib = [torch.from_numpy(rng.normal(size=(1, DIMS, 64)).astype(np.float32)) for _ in range(2)]
    rungs = ptq_espdl.ladder(quant.LADDER) | {"equalization": None, "bias_correction": False, "calibration": "minmax"}
    graph = quant.quantized(model, calib, tmp_path / "a", rungs, cfg["esp_ppq_patches"])
    assert ptq_espdl.io_bits(graph) == (8, 8)
    first = next(iter(graph.inputs.values())).dest_ops[0].name
    wide = quant.quantized(model, calib, tmp_path / "b", rungs | {"int16_ops": [first]}, cfg["esp_ppq_patches"])
    assert ptq_espdl.io_bits(wide) == (16, 8)


def test_a_row_the_chip_cannot_read_or_give_is_not_deployed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "load_ctc", lambda _run: SimpleNamespace(cfg={"listen_hash": listen.HASH}))
    monkeypatch.setattr(quant.export_espdl, "load_native", lambda _path: "graph")
    monkeypatch.setattr(quant.ptq_espdl, "io_bits", lambda _graph: (8, 16))
    with pytest.raises(ValueError, match="int8 at both ends"):
        quant.step_deploy(load_yaml(ctc.CONFIG), tmp_path, "row")


def test_the_ladder_decides_an_int8_window_as_the_record_the_chip_is_held_to(tmp_path: Path) -> None:
    cfg = load_yaml(ctc.CONFIG)
    cfg["quant"]["hops"] = 64
    torch.manual_seed(0)
    model = probe.draw_norm_scales(encoder.build(cfg), *cfg["probe"]["norm_scale"]).eval()
    rng = np.random.default_rng(8)
    calib = [torch.from_numpy(rng.normal(size=(1, DIMS, 64)).astype(np.float32)) for _ in range(2)]
    rungs = ptq_espdl.ladder(quant.LADDER) | {"equalization": None, "bias_correction": False, "calibration": "minmax"}
    graph = quant.quantized(model, calib, tmp_path, rungs, cfg["esp_ppq_patches"])
    norm = (np.zeros(DIMS, np.float32), np.ones(DIMS, np.float32))
    x = rng.normal(size=(50, DIMS)).astype(np.float32)
    record = probe.command_windows(cfg, graph, model, norm, [x])
    _, score, lead, gap = ctc_score.DECISION_RECORD.unpack(record[-ctc_score.DECISION_RECORD.size :])
    lexicon = ctc_score.default_lexicon()
    int8 = quant.Int8Net(graph, 64, *norm, model, cfg["esp_ppq_patches"])
    heard = quant.chip_heard(gate.Ctc(int8, *norm, [f"c{k}" for k in range(len(lexicon))], lexicon, cfg), x)
    assert (heard.score, heard.lead, heard.gap) == (score, lead, gap)


def test_the_operating_point_keeps_val_within_its_false_accepts_and_lifts_the_worst_command() -> None:
    a = [gate.Heard("a", 900, 80, 100), gate.Heard("a", 900, 30, 100), gate.Heard("a", 900, 80, 400)]
    b = [gate.Heard("b", 900, 60, 200), gate.Heard("a", 900, 60, 200)]
    c = [gate.Heard("c", 900, 10, 900)]
    val = [gate.Heard("a", 900, 40, 150), gate.Heard("b", 900, 90, 50), gate.Heard("a", 900, 90, 50)]
    meant = [False, False, True]
    spec = {"false_accept": 0.34, "margin_sweep": [25, 50], "min_windows": 2}
    got = quant.operating_point({"a": a, "b": b, "c": c}, val, meant, spec, [200, 500])
    assert (got["reject_permille"], got["margin_permille"], got["within_target"]) == (500, 50, True)
    assert got["chosen"] == {
        "reject_permille": 500,
        "margin_permille": 50,
        "worst": 0.5,
        "overall": 0.5,
        "false_accepts": 1,
        "commands": {"a": "2/3", "b": "1/2", "c": "0/1"},
    }
    assert len(got["table"]) == 4
    strict = quant.operating_point({"a": a, "b": b, "c": c}, val, meant, spec | {"false_accept": 0.0}, [200, 500])
    assert not strict["within_target"] and strict["chosen"]["false_accepts"] == 1


def test_a_row_without_its_chosen_thresholds_is_not_deployed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "load_ctc", lambda _run: SimpleNamespace(cfg={"listen_hash": listen.HASH}))
    monkeypatch.setattr(quant.export_espdl, "load_native", lambda _path: "graph")
    monkeypatch.setattr(quant.ptq_espdl, "io_bits", lambda _graph: (8, 8))
    quant.recorded(tmp_path, {}, {"row": gate_of(80, 0.3) | {"calibration": "kl", "int16_ops": []}})
    with pytest.raises(ValueError, match="no delta1, delta2 chosen"):
        quant.step_deploy(load_yaml(ctc.CONFIG), tmp_path, "row")


def test_a_pitch_dim_folded_into_the_front_reads_as_held_at_its_train_mean() -> None:
    cfg = load_yaml(ctc.CONFIG)
    torch.manual_seed(0)
    model = probe.draw_norm_scales(encoder.build(cfg), *cfg["probe"]["norm_scale"]).eval()
    net = gate.Ctc(model, np.zeros(DIMS, np.float32), np.ones(DIMS, np.float32), [], [], cfg)
    x = torch.randn(1, DIMS, 64)
    held = x.clone()
    held[:, DIMS - 3] = 0.0
    folded = quant.folded(net, (0,))
    with torch.no_grad():
        assert torch.allclose(folded.model(x), model(held), atol=1e-5)
        assert not torch.allclose(model(x), model(held), atol=1e-5)
    assert bool(torch.all(model.front.proj.weight[:, -3] != 0))


def test_a_ladder_heard_on_kaldi_with_a_dim_held_keeps_its_own_folder(tmp_path: Path) -> None:
    kaldi = quant.Hearing(kaldi=True, hold="voicing")
    assert quant.LEARNT.folder(tmp_path) == tmp_path / "int8"
    assert kaldi.folder(tmp_path) == tmp_path / "int8_kaldi_voicing" and kaldi.held() == gate.HOLDS["voicing"]
    quant.recorded(tmp_path, {}, {"a": gate_of(70, 0.3)}, kaldi)
    assert quant.ladder_file(tmp_path, kaldi).is_file() and not quant.ladder_file(tmp_path).exists()


def test_kaldi_hears_a_run_on_its_mel_from_build_only_if_that_is_the_contracts_front(monkeypatch) -> None:
    learnt = contract_front() | {"pitch": {"source": "swiftf0"}}
    cfg = {"simulate": {"mel_from": "v7"}, "split": {"version": "v8"}, "listen": learnt, "listen_hash": 0}
    monkeypatch.setattr(quant.data, "unbuilt", lambda _cfg, _paths: [])
    heard = quant.kaldi_cfg(cfg, {})
    assert heard["split"]["version"] == "v7" and heard["listen"]["pitch"] == contract_front()["pitch"]
    assert heard["listen_hash"] == listen.HASH and cfg["split"]["version"] == "v8"
    monkeypatch.setattr(quant.data, "unbuilt", lambda _cfg, _paths: [Path("v7/test")])
    with pytest.raises(ValueError, match="not the contract's front"):
        quant.kaldi_cfg(cfg, {})
    with pytest.raises(ValueError, match="no build holds it"):
        quant.kaldi_cfg(cfg | {"simulate": {}}, {})


def test_a_deploy_record_names_the_hearing_its_row_was_built_on() -> None:
    kaldi = quant.Hearing(kaldi=True, hold="voicing")
    assert quant.hearing_of(quant.heard_head(kaldi)) == kaldi
    assert quant.hearing_of({}) == quant.LEARNT == quant.hearing_of(quant.heard_head(quant.LEARNT))
