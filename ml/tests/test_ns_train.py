"""srpipe.tasks.ns.train: the loss's optimum, a batch that is a pure function of its step, a tiny run of every
candidate on the CPU that keeps its last weights and that eval scores, and runs stopped at an epoch's end, paused part
way through one, or killed between saves, each of which resumed ends where an unbroken one ends."""

from __future__ import annotations

import copy
import math
import os
import signal
from pathlib import Path

import numpy as np
import pytest
import yaml

from srpipe.core.audio_io import write_wav
from srpipe.core.config import CONFIGS, load_device, load_yaml
from srpipe.generated import grid
from srpipe.scenes import device
from srpipe.tasks import ns
from srpipe.tasks.ns import data

torch = pytest.importorskip("torch")

from srpipe.tasks.ns import eval as ns_eval  # noqa: E402
from srpipe.tasks.ns import gpu_mix, model, train  # noqa: E402

FS = grid.SAMPLE_RATE_HZ
UTTERANCES = 12


def tiny_config() -> dict:
    cfg = copy.deepcopy(load_yaml(ns.CONFIG))
    cfg["mix"] |= {"example_s": 1.024, "rooms": {role: [0, 2] for role in data.ROLES}}
    cfg["mix"]["long_pause"]["probability"] = 0.0
    cfg["noise"]["pools"] = [
        {"name": "fan", "class": "stationary", "weight": 1.0, "prefix": "noise/fan/", "group": "file", "roles": {}}
    ]
    cfg["noise"]["classes"] = {"stationary": {"share": 1.0, "snr_db": [0.0, 10.0]}}
    cfg["tone"]["pools"] = [{"name": "hum", "weight": 1.0, "prefix": "noise/hum/", "group": "file", "roles": {}}]
    cfg["pool"]["workers"] = 1
    cfg["sets"]["examples_per_shard"] = 8
    cfg["eval"]["settle_s"] = 0.256
    tiny = {"epochs": 2, "batch": 2, "workers": 0, "val_workers": 0, "norm_batches": 2, "log_every": 1000}
    cfg["train"] |= tiny | {"candidates": model.names(cfg)}
    return cfg


