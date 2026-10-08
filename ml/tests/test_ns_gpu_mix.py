"""srpipe.tasks.ns.gpu_mix: a batch filtered in torch is the numpy Mixer's examples at the slot to rounding, whatever
parts its examples hold, and a slot without its hpf is caught; the self noise drawn on the device has the
microphones' level, apart for each example, in a batch with no talker and no foreground at all."""

from __future__ import annotations

import copy
import math

import numpy as np
import pytest

from srpipe.core.audio_io import write_wav
from srpipe.core.config import CONFIGS, load_device, load_yaml
from srpipe.generated import grid
from srpipe.scenes import device
from srpipe.tasks.ns import data

torch = pytest.importorskip("torch")

from srpipe.tasks.ns import gpu_mix  # noqa: E402

FS = grid.SAMPLE_RATE_HZ
UTTERANCES = 12
EPOCHS = 2


def mix_config() -> dict:
    cfg = copy.deepcopy(load_yaml(CONFIGS / "models" / "ns.yaml"))
    cfg["mix"] |= {"example_s": 2.048, "rooms": {"train": [0, 2]}}
    fan = {"name": "fan", "class": "stationary", "weight": 1.0, "prefix": "noise/fan/", "group": "file", "roles": {}}
    coloured = {"name": "coloured", "class": "stationary", "weight": 1.0, "synthetic": {"slope_db_per_octave": [-6, 3]}}
    cfg["noise"]["pools"] = [fan, coloured, {"name": "babble", "class": "babble", "weight": 1.0, "babble": True}]
    cfg["noise"]["classes"] = {
        "stationary": {"share": 0.6, "snr_db": [0.0, 10.0]},
        "babble": {"share": 0.4, "snr_db": [0.0, 10.0]},
    }
    cfg["tone"]["pools"] = [{"name": "hum", "weight": 1.0, "prefix": "noise/hum/", "group": "file", "roles": {}}]
    return cfg


