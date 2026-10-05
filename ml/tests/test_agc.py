"""agc settles speech on its target and stays there, freezes without speech, and never lets a sample pass the
ceiling (E7-T4)."""

from __future__ import annotations

import numpy as np
import pytest

from srpipe.dsp.afe import agc
from srpipe.generated import afe, grid

FS = grid.SAMPLE_RATE_HZ
HOP = grid.HOP_SAMPLES
HOP_S = HOP / FS


def noise_at(level_dbfs: float, hops: int, seed: int = 0) -> np.ndarray:
    x = np.random.default_rng(seed).standard_normal(hops * HOP)
    return (10 ** (level_dbfs / 20) * x / np.sqrt(np.mean(x**2))).astype(np.float32)


def run(a: agc.Agc, x: np.ndarray, speech: bool = True) -> tuple[np.ndarray, np.ndarray]:
    outs, gains = [], []
    for k in range(0, len(x), HOP):
        out, gain_db = a.process(x[k : k + HOP], speech)
        outs.append(out)
        gains.append(gain_db)
    return np.concatenate(outs), np.array(gains)


def level_dbfs(x: np.ndarray) -> float:
    return float(10 * np.log10(np.mean(np.asarray(x, dtype=np.float64) ** 2)))


@pytest.mark.parametrize("input_dbfs", [-50.0, -40.0, -26.0, -10.0])
def test_a_steady_input_settles_on_the_target_and_stays(input_dbfs: float) -> None:
    a = agc.Agc()
    out, gains = run(a, noise_at(input_dbfs, 1200))
    assert level_dbfs(out[-100 * HOP :]) == pytest.approx(afe.AGC_TARGET_DBFS, abs=0.2)
    # Noise hops vary in power, so the settled gain follows the level estimate by a few hundredths of a dB.
    assert np.ptp(gains[-100:]) < 0.2


def test_a_truly_steady_input_leaves_the_gain_still() -> None:
    x = np.tile(noise_at(-40.0, 1), 3000)
    _, gains = run(agc.Agc(), x)
    assert gains[-1] == pytest.approx(afe.AGC_TARGET_DBFS + 40.0, abs=0.01)
    assert np.ptp(gains[-200:]) < 1e-3
    steps = np.diff(gains[-1000:])
    assert np.all(steps <= 0) or np.all(steps >= 0)


def test_the_gain_climbs_and_falls_at_the_plan_rates() -> None:
    a = agc.Agc()
    _, up = run(a, noise_at(-46.0, 40))
    assert np.diff(up)[5:30] == pytest.approx(afe.AGC_UP_DB_PER_S * HOP_S, abs=1e-4)
    b = agc.Agc()
    _, down = run(b, noise_at(-6.0, 40))
    assert np.diff(down)[5:30] == pytest.approx(-afe.AGC_DOWN_DB_PER_S * HOP_S, abs=1e-4)


def test_the_gain_freezes_without_speech() -> None:
    a = agc.Agc()
    run(a, noise_at(-46.0, 100))
    frozen = a.gain
    _, gains = run(a, noise_at(-70.0, 100), speech=False)
    assert a.gain == frozen and np.all(gains == gains[0])


def test_the_gain_stays_within_its_range() -> None:
    a = agc.Agc()
    _, gains = run(a, noise_at(-90.0, 3000))
    assert gains[-1] == pytest.approx(afe.AGC_GAIN_MAX_DB, abs=1e-4)
    b = agc.Agc()
    _, gains = run(b, noise_at(0.0, 1000))
    assert gains[-1] == pytest.approx(afe.AGC_GAIN_MIN_DB, abs=1e-4)


def test_quiet_samples_come_out_one_look_ahead_late() -> None:
    a = agc.Agc()
    x = noise_at(-40.0, 4)
    out, _ = run(a, x, speech=False)
    delay = agc.lookahead_samples(afe.AGC_LOOKAHEAD_MS)
    assert delay == 64
    assert np.array_equal(out[delay:], x[:-delay]) and not np.any(out[:delay])


def test_no_sample_passes_the_ceiling() -> None:
    rng = np.random.default_rng(1)
    x = noise_at(-30.0, 60, seed=2)
    for start in rng.integers(0, len(x) - 400, 12):
        x[start : start + 40] = rng.uniform(-1.0, 1.0, 40)
    x[1000] = 1.0
    out, _ = run(agc.Agc(), x, speech=False)
    # Exact in arithmetic; the running sum of the mean carries a hop of float32 rounding, about 2e-6 at worst.
    assert np.max(np.abs(out)) <= agc.db_to_amplitude(afe.AGC_LIMIT_DBFS) * np.float32(1.00001)
    assert np.max(np.abs(out)) > 0.5


def test_a_new_target_moves_the_settled_level() -> None:
    a = agc.Agc()
    x = noise_at(-40.0, 1500)
    run(a, x[: 600 * HOP])
    a.set_target(-20.0)
    out, _ = run(a, x[600 * HOP :])
    assert level_dbfs(out[-100 * HOP :]) == pytest.approx(-20.0, abs=0.2)


@pytest.mark.parametrize(
    "change",
    [{"target_dbfs": 1.0}, {"gain_min_db": 10.0, "gain_max_db": 0.0}, {"up_db_per_s": 0.0}, {"release_ms": 0.0}],
)
def test_a_bad_configuration_is_refused(change: dict) -> None:
    with pytest.raises(ValueError):
        agc.Agc(agc.AgcConfig(**change))


def test_a_start_gain_holds_without_speech_and_moves_with_it() -> None:
    quiet = np.full(grid.HOP_SAMPLES, 10.0 ** (-55.0 / 20.0), dtype=np.float32)
    held = agc.Agc(start_db=24.0)
    for _ in range(50):
        _, gain_db = held.process(quiet, False)
    assert abs(float(gain_db) - 24.0) < 1e-3
    for _ in range(50):
        _, gain_db = held.process(quiet, True)
    assert float(gain_db) > 24.0
    fresh = agc.Agc()
    _, first_db = fresh.process(quiet, False)
    assert float(first_db) == 0.0
