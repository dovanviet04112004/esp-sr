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
    """-log P(units) by summing, in float64, every path through the (frames, units + 1) lattice: a unit moves up at
    the same frame, a blank moves to the next frame, and the path ends on a blank from the last frame."""
    frames, total = log_probs.shape[0], []
    for ups in itertools.combinations(range(frames + len(units) - 1), len(units)):
        t = u = 0
        path = 0.0
        for move in range(frames + len(units)):
            if move in ups:
                path += log_probs[t, u, units[u] + 1]
                u += 1
            else:
                path += log_probs[t, u, BLANK]
                t += 1
        total.append(path)
    return -float(np.logaddexp.reduce(total))


@pytest.fixture
def small():
    """A transducer on a narrow encoder, three sentences of encoded frames and their units."""
    torch.manual_seed(4)
    t = transducer.Transducer(8, encoder.n_classes(), 16, 2).double()
    encoded = torch.randn(3, 8, 5, dtype=torch.float64)
    return t, encoded, np.array([5, 3, 4]), [np.array([4, 0]), np.array([2]), np.array([1, 1, 7])]


def test_the_rnnt_loss_sums_every_alignment_whatever_chunk_builds_the_lattice(small) -> None:
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
    for chunk in (1, 2, 3):
        t.zero_grad()
        loss = transducer.rnnt_loss_of(t.float(), encoded.float(), n_frames, units, chunk)
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
    assert batch == alone and all(0 <= u < encoder.n_classes() - 1 for path in batch for u in path)
