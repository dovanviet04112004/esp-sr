"""ctc training: sentences load with their units and nothing too long, batches pad to the net's chunk, the best path
merges repeats and drops blanks, the RNN-T loss sums every alignment whatever chunk of sentences builds its lattice,
greedy RNN-T paths of a batch are those of each sentence, a ring smaller than train goes round every shard and is the
same rebuilt for a resume, a tiny run evaluates, saves each evaluated net, keeps the last, and paused or stopped then
resumed ends as an unbroken one, and each layer's stream is kept after every block and costs only above its cap."""

from __future__ import annotations

import itertools
import json
import signal
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


def sharded(folder: Path, shards: list[list[int]], rng: np.random.Generator) -> Path:
    """A finished build with pitch in float16, a shard a list of sentence hops, sentences s0, s1, ... throughout."""
    folder.mkdir(parents=True)
    listed, k = {}, 0
    for j, hops in enumerate(shards):
        stem, rows, offset = f"shard_{j:05d}", [], 0
        for n in hops:
            rows.append({"item": f"s{k}", "frame_offset": offset, "n_frames": n, "speech_frames": [0, n]})
            offset, k = offset + n, k + 1
        np.save(folder / f"{stem}.features.npy", rng.normal(size=(offset, DIMS - 3)).astype(np.float16))
        np.save(folder / f"{stem}.pitch.npy", rng.normal(size=(offset, 3)).astype(np.float16))
        (folder / f"{stem}.items.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        listed |= {f"{stem}{s}": "-" for s in (".features.npy", ".pitch.npy", ".items.jsonl")}
    (folder / "manifest.yaml").write_text(yaml.safe_dump({"pitch": True, "sha256": listed}))
    return folder


def pool_of(folders: list[Path], units: dict, capacity: int = 1 << 20, rotate_steps: int = 1) -> train.Pool:
    return train.Pool(train.shards_of(folders, units, 64), DIMS, capacity, rotate_steps, 5)


def stored(folder: Path, k: int) -> np.ndarray:
    """Sentence s<k>'s features as its build stores them."""
    for listing in sorted(folder.glob("*.items.jsonl")):
        for line in listing.read_text().splitlines():
            item = json.loads(line)
            if item["item"] == f"s{k}":
                stem = str(listing).removesuffix(".items.jsonl")
                mel, pitch = (np.load(stem + s) for s in (".features.npy", ".pitch.npy"))
                span = slice(item["frame_offset"], item["frame_offset"] + item["n_frames"])
                return np.concatenate([mel[span], pitch[span]], axis=1)
    raise KeyError(k)


def test_a_pool_within_its_capacity_holds_train_whole_as_load_role_does(tmp_path: Path) -> None:
    folder = sharded(tmp_path / "t", [[40, 90, 30], [20, 50]], np.random.default_rng(6))
    units = {"s0": [0, 5], "s1": [3], "s3": [7], "s4": [2, 2]}
    whole, pool = train.load_role([folder], units, 64, "float16"), pool_of([folder], units)
    data = pool.at(1)
    assert pool.whole and data.first.tolist() == whole.first.tolist() and data.hops.tolist() == whole.hops.tolist()
    assert [data.units[k].tolist() for k in range(len(data.units))] == [u.tolist() for u in whole.units]
    np.testing.assert_array_equal(data.features, whole.features)
    assert pool.at(57) is data and pool.sentences == 3 and len(pool.ring) == 230
    mean, std = pool.stats()
    np.testing.assert_allclose(mean, whole.features.astype(np.float64).mean(axis=0), rtol=1e-5, atol=1e-6)
    np.testing.assert_allclose(std, whole.features.astype(np.float64).std(axis=0), rtol=1e-4)


def test_a_ring_smaller_than_train_goes_round_every_shard_and_is_the_same_rebuilt(tmp_path: Path) -> None:
    rng = np.random.default_rng(7)
    folder = sharded(tmp_path / "t", [[int(n) for n in rng.integers(10, 40, 3)] for _ in range(7)], rng)
    units = {f"s{k}": [k] for k in range(21)}
    pool, seen = pool_of([folder], units, capacity=150), set()
    for step in range(1, 61):
        data = pool.at(step)
        assert sum(pool.shards[s].n_hops for s, _ in pool.placed) <= 150 == len(pool.ring)
        seen |= {s for s, _ in pool.placed}
        if step in (1, 17, 60):
            again = pool_of([folder], units, capacity=150).at(step)
            assert again.first.tolist() == data.first.tolist() and again.hops.tolist() == data.hops.tolist()
            for k, (a, n) in enumerate(zip(data.first, data.hops, strict=True)):
                np.testing.assert_array_equal(data.features[a : a + n], stored(folder, int(data.units[k][0]) - 1))
                np.testing.assert_array_equal(again.features[a : a + n], data.features[a : a + n])
    assert seen == set(range(7)) and not pool.whole and pool.passes >= 1


def test_sentences_keep_their_units_past_the_blank_and_leave_out_the_unread_and_the_long(tmp_path: Path) -> None:
    folder = processed(tmp_path / "a", [40, 90, 30], np.random.default_rng(0))
    data = train.load_role([folder], {"s0": [0, 5], "s1": [3], "s2": []}, 64, "float16")
    assert data.first.tolist() == [0] and data.hops.tolist() == [40] and data.units[0].tolist() == [1, 6]
    assert data.features.dtype == np.float16 and data.features.shape == (160, DIMS)
    with pytest.raises(ValueError, match="no sentence"):
        train.load_role([folder], {}, 64, "float16")


def test_a_window_holding_clips_says_their_units_in_order_and_needs_each(tmp_path: Path) -> None:
    folder = processed(tmp_path / "a", [40, 30, 20], np.random.default_rng(2))
    listing = folder / "shard_00000.items.jsonl"
    rows = [json.loads(line) for line in listing.read_text().splitlines()]
    rows[0]["clips"], rows[1]["clips"] = ["s0", "s9"], ["s1", "s2"]
    listing.write_text("".join(json.dumps(r) + "\n" for r in rows))
    units = {"s0": [0], "s1": [3], "s2": [4, 5]}
    data = train.load_role([folder], units, 64, "float32")
    assert data.first.tolist() == [40, 70] and [u.tolist() for u in data.units] == [[4, 5, 6], [5, 6]]
    shard = train.shards_of([folder], units, 64)[0]
    assert shard.first.tolist() == [40, 70] and shard.units.tolist() == [4, 5, 6, 5, 6]


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
        "train": pool_of([processed(tmp_path / "t", [48, 40, 56, 32, 48, 40], rng)], units),
        "val": train.load_role([processed(tmp_path / "v", [40, 48], rng)], units, 64, "float32"),
    }
    cfg = load_yaml(ctc.CONFIG)
    cfg["train"] |= {"batch": 2, "steps": 4, "eval_every": 2}
    net, (mean, std), history = train.train(cfg, sets, "cpu", tmp_path / "run")
    assert [row["step"] for row in history] == [2, 4] and len(mean) == len(std) == DIMS
    saved = np.load(tmp_path / "run" / "feature_stats.npz")
    np.testing.assert_array_equal(saved["mean"], mean)
    np.testing.assert_array_equal(saved["std"], std)
    assert all(row["loss"] > 0 and row["unit_error_rate"] >= 0 for row in history)
    assert all(row["rnnt_loss"] > 0 and row["rnnt_unit_error_rate"] >= 0 for row in history)
    layers = sum(s["layers"] for s in cfg["model"]["stacks"])
    assert all(row["stream_rms_max"] > 0 and 0 <= row["stream_layer"] < layers for row in history)
    assert all(row["stream_penalty"] >= 0 for row in history)
    assert yaml.safe_load(yaml.safe_dump(history)) == history
    assert type(train.edit_distance([3, 3, 5], np.array([3, 5], dtype=np.uint8))) is int
    last = torch.load(train.checkpoint(tmp_path / "run", 4))
    assert all(torch.equal(last[k], v) for k, v in net.state_dict().items())


