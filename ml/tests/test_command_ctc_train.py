"""ctc training: sentences load with their units and nothing too long, batches pad to the net's chunk, the best path
merges repeats and drops blanks, the RNN-T loss sums every alignment whatever chunk of sentences builds its lattice,
greedy RNN-T paths of a batch are those of each sentence, and a tiny run evaluates, saves each evaluated net and keeps
the last."""

from __future__ import annotations

import itertools
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
from srpipe.tasks.command.ctc.postproc.ctc_score import BLANK  # noqa: E402
from srpipe.tasks.command.rnnt.model import transducer  # noqa: E402

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
    assert all(row["rnnt_loss"] > 0 and row["rnnt_unit_error_rate"] >= 0 for row in history)
    assert yaml.safe_load(yaml.safe_dump(history)) == history
    assert type(train.edit_distance([3, 3, 5], np.array([3, 5], dtype=np.uint8))) is int
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


def every_alignment(log_probs: np.ndarray, units: list[int]) -> float:
    """-log P(units), units as classes, by summing in float64 every path through the (frames, units + 1) lattice: a
    unit moves up at the same frame, a blank moves to the next frame, and the path ends on a blank from the last
    frame."""
    frames, total = log_probs.shape[0], []
    for ups in itertools.combinations(range(frames + len(units) - 1), len(units)):
        t = u = 0
        path = 0.0
        for move in range(frames + len(units)):
            if move in ups:
                path += log_probs[t, u, units[u]]
                u += 1
            else:
                path += log_probs[t, u, BLANK]
                t += 1
        total.append(path)
    return -float(np.logaddexp.reduce(total))


@pytest.fixture
def small():
    """A transducer on a narrow encoder, three sentences of encoded frames and their units as train.Sentences holds
    them, classes past the blank, the last class among them."""
    torch.manual_seed(4)
    t = transducer.Transducer(8, encoder.n_classes(), 16, 2).double()
    encoded = torch.randn(3, 8, 5, dtype=torch.float64)
    return t, encoded, np.array([5, 3, 4]), [np.array([5, 1]), np.array([encoder.n_classes() - 1]), np.array([2, 2, 8])]


def test_lattice_groups_hold_every_sentence_once_each_group_within_its_cells() -> None:
    rng = np.random.default_rng(5)
    n_frames, n_units = rng.integers(1, 400, 64), rng.integers(1, 220, 64)
    for cells in (1, 20_000, 100_000, 10**9):
        groups = transducer.lattice_groups(n_frames, n_units, cells)
        assert sorted(np.concatenate(groups).tolist()) == list(range(64))
        for g in groups:
            assert len(g) == 1 or len(g) * n_frames[g].max() * (n_units[g].max() + 1) <= cells
    assert len(transducer.lattice_groups(n_frames, n_units, 10**9)) == 1
    assert len(transducer.lattice_groups(n_frames, n_units, 1)) == 64


def test_the_rnnt_loss_sums_every_alignment_whatever_groups_build_the_lattice(small) -> None:
    t, encoded, n_frames, units = small
    want = []
    with torch.no_grad():
        frames = t.joiner.frame_proj(encoded.transpose(1, 2))
        prefixes, _ = transducer.with_blank_first(units, encoded.device)
        projected = t.joiner.prefix_proj(t.predictor(prefixes))
        lattice = t.joiner(frames[:, :, None], projected[:, None]).log_softmax(-1).numpy()
    for row, (n, u) in enumerate(zip(n_frames, units, strict=True)):
        want.append(every_alignment(lattice[row, :n, : len(u) + 1], u.tolist()) / len(u))
    grads = []
    for cells in (1, 40, 100):
        t.zero_grad()
        loss = transducer.rnnt_loss_of(t.float(), encoded.float(), n_frames, units, cells)
        loss.backward()
        assert abs(loss.item() - np.mean(want)) < 1e-4
        grads.append(torch.cat([p.grad.flatten() for p in t.parameters()]))
        t.double()
    assert all(torch.allclose(g, grads[0], atol=1e-6) for g in grads[1:])