def device_config() -> dict:
    dev = copy.deepcopy(load_device(CONFIGS / "scenes" / "device.yaml"))
    dev["name"] = "ns_gpu_mix_test"
    dev["rooms"]["count"] = 2
    dev["rooms"]["rt60_s"] = [0.2, 0.3]
    return dev


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict, dict, dict]:
    """A train role of synthetic pools (voiced bursts as speech, a fan as noise, hiss as tone and babble) and a
    two-room bank."""
    root = tmp_path_factory.mktemp("ns_gpu_mix")
    paths = {"raw": root / "raw", "interim": root / "interim", "processed": root / "processed", "cache": root}
    cfg, dev = mix_config(), device_config()
    rng = np.random.default_rng(1)
    folder = data.pool_dir(paths, cfg, "train")
    folder.mkdir(parents=True)
    pieces = []
    for k in range(UTTERANCES):
        n = int(rng.integers(FS // 2, 3 * FS // 2))
        pieces.append(0.1 * np.sin(2 * np.pi * (150 + 10 * k) * np.arange(n) / FS) * np.hanning(n))
    data.write_run(folder, "speech_0000", pieces, [0] * UTTERANCES)
    index = np.load(folder / "speech_0000_index.npz")
    np.savez(
        folder / "speech_index.npz",
        run=np.zeros(UTTERANCES, dtype=np.int32),
        offset=index["offset"],
        length=index["length"],
        active_rms=np.array(
            [device.active_rms(p, dev["talker"]["active_below_peak_db"]) for p in pieces], dtype=np.float32
        ),
    )
    write_wav(paths["raw"] / "noise" / "fan" / "a.wav", 0.05 * rng.standard_normal(4 * FS))
    row = {"item": "noise/fan/a.wav", "pool": "fan", "group": "a", "seconds": 4}
    data.write_tsv(folder / "noise_index.tsv", data.NOISE_FIELDS, [row])
    data.write_run(folder, "tone", [0.01 * rng.standard_normal(6 * FS)], [0])
    data.write_run(folder, "babble", [0.01 * rng.standard_normal(6 * FS)], [0])
    return cfg, dev, paths


def quiet(mixer: data.Mixer) -> data.Mixer:
    """The mixer without the microphones' self noise, which the two paths draw apart."""
    mixer.mics = device.Microphones(**{**mixer.mics.__dict__, "self_noise_rms": 0.0})
    return mixer


def error_db(got: torch.Tensor, want: np.ndarray) -> float:
    """Energy of the difference over the reference's, dB."""
    return 10.0 * math.log10(float(np.sum((got.numpy().astype(np.float64) - want) ** 2)) / float(np.sum(want**2.0)))


def every_part(world: tuple[dict, dict, dict]) -> tuple[data.Mixer, list[data.Recipe]]:
    """Every example of the first epochs: talking or not, a file, a synthetic or a babble foreground, or none."""
    cfg, dev, paths = world
    mixer = quiet(data.Mixer(cfg, dev, paths, "train", 7))
    recipes = [mixer.recipe(epoch, j) for epoch in range(EPOCHS) for j in range(len(mixer.kinds(epoch)))]
    pools = {r.draws.get("pool") for r in recipes}
    assert pools == {"fan", "coloured", "babble", None} and {r.dry is None for r in recipes} == {True, False}
    return mixer, recipes


def test_a_batch_filtered_in_torch_is_the_numpy_examples_at_the_slot(world: tuple[dict, dict, dict]) -> None:
    mixer, recipes = every_part(world)
    got = gpu_mix.Render(mixer.mics, mixer.n, "cpu")(gpu_mix.collate(recipes, mixer.n))
    worst = {"power": -math.inf, "speech": -math.inf}
    for i, r in enumerate(recipes):
        e = mixer.mixed(r)
        power, speech, _ = data.slot_powers(e, mixer.mics.gains)
        worst["power"] = max(worst["power"], error_db(got["power"][i], power))
        if r.dry is None:
            assert not got["speech"][i].any()
        else:
            worst["speech"] = max(worst["speech"], error_db(got["speech"][i], speech))
        np.testing.assert_array_equal(got["vad"][i].numpy(), e.vad)
    # The capture differs where a sample sits within float32 rounding of an int16 step: one step, now and then.
    assert worst["power"] < -60.0 and worst["speech"] < -100.0


def test_a_slot_without_its_hpf_is_caught(world: tuple[dict, dict, dict]) -> None:
    mixer, recipes = every_part(world)
    render = gpu_mix.Render(mixer.mics, mixer.n, "cpu")
    render.hpf = torch.ones_like(render.hpf)
    got = render(gpu_mix.collate(recipes, mixer.n))
    errors = [
        error_db(got["power"][i], data.slot_powers(mixer.mixed(r), mixer.mics.gains)[0]) for i, r in enumerate(recipes)
    ]
    assert max(errors) > -60.0


def test_the_self_noise_on_the_device_has_the_microphones_level_apart_for_each_example(
    world: tuple[dict, dict, dict],
) -> None:
    cfg, dev, paths = world
    cfg = copy.deepcopy(cfg)
    cfg["mix"]["foreground"] = 0.0
    cfg["tone"]["level_dbfs"] = [-150.0, -150.0]
    mixer = data.Mixer(cfg, dev, paths, "train", 7)
    recipes = [mixer.recipe(0, int(j)) for j in np.nonzero(mixer.kinds(0) < 0)[0][:2]]
    assert len(recipes) == 2 and all(r.dry is None and r.source is None and r.pair is None for r in recipes)
    got = gpu_mix.Render(mixer.mics, mixer.n, "cpu")(gpu_mix.collate(recipes, mixer.n))
    for i, r in enumerate(recipes):
        want = data.slot_powers(mixer.mixed(r), mixer.mics.gains)[0]
        assert abs(10.0 * math.log10(float(got["power"][i].mean()) / float(want.mean()))) < 0.3
    assert not torch.equal(got["power"][0], got["power"][1])
    assert not got["speech"].any() and not got["vad"].any()
