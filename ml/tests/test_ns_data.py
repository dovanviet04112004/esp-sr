"""srpipe.tasks.ns.data: the noise pools' groups and roles, an epoch that hears every utterance once, examples that
are a pure function of their index, the drawn SNR, the talker alone at the slot as the capture of a quiet room, and
examples without a talker."""

from __future__ import annotations

import copy
import math

import numpy as np
import pytest

from srpipe.core.audio_io import INT16_SCALE, ramped, write_wav
from srpipe.core.config import CONFIGS, load_yaml
from srpipe.generated import grid
from srpipe.scenes import device
from srpipe.tasks.ns import data

FS, HOP = grid.SAMPLE_RATE_HZ, grid.HOP_SAMPLES
UTTERANCES = 12


def facts() -> data.NoiseFacts:
    return data.NoiseFacts(
        music={"music-fma-0001": ("Y", "Artist A"), "music-fma-0002": ("N", "Artist B")},
        background={"noise-free-sound-0001"},
        isotropic={"rir/openslr28/RIRS_NOISES/real_rirs_isotropic_noises/a.wav": "room1"},
    )


def test_each_pool_groups_files_by_its_own_key() -> None:
    fan = {"prefix": "noise/dns/datasets/noise/fan_", "group": "freesound"}
    assert data.group_of(fan, "noise/dns/datasets/noise/fan_Freesound_validated_100355_3.wav", facts()) == (
        "fan_Freesound_validated_100355"
    )
    assert data.group_of(fan, "noise/dns/datasets/noise/door_1.wav", facts()) is None
    vocals = {"prefix": "noise/musan/musan/music/", "vocals": "Y", "group": "artist"}
    assert data.group_of(vocals, "noise/musan/musan/music/fma/music-fma-0001.wav", facts()) == "Artist A"
    assert data.group_of(vocals, "noise/musan/musan/music/fma/music-fma-0002.wav", facts()) is None
    steady = {"prefix": "noise/demand/", "file": "ch01.wav", "group": "environment", "envs": {"TBUS": "train"}}
    assert data.group_of(steady, "noise/demand/TBUS/ch01.wav", facts()) == "TBUS"
    assert data.group_of(steady, "noise/demand/TBUS/ch02.wav", facts()) is None
    assert data.group_of(steady, "noise/demand/DLIVING/ch01.wav", facts()) is None
    background = {"prefix": "noise/musan/musan/noise/free-sound/", "background": True, "group": "file"}
    assert data.group_of(background, "noise/musan/musan/noise/free-sound/noise-free-sound-0001.wav", facts())
    assert data.group_of(background, "noise/musan/musan/noise/free-sound/noise-free-sound-0002.wav", facts()) is None


def test_a_noise_group_in_two_roles_is_reported() -> None:
    fine = {"a_1.wav": ("fan", "a", "train"), "a_2.wav": ("fan", "a", "train"), "b_1.wav": ("fan", "b", "test")}
    assert data.groups_disjoint(fine) == []
    leaked = fine | {"a_3.wav": ("fan", "a", "test")}
    assert data.groups_disjoint(leaked) == ["fan group a sits in ['test', 'train']"]


def ns_config() -> dict:
    cfg = copy.deepcopy(load_yaml(CONFIGS / "models" / "ns.yaml"))
    cfg["mix"] |= {"example_s": 2.048, "rooms": {"train": [0, 2], "val": [0, 2], "test": [0, 2]}}
    cfg["mix"]["long_pause"]["probability"] = 0.0
    cfg["noise"]["pools"] = [
        {"name": "fan", "class": "stationary", "weight": 1.0, "prefix": "noise/fan/", "group": "file", "roles": {}}
    ]
    cfg["noise"]["classes"] = {"stationary": {"share": 1.0, "snr_db": [5.0, 5.0]}}
    cfg["tone"]["pools"] = [{"name": "hum", "weight": 1.0, "prefix": "noise/hum/", "group": "file", "roles": {}}]
    return cfg