def test_a_batch_of_greedy_paths_is_each_sentences_own(small) -> None:
    t, encoded, n_frames, _ = small
    t, encoded = t.float(), encoded.float()
    batch = transducer.greedy_paths(t, encoded, n_frames)
    alone = [transducer.greedy_paths(t, encoded[k : k + 1, :, :n], np.array([n]))[0] for k, n in enumerate(n_frames)]
    assert batch == alone and all(BLANK < u < encoder.n_classes() for path in batch for u in path)


N_MEL = DIMS - 3
STILL = {"tempo": [1.0, 1.0], "warp_hops": 0, "tilt_db": 0.0}


def ramp(hops: int) -> np.ndarray:
    return np.tile(np.arange(hops, dtype=np.float32)[:, None], (1, DIMS))


def test_ctc_needs_a_frame_a_unit_and_a_blank_between_equal_neighbours() -> None:
    assert train.need_frames(np.array([3, 4, 4, 5, 5, 5])) == 6 + 3


def test_unchanged_draws_give_the_sentence_back() -> None:
    x = np.random.default_rng(0).standard_normal((50, DIMS)).astype(np.float32)
    y = train.augmented(x, np.array([1, 2]), STILL, N_MEL, 2, np.random.default_rng(1))
    np.testing.assert_allclose(y, x, atol=1e-6)


def test_a_faster_tempo_shortens_the_sentence_and_scales_delta_pitch() -> None:
    spec = STILL | {"tempo": [2.0, 2.0]}
    y = train.augmented(ramp(101), np.array([1]), spec, N_MEL, 2, np.random.default_rng(0))
    assert len(y) == 50
    np.testing.assert_allclose(y[:, 0], np.linspace(0.0, 100.0, 50), atol=1e-4)
    np.testing.assert_allclose(y[:, N_MEL + train.DELTA_PITCH], np.linspace(0.0, 100.0, 50) * 100 / 49, rtol=1e-5)


def test_tempo_never_leaves_ctc_fewer_frames_than_the_units_need() -> None:
    units = np.arange(1, 31)
    spec = STILL | {"tempo": [1.6, 1.6]}
    y = train.augmented(ramp(70), units, spec, N_MEL, 2, np.random.default_rng(0))
    assert len(y) == 60 and -(-len(y) // 2) >= train.need_frames(units)


def test_the_warp_keeps_both_ends_and_runs_forward() -> None:
    spec = STILL | {"warp_hops": 8}
    for seed in range(20):
        y = train.augmented(ramp(80), np.array([1]), spec, N_MEL, 2, np.random.default_rng(seed))
        assert len(y) == 80 and y[0, 0] == 0.0 and y[-1, 0] == 79.0
        assert np.all(np.diff(y[:, 0]) > 0.0)


def test_the_tilt_slopes_the_mel_bands_only() -> None:
    x = np.zeros((40, DIMS), dtype=np.float32)
    y = train.augmented(x, np.array([1]), STILL | {"tilt_db": 3.0}, N_MEL, 2, np.random.default_rng(4))
    slope = y[0, :N_MEL]
    assert abs(slope.mean()) < 1e-6 and abs(slope[-1]) <= 3.0 * train.LOG_PER_DB + 1e-6
    np.testing.assert_allclose(np.diff(slope), np.diff(slope)[0], atol=1e-6)
    assert np.all(y[:, N_MEL:] == 0.0) and np.all(y == y[0])


def test_an_augmented_batch_pads_to_its_longest_sentence() -> None:
    data = train.Sentences(ramp(150), np.array([0, 60]), np.array([60, 90]), [np.array([2, 3]), np.array([4])])
    spec = STILL | {"tempo": [2.0, 2.0]}
    x, hops, units = train.augmented_batch(data, np.array([0, 1]), 16, spec, N_MEL, 2, np.random.default_rng(0))
    assert hops.tolist() == [30, 45] and x.shape == (2, 48, DIMS)
    assert np.all(x[0, 30:] == 0.0) and [u.tolist() for u in units] == [[2, 3], [4]]