def ringed_sets(tmp_path: Path, seed: int) -> dict:
    """Train in three shards through a ring of about two that turns every step, and val."""
    rng = np.random.default_rng(seed)
    units = {f"s{k}": [k % 5, (k + 1) % 5] for k in range(8)}
    folder = sharded(tmp_path / "t", [[48, 40, 56], [32, 48], [40, 44, 36]], rng)
    return {
        "train": pool_of([folder], units, capacity=230),
        "val": train.load_role([processed(tmp_path / "v", [40, 48], rng)], units, 64, "float32"),
    }


def test_a_stopped_run_resumed_from_its_last_checkpoint_ends_as_an_unbroken_one(tmp_path: Path, monkeypatch) -> None:
    sets = ringed_sets(tmp_path, 3)
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
        train.train(cfg, ringed_sets(tmp_path / "b", 3), "cpu", tmp_path / "stopped")
    monkeypatch.setattr(train, "evaluate", evaluate)
    assert torch.load(train.checkpoint(tmp_path / "stopped"), weights_only=False)["step"] == 2
    resumed, _, history = train.train(cfg, ringed_sets(tmp_path / "c", 3), "cpu", tmp_path / "stopped", resume=True)
    assert history == unbroken
    assert all(torch.equal(resumed.state_dict()[k], v) for k, v in whole.state_dict().items())


