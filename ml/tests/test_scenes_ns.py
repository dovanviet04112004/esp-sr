"""Check srpipe.scenes.ns: gains applied to each part alone give figures that add up, delay and window cancelled."""

from __future__ import annotations

import numpy as np
import pytest

from srpipe.generated import grid
from srpipe.scenes import ns

HOPS = 120
SETTLE_HOPS = 10


def parts(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = HOPS * grid.HOP_SAMPLES
    t = np.arange(n) / grid.SAMPLE_RATE_HZ
    labels = (np.arange(HOPS) // 20) % 2 == 1
    speech = 0.1 * np.sin(2 * np.pi * 440.0 * t) * np.repeat(labels, grid.HOP_SAMPLES)
    return speech, 0.01 * rng.standard_normal(n), labels


def test_unit_gains_lose_nothing() -> None:
    speech, noise, labels = parts(np.random.default_rng(1))
    f = ns.figures(ns.shadow(speech, noise, lambda p: np.ones_like(p)), labels, SETTLE_HOPS)
    assert (f.noise_down_db, f.pause_noise_down_db, f.speech_down_db, f.snr_gain_db) == pytest.approx(
        (0.0, 0.0, 0.0, 0.0), abs=1e-6
    )


def test_a_constant_half_takes_6_db_off_both_parts_and_no_snr() -> None:
    speech, noise, labels = parts(np.random.default_rng(2))
    f = ns.figures(ns.shadow(speech, noise, lambda p: np.full_like(p, 0.5)), labels, SETTLE_HOPS)
    six = 20 * np.log10(2.0)
    assert (f.noise_down_db, f.pause_noise_down_db, f.speech_down_db) == pytest.approx((six, six, six), abs=1e-4)
    assert f.snr_gain_db == pytest.approx(0.0, abs=1e-4)


def test_gains_that_close_only_pauses_suppress_noise_in_pauses_only() -> None:
    speech, noise, labels = parts(np.random.default_rng(3))
    state = {"hop": 0}

    def gate(p: np.ndarray) -> np.ndarray:
        on = labels[state["hop"]]
        state["hop"] += 1
        return np.full_like(p, 1.0 if on else 0.1)

    f = ns.figures(ns.shadow(speech, noise, gate), labels, SETTLE_HOPS)
    assert f.pause_noise_down_db > 12.0
    assert f.speech_down_db < 1.0
    assert f.snr_gain_db < 1.0 < f.noise_down_db