def device_config() -> dict:
    dev = copy.deepcopy(load_yaml(CONFIGS / "scenes" / "device.yaml"))
    dev["name"] = "ns_test"
    dev["rooms"]["count"] = 2
    dev["rooms"]["rt60_s"] = [0.2, 0.3]
    return dev


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict, dict, dict]:
    """A train role of synthetic pools: voiced bursts of varied length as speech, a fan hum as noise, a quiet hum as
    room tone, and a two-room bank."""
    root = tmp_path_factory.mktemp("ns")
    paths = {"raw": root / "raw", "interim": root / "interim", "processed": root / "processed", "cache": root}
    cfg, dev = ns_config(), device_config()
    rng = np.random.default_rng(1)
    folder = data.pool_dir(paths, cfg, "train")
    folder.mkdir(parents=True)
    pieces, lengths = [], []
    for k in range(UTTERANCES):
        n = int(rng.integers(FS // 2, 3 * FS // 2))
        t = np.arange(n) / FS
        pieces.append(0.1 * np.sin(2 * np.pi * (150 + 10 * k) * t) * np.hanning(n))
        lengths.append(n)
    data.write_run(folder, "speech_0000", pieces, [0] * UTTERANCES)
    run = np.load(folder / "speech_0000_index.npz")
    np.savez(
        folder / "speech_index.npz",
        run=np.zeros(UTTERANCES, dtype=np.int32),
        offset=run["offset"],
        length=run["length"],
        active_rms=np.array([device.active_rms(p, 40.0) for p in pieces], dtype=np.float32),
    )
    write_wav(paths["raw"] / "noise" / "fan" / "a.wav", 0.05 * rng.standard_normal(4 * FS))
    data.write_tsv(
        folder / "noise_index.tsv",
        data.NOISE_FIELDS,
        [{"item": "noise/fan/a.wav", "pool": "fan", "group": "a", "seconds": 4}],
    )
    data.write_run(folder, "tone", [0.01 * rng.standard_normal(6 * FS)], [0])
    data.write_run(folder, "babble", [0.01 * rng.standard_normal(6 * FS)], [0])
    return cfg, dev, paths


def test_an_epoch_hears_every_utterance_once(world: tuple[dict, dict, dict]) -> None:
    cfg, dev, paths = world
    mixer = data.Mixer(cfg, dev, paths, "train", 7)
    s = mixer.stream(0)
    assert sorted(s.order.tolist()) == list(range(UTTERANCES))
    assert np.all(np.diff(s.start) >= mixer.speech.length[s.order][:-1])
    assert np.all(s.start % HOP == 0)
    windows = int((mixer.kinds(0) >= 0).sum())
    heard = sum(float(np.sum(mixer.dry(0, w * mixer.n, (w + 1) * mixer.n)[0] ** 2)) for w in range(windows))
    ramp = dev["talker"]["edge_ramp_s"]
    said = sum(
        float(np.sum((ramped(mixer.speech.utterance(int(u)), ramp) * scale) ** 2))
        for u, scale in zip(s.order, s.scale, strict=True)
    )
    assert heard == pytest.approx(said, rel=1e-9)
    assert not np.array_equal(mixer.stream(1).order, s.order)


def test_the_same_index_gives_the_same_example(world: tuple[dict, dict, dict]) -> None:
    cfg, dev, paths = world
    a = data.Mixer(cfg, dev, paths, "train", 7).example(0, 2)
    b = data.Mixer(cfg, dev, paths, "train", 7).example(0, 2)
    assert np.array_equal(a.capture, b.capture) and np.array_equal(a.talker, b.talker) and a.draws == b.draws
    c = data.Mixer(cfg, dev, paths, "train", 7).example(0, 3)
    assert not np.array_equal(a.capture, c.capture)


def speech_example(mixer: data.Mixer, epoch: int = 0) -> data.Example:
    for j, kind in enumerate(mixer.kinds(epoch)):
        if kind >= 0:
            e = mixer.example(epoch, j)
            if e.draws["talker"]:
                return e
    raise AssertionError("no example with a talker")


def test_the_drawn_snr_is_the_one_on_the_talkers_active_hops(world: tuple[dict, dict, dict]) -> None:
    cfg, dev, paths = world
    cfg = copy.deepcopy(cfg)
    cfg["mix"]["foreground"] = 1.0
    cfg["tone"]["level_dbfs"] = [-150.0, -150.0]
    mixer = data.Mixer(cfg, dev, paths, "train", 7)
    mixer.mics = device.Microphones(**{**mixer.mics.__dict__, "self_noise_rms": 0.0})
    e = speech_example(mixer)
    assert e.draws["snr_db"] == 5.0
    talker = e.talker[0].astype(np.float64)
    noise = e.capture[:, 0] / INT16_SCALE - talker
    active = np.repeat(e.vad.astype(bool) & np.concatenate([e.vad[1:], [0]]).astype(bool), HOP)
    snr = 10 * math.log10(np.mean(talker[active] ** 2) / np.mean(noise**2))
    # Drawn in the air at ch0, as the board simulation draws it; measured past ch0's calib/bal response.
    assert abs(snr - 5.0) < 1.0


def test_the_talker_alone_at_the_slot_is_the_capture_of_a_quiet_room(world: tuple[dict, dict, dict]) -> None:
    cfg, dev, paths = world
    cfg = copy.deepcopy(cfg)
    cfg["mix"] |= {"foreground": 0.0, "global_gain_db": [0.0, 0.0]}
    cfg["tone"]["level_dbfs"] = [-150.0, -150.0]
    mixer = data.Mixer(cfg, dev, paths, "train", 7)
    mixer.mics = device.Microphones(**{**mixer.mics.__dict__, "self_noise_rms": 0.0})
    e = speech_example(mixer)
    x = device.slot_bins(e.capture.T.astype(np.float64) / INT16_SCALE, mixer.mics.gains)
    s = device.slot_bins(e.talker, mixer.mics.gains)
    active = e.vad.astype(bool)
    residue_db = 10 * math.log10(np.sum(np.abs(x[active] - s[active]) ** 2) / np.sum(np.abs(s[active]) ** 2))
    assert residue_db < -35.0


def test_an_example_without_a_talker_has_no_speech(world: tuple[dict, dict, dict]) -> None:
    cfg, dev, paths = world
    mixer = data.Mixer(cfg, dev, paths, "train", 7)
    j = int(np.nonzero(mixer.kinds(0) < 0)[0][0])
    e = mixer.example(0, j)
    assert not e.draws["talker"] and not e.vad.any() and not np.any(e.talker)


def test_train_takes_only_clean_clips_of_its_corpora_and_never_common_voice() -> None:
    cfg = ns_config()
    assert data.train_rule(cfg, "speech/common_voice_vi/cv-corpus/vi/clips/a.mp3") is None
    rule = data.train_rule(cfg, "speech/bud500/data/train-00000.parquet#3")
    assert rule is not None
    quiet = {"quiet_dbfs": -60.0, "loud_dbfs": -15.0}
    noisy = {"quiet_dbfs": -45.0, "loud_dbfs": -10.0}
    assert data.clean_enough(quiet, rule["min_span_db"], rule["max_quiet_dbfs"])
    assert not data.clean_enough(noisy, rule["min_span_db"], rule["max_quiet_dbfs"])
    assert not data.clean_enough({"quiet_dbfs": None, "loud_dbfs": -10.0}, rule["min_span_db"])