def test_ctrl_c_pauses_after_the_step_under_way_and_the_resumed_run_ends_as_an_unbroken_one(
    tmp_path: Path, monkeypatch
) -> None:
    cfg = load_yaml(ctc.CONFIG)
    cfg["train"] |= {"batch": 2, "steps": 5, "eval_every": 2}
    whole, _, unbroken = train.train(cfg, ringed_sets(tmp_path / "a", 4), "cpu", tmp_path / "whole")
    mask, calls = train.mask, []

    def interrupted_in_the_third(*args):
        calls.append(1)
        if len(calls) == 3:
            signal.raise_signal(signal.SIGINT)
        return mask(*args)

    monkeypatch.setattr(train, "mask", interrupted_in_the_third)
    with pytest.raises(KeyboardInterrupt):
        train.train(cfg, ringed_sets(tmp_path / "b", 4), "cpu", tmp_path / "paused")
    monkeypatch.setattr(train, "mask", mask)
    state = torch.load(train.checkpoint(tmp_path / "paused"), weights_only=False)
    assert (
        state["step"] == 3
        and len(state["losses"]) == 1
        and signal.getsignal(signal.SIGINT) is signal.default_int_handler
    )
    resumed, _, history = train.train(cfg, ringed_sets(tmp_path / "c", 4), "cpu", tmp_path / "paused", resume=True)
    assert history == unbroken
    assert all(torch.equal(resumed.state_dict()[k], v) for k, v in whole.state_dict().items())


def test_streams_kept_are_each_layers_stream_after_every_block_and_stop_on_leaving() -> None:
    net = encoder.build(load_yaml(ctc.CONFIG)).eval()
    layers = [layer for stack in net.stacks for layer in stack.layers]
    given, normed = [], []
    hooks = [layer.register_forward_pre_hook(lambda _m, args: given.append(args[0])) for layer in layers]
    hooks += [layer.norm.register_forward_pre_hook(lambda _m, args: normed.append(args[0])) for layer in layers]
    x = torch.from_numpy(np.random.default_rng(8).normal(size=(2, DIMS, 64)).astype(np.float32))
    with torch.no_grad():
        with encoder.streams_kept(net) as kept:
            out = net(x)
        for hook in hooks:
            hook.remove()
        assert [len(points) for points in kept] == [6] * len(layers)
        for layer, y, last, points in zip(layers, given, normed, kept, strict=True):
            blocks = (layer.ff1, layer.mixer, layer.conv1, layer.ff2, layer.conv2, layer.ff3)
            for block, ms in zip(blocks, points, strict=True):
                y = y + block(y)
                torch.testing.assert_close(ms, encoder.frame_mean_square(y))
            torch.testing.assert_close(points[-1], encoder.frame_mean_square(last))
        torch.testing.assert_close(net(x), out, rtol=0, atol=0)
    assert [len(points) for points in kept] == [6] * len(layers)


def test_the_stream_penalty_is_nothing_under_the_cap_and_the_squared_octaves_over_it() -> None:
    spec, every = {"cap_rms": 32.0, "weight": 0.5}, torch.ones((1, 5), dtype=torch.bool)
    rms = torch.tensor([[64.0, 128.0, 16.0, 32.0, 0.0]], requires_grad=True)
    penalty = train.stream_penalty([[rms.pow(2)], [torch.full((1, 5), 256.0**2)]], [every, every], spec)
    assert penalty.item() == pytest.approx(0.5 * ((1 + 4) / 5 + 9))
    penalty.backward()
    grad = rms.grad[0].tolist()
    assert grad[0] > 0 and grad[1] > 0 and grad[2:] == [0.0, 0.0, 0.0]
    still = torch.full((2, 3), 32.0**2)
    assert float(train.stream_penalty([[still]], [torch.ones((2, 3), dtype=torch.bool)], spec)) == 0.0


def test_the_batch_padding_is_no_frame_of_the_stream_penalty() -> None:
    kept = [[torch.zeros((2, 6))], [torch.zeros((2, 3))]]
    real = train.stream_frames(kept, np.array([5, 2]), [1, 2])
    assert real[0].tolist() == [[True] * 5 + [False], [True] * 2 + [False] * 4]
    assert real[1].tolist() == [[True, True, True], [True, False, False]]
    rms = torch.tensor([[64.0, 128.0, 1e6], [1e6, 1e6, 1e6]], requires_grad=True)
    mine = torch.tensor([[True, True, False], [False] * 3])
    penalty = train.stream_penalty([[rms.pow(2)]], [mine], {"cap_rms": 32.0, "weight": 1.0})
    assert penalty.item() == pytest.approx((1 + 4) / 2)
    penalty.backward()
    assert rms.grad[0, 2] == 0.0 and not rms.grad[1].any()


