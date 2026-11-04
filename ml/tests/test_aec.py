"""aec against answers known without a room: a filter learns a known echo path, a talker survives it, silence costs
nothing, and with learning off nothing is cancelled (KEHOACH 3.5).
"""

from __future__ import annotations

import numpy as np
import pytest

from srpipe.dsp.afe import aec

HOP = aec.HOP
HOPS = 500
f32 = np.float32


def far_end(rng: np.random.Generator, hops: int = HOPS) -> np.ndarray:
    return (rng.standard_normal(hops * HOP) * 0.05).astype(f32)


def room(rng: np.random.Generator) -> np.ndarray:
    return (rng.standard_normal(400) * np.exp(-np.arange(400) / 80.0) * 0.25).astype(f32)


def run(canceller: aec.Aec, mic: np.ndarray, far: np.ndarray) -> np.ndarray:
    out = np.empty_like(mic)
    for i in range(0, mic.size, HOP):
        cleaned, _ = canceller.process(mic[None, i : i + HOP], far[i : i + HOP])
        out[i : i + HOP] = cleaned[0]
    return out


def gain_db(before: np.ndarray, after: np.ndarray) -> float:
    return float(10.0 * np.log10((before**2).sum() / ((after**2).sum() + 1e-20)))


def test_a_known_echo_path_is_learnt_past_the_goal_of_20_db() -> None:
    rng = np.random.default_rng(1)
    far = far_end(rng)
    mic = np.convolve(far, room(rng))[: far.size].astype(f32)
    out = run(aec.Aec(), mic, far)
    assert gain_db(mic[-100 * HOP :], out[-100 * HOP :]) > 20.0


def test_with_learning_off_nothing_is_cancelled() -> None:
    rng = np.random.default_rng(2)
    far = far_end(rng)
    mic = np.convolve(far, room(rng))[: far.size].astype(f32)
    out = run(aec.Aec(learn=False), mic, far)
    assert abs(gain_db(mic, out)) < 0.01


def test_a_talker_over_the_echo_loses_less_than_3_db_and_nothing_diverges() -> None:
    rng = np.random.default_rng(3)
    far = far_end(rng)
    talker = (rng.standard_normal(far.size) * 0.05).astype(f32)
    talker[: far.size // 2] = 0.0
    mic = np.convolve(far, room(rng))[: far.size].astype(f32) + talker
    canceller = aec.Aec()
    out = run(canceller, mic, far)
    late = slice(far.size // 2, None)
    kept = float((out[late] * talker[late]).sum() / (talker[late] ** 2).sum())
    assert 20.0 * np.log10(kept) > -3.0
    assert canceller.resets == 0


def test_silence_after_the_tail_passes_the_microphone_untouched() -> None:
    rng = np.random.default_rng(4)
    canceller = aec.Aec()
    mic = (rng.standard_normal(HOP) * 0.1).astype(f32)
    out, power = canceller.process(mic[None], np.zeros(HOP, dtype=f32))
    assert np.array_equal(out[0], mic)
    assert not power.any()


def test_a_short_gap_forgets_the_reference_and_keeps_the_filter() -> None:
    rng = np.random.default_rng(7)
    far = far_end(rng)
    mic = np.convolve(far, room(rng))[: far.size].astype(f32)
    canceller = aec.Aec()
    half = far.size // 2
    run(canceller, mic[:half], far[:half])
    canceller.flush()
    assert not canceller.spectra.any() and not canceller.previous.any()
    assert canceller.mics[0].weights.any()
    out = run(canceller, mic[half:], far[half:])
    settled = slice(20 * HOP, 40 * HOP)
    assert gain_db(mic[half:][settled], out[settled]) > 20.0


def test_a_bulk_delay_keeps_the_filter_for_the_room() -> None:
    rng = np.random.default_rng(5)
    far = far_end(rng)
    echo = np.convolve(far, room(rng))[: far.size].astype(f32)
    mic = np.concatenate([np.zeros(200, dtype=f32), echo[:-200]])
    out = run(aec.Aec(aec.AecConfig(bulk_delay_samples=200)), mic, far)
    assert gain_db(mic[-100 * HOP :], out[-100 * HOP :]) > 20.0


def test_a_bad_configuration_is_refused() -> None:
    with pytest.raises(ValueError, match="bad aec configuration"):
        aec.Aec(aec.AecConfig(n_partitions=1))
