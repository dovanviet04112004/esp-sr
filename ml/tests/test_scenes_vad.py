"""Labelled vad scenes put speech where their labels say, at the level and SNR asked for (E7-T3)."""

from __future__ import annotations

import numpy as np
import pytest

from srpipe.core.config import load_config
from srpipe.generated import grid
from srpipe.scenes import vad as scenes

FS = grid.SAMPLE_RATE_HZ
HOP = grid.HOP_SAMPLES
CFG = load_config("afe/vad")["eval"]


def utterance(rng: np.random.Generator, seconds: float) -> np.ndarray:
    """Room noise, a tone burst in the middle half, room noise: the burst hops are the speech."""
    n = round(seconds * FS)
    x = 1e-4 * rng.standard_normal(n)
    x[n // 4 : 3 * n // 4] += 0.2 * np.sin(2 * np.pi * 300 * np.arange(n // 2) / FS)
    return x


def test_labels_cover_the_burst_and_nothing_else() -> None:
    x = scenes.whole_hops(utterance(np.random.default_rng(0), 2.0))
    labels = scenes.utterance_labels(x, CFG)
    burst = np.zeros(len(x) // HOP, dtype=bool)
    burst[(len(x) // 4 + HOP - 1) // HOP : (3 * len(x) // 4) // HOP] = True
    assert np.sum(labels != burst) <= 2


@pytest.mark.parametrize(("snr_db", "level_dbfs"), [(10.0, -26.0), (0.0, -40.0)])
def test_a_scene_meets_its_level_and_snr(snr_db: float, level_dbfs: float) -> None:
    rng = np.random.default_rng(1)
    utterances = [utterance(rng, s) for s in (1.0, 1.5, 2.0)]
    noise = rng.standard_normal(3 * FS)
    scene = scenes.build(utterances, noise, "white", snr_db, level_dbfs, CFG, np.random.default_rng(2))
    assert len(scene.labels) * HOP == len(scene.signal)
    clean = scenes.build(utterances, 0 * noise + 1e-30, "none", 300.0, level_dbfs, CFG, np.random.default_rng(2))
    speech_hops = clean.signal.reshape(-1, HOP)[clean.labels].astype(np.float64)
    assert 10 * np.log10(np.mean(speech_hops**2)) == pytest.approx(level_dbfs, abs=0.01)
    noise_only = scene.signal.astype(np.float64) - clean.signal
    assert 10 * np.log10(np.mean(speech_hops**2) / np.mean(noise_only**2)) == pytest.approx(snr_db, abs=0.01)


def test_pauses_are_silent_hops() -> None:
    rng = np.random.default_rng(3)
    scene = scenes.build([utterance(rng, 1.0)], np.ones(FS), "dc", 300.0, -26.0, CFG, np.random.default_rng(4))
    first_speech = int(np.argmax(scene.labels))
    pause_hops = round(CFG["pause_s"][0] * FS / HOP)
    assert first_speech >= pause_hops