def test_the_loudest_stream_of_val_is_that_of_its_sentences_alone_never_the_padding() -> None:
    net = encoder.build(load_yaml(ctc.CONFIG)).eval()
    flat = np.full((80, DIMS), 50.0, dtype=np.float32)
    data = train.Sentences(flat, np.array([0, 16]), np.array([16, 64]), [np.array([2, 3]), np.array([4])])
    stats = (np.full(DIMS, 50.0, dtype=np.float32), np.ones(DIMS, dtype=np.float32))
    alone = [
        train.evaluate(
            net,
            train.Sentences(flat, data.first[k : k + 1], data.hops[k : k + 1], [data.units[k]]),
            stats,
            "cpu",
            100_000,
        )
        for k in range(2)
    ]
    loudest = train.evaluate(net, data, stats, "cpu", 100_000)["stream_rms_max"]
    assert loudest == pytest.approx(max(row["stream_rms_max"] for row in alone), rel=1e-5)
    x, _, _ = train.batch_of(data, np.arange(2), net.chunk_multiple)
    with torch.no_grad(), encoder.streams_kept(net) as kept:
        net(torch.from_numpy((x - stats[0]) / stats[1]).transpose(1, 2))
    assert max(float(ms.max()) for layer in kept for ms in layer) ** 0.5 > 2 * loudest


def test_a_cap_over_every_stream_leaves_the_run_as_none_and_one_under_them_costs_and_steers_it(tmp_path: Path) -> None:
    runs, caps = {}, {"none": None, "over": 1e6, "under": 1e-3}
    for name, cap in caps.items():
        cfg = load_yaml(ctc.CONFIG)
        cfg["train"] |= {"batch": 2, "steps": 2, "eval_every": 2}
        cfg["train"].pop("stream")
        if cap is not None:
            cfg["train"]["stream"] = {"cap_rms": cap, "weight": 1.0}
        runs[name] = train.train(cfg, ringed_sets(tmp_path / name, 3), "cpu")
    weights = {name: net.state_dict() for name, (net, _, _) in runs.items()}
    assert all(torch.equal(weights["over"][k], v) for k, v in weights["none"].items())
    assert not all(torch.equal(weights["under"][k], v) for k, v in weights["none"].items())
    assert runs["none"][2][-1]["stream_penalty"] == runs["over"][2][-1]["stream_penalty"] == 0.0
    assert runs["under"][2][-1]["stream_penalty"] > 0


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
    nearest = np.rint(np.linspace(0.0, 100.0, 50))
    assert len(y) == 50
    np.testing.assert_array_equal(y[:, 0], nearest)
    np.testing.assert_allclose(y[:, N_MEL + train.DELTA_PITCH], nearest * 100 / 49, rtol=1e-5)


def test_tempo_never_leaves_ctc_fewer_frames_than_the_units_need() -> None:
    units = np.arange(1, 31)
    spec = STILL | {"tempo": [1.6, 1.6]}
    y = train.augmented(ramp(70), units, spec, N_MEL, 2, np.random.default_rng(0))
    assert len(y) == 60 and -(-len(y) // 2) >= train.need_frames(units)


def test_the_warp_keeps_both_ends_and_never_runs_back() -> None:
    spec = STILL | {"warp_hops": 8}
    for seed in range(20):
        y = train.augmented(ramp(80), np.array([1]), spec, N_MEL, 2, np.random.default_rng(seed))
        assert len(y) == 80 and y[0, 0] == 0.0 and y[-1, 0] == 79.0
        assert np.all(np.diff(y[:, 0]) >= 0.0)


def test_tempo_and_warp_give_whole_hops_of_the_sentence_never_a_blend_of_two() -> None:
    x = np.random.default_rng(2).standard_normal((120, DIMS)).astype(np.float32)
    spec = {"tempo": [0.8, 1.6], "warp_hops": 8, "tilt_db": 0.0}
    for seed in range(20):
        y = train.augmented(x, np.array([1]), spec, N_MEL, 2, np.random.default_rng(seed))
        matches = [np.flatnonzero((x[:, :N_MEL] == row[:N_MEL]).all(axis=1)) for row in y]
        assert all(len(m) == 1 for m in matches)
        source = [int(m[0]) for m in matches]
        assert np.all(np.diff(source) >= 0) and source[0] == 0 and source[-1] == len(x) - 1


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
