"""Scenes of srpipe.scenes.room: angle labels that the microphones' delay confirms, the SNR and level asked for,
interferers apart from the talker, the same bytes for the same seed."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from srpipe.core.audio_io import read_wav, write_wav
from srpipe.core.config import CONFIGS, load_yaml
from srpipe.generated import array, grid
from srpipe.scenes import room

UPSAMPLE = 32


@pytest.fixture
def raw_root(tmp_path: Path) -> Path:
    rng = np.random.default_rng(1)
    for spk in ("SPK01", "SPK02", "SPK03"):
        for n in range(3):
            burst = rng.standard_normal(grid.SAMPLE_RATE_HZ) * np.hanning(grid.SAMPLE_RATE_HZ) * 0.1
            write_wav(tmp_path / "speech" / spk / f"{spk}_{n}.wav", burst)
    write_wav(tmp_path / "noise" / "ROOM" / "ch01.wav", 0.05 * rng.standard_normal(4 * grid.SAMPLE_RATE_HZ))
    return tmp_path


def small(**changes: object) -> dict:
    cfg = load_yaml(CONFIGS / "scenes" / "standard.yaml")
    base = {"speech": "speech", "noise": "noise", "duration_s": 2.0, "lead_s": 0.2, "scenes_per_combination": 1}
    return {**cfg, **base, **changes}


def gcc_phat_tdoa_samples(x: np.ndarray) -> float:
    cross = np.fft.rfft(x[:, 0]) * np.conj(np.fft.rfft(x[:, 1]))
    cc = np.fft.irfft(cross / (np.abs(cross) + 1e-12), UPSAMPLE * len(x))
    reach = UPSAMPLE * 4
    lags = np.concatenate([cc[-reach:], cc[: reach + 1]])
    return (np.argmax(lags) - reach) / UPSAMPLE


@pytest.mark.parametrize("index", range(4))
def test_the_angle_label_is_what_the_microphone_delay_measures(raw_root: Path, index: int) -> None:
    scene = room.build_scene(small(rt60_s=[0.0]), raw_root, index)
    talker = scene["labels"]["talker"]
    measured = gcc_phat_tdoa_samples(scene["talker"])
    assert abs(measured - talker["tdoa_s"] * grid.SAMPLE_RATE_HZ) <= 1.0 / UPSAMPLE
    far_field = np.degrees(np.arccos(np.clip(talker["tdoa_s"] * array.SPEED_OF_SOUND_M_S / array.SPACING_M, -1, 1)))
    assert abs(far_field - talker["angle_deg"]) < 1.0
    assert (talker["angle_deg"] < 90) == (talker["tdoa_s"] > 0)


def test_level_and_snr_are_the_ones_asked_for(raw_root: Path) -> None:
    cfg = small(rt60_s=[0.3], interferer={**small()["interferer"], "kinds": ["noise"]}, snr_db=[5.0])
    scene = room.build_scene(cfg, raw_root, 1)
    active = room.active_hops(scene["talker_dry"], cfg["active_below_peak_db"])
    hops = scene["talker"][: len(active) * grid.HOP_SAMPLES, 0].reshape(-1, grid.HOP_SAMPLES)[active]
    level = 10 * np.log10(np.mean(hops**2))
    noise = 10 * np.log10(np.mean(scene["interferer"][:, 0] ** 2))
    assert abs(level - cfg["speech_level_dbfs"]) < 0.01
    assert abs(level - noise - 5.0) < 0.01
    assert scene["labels"]["snr_db"] == 5.0


def test_the_measured_rt60_is_the_target_within_tolerance(raw_root: Path) -> None:
    cfg = small(rt60_s=[0.5])
    labels = room.build_scene(cfg, raw_root, 0)["labels"]
    assert abs(labels["rt60_measured_s"] / 0.5 - 1.0) <= cfg["rt60_tolerance"]


def test_an_interferer_sits_apart_from_the_talker(raw_root: Path) -> None:
    cfg = small(rt60_s=[0.0])
    for index in range(len(room.combinations(cfg))):
        labels = room.build_scene(cfg, raw_root, index)["labels"]
        if labels["interferer"] is not None:
            gap = abs(labels["interferer"]["angle_deg"] - labels["talker"]["angle_deg"])
            assert gap >= cfg["interferer"]["min_separation_deg"]


def test_the_same_seed_writes_the_same_set(raw_root: Path, tmp_path: Path) -> None:
    cfg = small(rt60_s=[0.2], interferer={**small()["interferer"], "kinds": ["talker"]}, snr_db=[10.0])
    first = room.build_set(cfg, raw_root, tmp_path / "a", workers=2).read_text()
    second = room.build_set(cfg, raw_root, tmp_path / "b", workers=1).read_text()
    assert first == second
    assert read_wav(tmp_path / "a" / "scene_0001" / "mix.wav")[0].shape[1] == 2