def device_config() -> dict:
    dev = copy.deepcopy(load_device(CONFIGS / "scenes" / "device.yaml"))
    dev["name"] = "ns_train_test"
    dev["rooms"]["count"] = 2
    dev["rooms"]["rt60_s"] = [0.2, 0.3]
    return dev


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict, dict, dict]:
    """Every role's synthetic pools (voiced bursts as speech, a fan as noise, quiet hiss as tone and babble), a
    two-room bank, and the val and test sets they mix."""
    root = tmp_path_factory.mktemp("ns_train")
    paths = {"raw": root / "raw", "interim": root / "interim", "processed": root / "processed", "cache": root}
    cfg, dev = tiny_config(), device_config()
    rng = np.random.default_rng(1)
    write_wav(paths["raw"] / "noise" / "fan" / "a.wav", 0.05 * rng.standard_normal(4 * FS))
    below = dev["talker"]["active_below_peak_db"]
    for role in data.ROLES:
        folder = data.pool_dir(paths, cfg, role)
        folder.mkdir(parents=True)
        pieces = []
        for k in range(UTTERANCES):
            n = int(rng.integers(FS // 2, 3 * FS // 2))
            pieces.append(0.1 * np.sin(2 * np.pi * (150 + 10 * k) * np.arange(n) / FS) * np.hanning(n))
        data.write_run(folder, "speech_0000", pieces, [0] * UTTERANCES)
        names = "".join(f"speech/vivos/{role}/u{k:02d}.wav\n" for k in range(UTTERANCES))
        (folder / "speech_items.txt").write_text(names, encoding="utf-8")
        index = np.load(folder / "speech_0000_index.npz")
        np.savez(
            folder / "speech_index.npz",
            run=np.zeros(UTTERANCES, dtype=np.int32),
            offset=index["offset"],
            length=index["length"],
            active_rms=np.array([device.active_rms(p, below) for p in pieces], dtype=np.float32),
        )
        row = {"item": "noise/fan/a.wav", "pool": "fan", "group": "a", "seconds": 4}
        data.write_tsv(folder / "noise_index.tsv", data.NOISE_FIELDS, [row])
        data.write_run(folder, "tone", [0.01 * rng.standard_normal(6 * FS)], [0])
        data.write_run(folder, "babble", [0.01 * rng.standard_normal(6 * FS)], [0])
        (folder / "manifest.yaml").write_text("{}\n", encoding="utf-8")
    data.sets(cfg, dev, paths)
    return cfg, dev, paths


def test_the_loss_is_least_at_the_magnitude_ratio_of_speech_to_mixture() -> None:
    spec = load_yaml(ns.CONFIG)["loss"]
    speech = torch.tensor([1.0, 1.0, 1.0, 0.1, 0.0], dtype=torch.float64)
    mixture = speech + torch.tensor([1.0, 0.1, 10.0, 1.0, 1.0], dtype=torch.float64)
    best = torch.sqrt(speech / mixture)
    assert float(best[0]) == pytest.approx(0.5**0.5) and float(best[4]) == 0.0
    scan = torch.linspace(0.0, 1.0, 2001, dtype=torch.float64)
    for s, x, g in zip(speech, mixture, best, strict=True):
        losses = [float(train.gain_loss(v.view(1, 1, 1), s.view(1, 1, 1), x.view(1, 1, 1), spec, 1e-9)) for v in scan]
        assert abs(float(scan[int(np.argmin(losses))]) - float(g)) <= 1e-3
    whole = train.gain_loss(best.view(1, 1, -1), speech.view(1, 1, -1), mixture.view(1, 1, -1), spec, 1e-9)
    nudged = (best + 0.02 * torch.tensor([1.0, -1.0, 1.0, 1.0, 1.0], dtype=torch.float64)).clamp(0.0, 1.0)
    assert whole < train.gain_loss(nudged.view(1, 1, -1), speech.view(1, 1, -1), mixture.view(1, 1, -1), spec, 1e-9)


def test_residual_noise_keeps_pulling_the_gain_down_far_under_the_speech() -> None:
    spec = load_yaml(ns.CONFIG)["loss"]
    silent, noisy = torch.zeros(1, 1, 1, dtype=torch.float64), torch.ones(1, 1, 1, dtype=torch.float64)

    def pull(gain_db: float) -> float:
        logit = torch.logit(torch.tensor(10.0 ** (gain_db / 20.0), dtype=torch.float64)).requires_grad_()
        train.gain_loss(torch.sigmoid(logit).view(1, 1, 1), silent, noisy, spec, 1e-9).backward()
        return float(logit.grad)

    assert pull(-40.0) > 0.2 * pull(-20.0) > 0.0
    zero = torch.zeros(1, 2, 3, dtype=torch.float64, requires_grad=True)
    train.gain_loss(zero, torch.ones_like(zero), torch.ones_like(zero), spec, 1e-9).backward()
    assert torch.all(torch.isfinite(zero.grad))


def test_a_batch_is_a_pure_function_of_its_step(world: tuple[dict, dict, dict]) -> None:
    cfg, dev, paths = world
    mixer = data.Mixer(cfg, dev, paths, "train", cfg["mix"]["seed"])
    steps = train.plan(cfg, mixer)
    assert steps.firsts[0] == 0 and steps.firsts[1] < steps.steps and steps.ends_epoch(steps.firsts[1] - 1)
    render = gpu_mix.Render(mixer.mics, mixer.n, "cpu")
    a = render(train.TrainBatches(cfg, dev, paths, steps)[3])
    b = render(train.TrainBatches(cfg, dev, paths, steps)[3])
    assert a.keys() == b.keys() and all(torch.equal(a[k], b[k]) for k in a)
    hops = round(cfg["mix"]["example_s"] * FS) // grid.HOP_SAMPLES
    assert a["power"].shape == (2, hops, grid.N_BINS) and a["vad"].shape == (2, hops)
    assert not torch.equal(a["power"], render(train.TrainBatches(cfg, dev, paths, steps)[4])["power"])
    alone = copy.deepcopy(cfg)
    alone["train"]["read_threads"] = 1
    read_alone = train.TrainBatches(alone, dev, paths, steps)[3]
    threaded = train.TrainBatches(cfg, dev, paths, steps)[3]
    assert cfg["train"]["read_threads"] > 1 and read_alone.keys() == threaded.keys()
    assert all(read_alone[k].numpy().tobytes() == threaded[k].numpy().tobytes() for k in threaded)


def test_a_tiny_run_trains_every_candidate_on_the_same_batches_keeps_its_last_weights_and_is_scored(
    world: tuple[dict, dict, dict], tmp_path: Path
) -> None:
    cfg, dev, paths = world
    run = tmp_path / "run"
    run.mkdir()
    val = train.train(cfg, dev, paths, run, "cpu", resume=False)
    history = yaml.safe_load((run / "history.yaml").read_text(encoding="utf-8"))
    assert [row["epoch"] for row in history] == [0, 1] and set(val) == set(model.names(cfg))
    for name in model.names(cfg):
        kept = torch.load(run / name / "model.pt")
        assert all(torch.equal(kept[k], v) for k, v in torch.load(train.checkpoint(run, name, 1)).items())
        np.testing.assert_array_equal(kept["mean"].numpy(), np.load(run / name / "norm.npz")["mean"])
        torch.manual_seed(cfg["train"]["seed"])
        start = model.build(cfg, name)
        assert all(not torch.equal(kept[k], p) for k, p in start.named_parameters())
        assert math.isfinite(val[name]["loss"]) and math.isfinite(val[name]["noise_down_db"])
    summary = ns_eval.score(run, cfg, dev, paths, "val", 1)
    floors = [ns_eval.floor_name(f) for f in cfg["eval"]["floors_db"]]
    assert set(summary) == {ns_eval.OMLSA} | {f"{n}@{f}" for n in model.names(cfg) for f in floors}
    whole = summary[ns_eval.OMLSA]["settled"]["all"]["all"]
    listings = data.set_dir(paths, cfg, "val").glob("*.items.jsonl")
    assert whole["examples"] == sum(len(p.read_text(encoding="utf-8").splitlines()) for p in listings)
    assert whole["noise_down_db"] is not None and set(summary[ns_eval.OMLSA]["settled"]["corpus"]) == {"vivos"}
    assert (run / "eval" / "val.yaml").exists()


def test_a_resumed_run_ends_where_an_unbroken_one_ends(
    world: tuple[dict, dict, dict], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, dev, paths = world
    cfg = copy.deepcopy(cfg)
    cfg["train"]["candidates"] = [model.RNNOISE, model.names(cfg)[1]]
    unbroken, broken = tmp_path / "unbroken", tmp_path / "broken"
    unbroken.mkdir(), broken.mkdir()
    train.train(cfg, dev, paths, unbroken, "cpu", resume=False)
    scored = train.score
    calls: list[int] = []

    def stop_at_the_second_epoch(*args: object) -> dict:
        if calls:
            raise KeyboardInterrupt
        calls.append(1)
        return scored(*args)

    monkeypatch.setattr(train, "score", stop_at_the_second_epoch)
    with pytest.raises(KeyboardInterrupt):
        train.train(cfg, dev, paths, broken, "cpu", resume=False)
    monkeypatch.undo()
    assert len(yaml.safe_load((broken / "history.yaml").read_text(encoding="utf-8"))) == 1
    train.train(cfg, dev, paths, broken, "cpu", resume=True)
    for name in cfg["train"]["candidates"]:
        a, b = torch.load(unbroken / name / "model.pt"), torch.load(broken / name / "model.pt")
        assert all(torch.equal(a[k], b[k]) for k in a)


def two_candidates(world: tuple[dict, dict, dict], every: int | None = None) -> tuple[dict, dict, dict, train.Plan]:
    cfg, dev, paths = world
    cfg = copy.deepcopy(cfg)
    cfg["train"]["candidates"] = [model.RNNOISE, model.names(cfg)[1]]
    if every is not None:
        cfg["train"]["checkpoint_every"] = every
    return cfg, dev, paths, train.plan(cfg, data.Mixer(cfg, dev, paths, "train", cfg["mix"]["seed"]))


def same_weights(a: Path, b: Path, names: list[str]) -> bool:
    return all(
        all(torch.equal(x[k], y[k]) for k in x)
        for x, y in ((torch.load(a / n / "model.pt"), torch.load(b / n / "model.pt")) for n in names)
    )


def test_a_pause_part_way_through_an_epoch_goes_on_where_an_unbroken_run_ends(
    world: tuple[dict, dict, dict], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, dev, paths, steps = two_candidates(world)
    stop, names = steps.firsts[1] // 2, cfg["train"]["candidates"]
    assert 0 < stop < steps.firsts[1] - 1
    unbroken, paused = tmp_path / "unbroken", tmp_path / "paused"
    unbroken.mkdir(), paused.mkdir()
    train.train(cfg, dev, paths, unbroken, "cpu", resume=False)
    losses, calls = train.loss_of, []

    def ctrl_c_on_step(*args: object) -> object:
        calls.append(1)
        if len(calls) == stop * len(names):
            os.kill(os.getpid(), signal.SIGINT)
        return losses(*args)

    monkeypatch.setattr(train, "loss_of", ctrl_c_on_step)
    with pytest.raises(KeyboardInterrupt):
        train.train(cfg, dev, paths, paused, "cpu", resume=False)
    monkeypatch.undo()
    state = torch.load(train.resume_state(paused), weights_only=False)
    assert state["step"] == stop - 1 and state["history"] == []
    train.train(cfg, dev, paths, paused, "cpu", resume=True)
    assert same_weights(unbroken, paused, names)


def test_a_run_killed_between_saves_goes_on_from_its_last_periodic_state(
    world: tuple[dict, dict, dict], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, dev, paths, steps = two_candidates(world, every=2)
    names = cfg["train"]["candidates"]
    assert steps.firsts[1] > 4
    unbroken, killed = tmp_path / "unbroken", tmp_path / "killed"
    unbroken.mkdir(), killed.mkdir()
    train.train(cfg, dev, paths, unbroken, "cpu", resume=False)
    losses, calls = train.loss_of, []

    def killed_in_step_three(*args: object) -> object:
        calls.append(1)
        if len(calls) > 3 * len(names):
            raise RuntimeError("killed")
        return losses(*args)

    monkeypatch.setattr(train, "loss_of", killed_in_step_three)
    with pytest.raises(RuntimeError, match="killed"):
        train.train(cfg, dev, paths, killed, "cpu", resume=False)
    monkeypatch.undo()
    assert torch.load(train.resume_state(killed), weights_only=False)["step"] == 1
    assert not train.resume_state(killed).with_name("last.pt.part").exists()
    train.train(cfg, dev, paths, killed, "cpu", resume=True)
    assert same_weights(unbroken, killed, names)


def test_resume_alone_takes_the_latest_paused_run(tmp_path: Path) -> None:
    runs = tmp_path / "ns" / "runs"
    for name, finished, saved_at in (("a", False, 100), ("b", True, 300), ("c", False, 200)):
        state = train.resume_state(runs / name)
        state.parent.mkdir(parents=True)
        state.write_bytes(b"state")
        os.utime(state, (saved_at, saved_at))
        if finished:
            (runs / name / "metrics.yaml").write_text("{}\n", encoding="utf-8")
    assert train.paused_run({"artifacts": tmp_path}) == runs / "c"
    with pytest.raises(FileNotFoundError, match="no paused ns run"):
        train.paused_run({"artifacts": tmp_path / "elsewhere"})
