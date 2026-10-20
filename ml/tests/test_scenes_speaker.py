"""The speaker survey's figures: EER at the crossing, a threshold that lets at most the asked share of non-targets
through, and EER over the pairs of a set of windows; noise lands on a recording at the SNR asked."""

from __future__ import annotations

import numpy as np
import pytest

from srpipe.generated import grid
from srpipe.scenes import device

pytest.importorskip("torch")

from srpipe.scenes import speaker


def test_eer_is_zero_when_apart_and_a_half_when_the_same() -> None:
    assert speaker.eer(np.array([0.8, 0.9]), np.array([0.1, 0.2])) == 0.0
    same = np.linspace(0.0, 1.0, 101)
    assert speaker.eer(same, same) == pytest.approx(0.5, abs=0.01)


def test_eer_of_one_crossing_pair() -> None:
    # Accepting at >= 0.5: one target of four missed, one non-target of four passing.
    target, nontarget = np.array([0.4, 0.6, 0.7, 0.8]), np.array([0.1, 0.2, 0.3, 0.5])
    assert speaker.eer(target, nontarget) == pytest.approx(0.25)


def test_threshold_lets_through_at_most_the_share_asked() -> None:
    nontarget = np.arange(100, dtype=np.float64)
    for passing in (0.0, 0.01, 0.05, 0.5):
        t = speaker.threshold_at(nontarget, passing)
        assert np.mean(nontarget > t) <= passing
        assert np.mean(nontarget > t - 1.0) > passing


def test_pair_eer_on_two_clean_clusters_is_zero() -> None:
    rng = np.random.default_rng(0)
    a, b = np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])
    x = np.stack(
        [a + 0.05 * rng.standard_normal(3) for _ in range(5)] + [b + 0.05 * rng.standard_normal(3) for _ in range(5)]
    )
    assert speaker.pair_eer(x, ["a"] * 5 + ["b"] * 5) == 0.0


def test_owner_figures_pool_a_distance_s_sessions_as_the_gate_reads_them() -> None:
    """At 3 m one session keeps 2 of its 4 accepted windows and the other 4 of 6: per distance that is 6 of 10, not
    the mean of the two shares nor the worse one."""
    toward, away, aside = np.eye(3)[0], -np.eye(3)[0], np.eye(3)[1]

    def owner(group: str, e: np.ndarray) -> tuple:
        return speaker.Window("me", group, np.zeros(16, np.float32), accepted=True), e

    rows = [owner("d1 100", toward), owner("d1 100", toward)]
    rows += [owner("d1 300", e) for e in (toward, toward, away, away)]
    rows += [owner("d2 300", e) for e in (toward,) * 4 + (away,) * 2]
    rows += [(speaker.Window(f"them{k}", "d1 100", np.zeros(16, np.float32)), aside) for k in range(20)]
    cfg = {
        "seed": 1,
        "owner": {"spk": "me", "enrol": {"date": "d1", "distance_cm": "100"}, "counts": [1], "draws": 1},
        "rule": {"impostors_passing": 0.05, "curve": [0.05]},
    }
    figures = speaker.owner_figures(np.array([e for _, e in rows]), [w for w, _ in rows], cfg, [1.0])[1]
    assert figures["kept accepted d1 300"] == 0.5
    assert figures["kept accepted d2 300"] == pytest.approx(4 / 6)
    assert figures["kept accepted 300 cm"] == pytest.approx(6 / 10)


def flat_pair() -> device.Microphones:
    """Board B's pair with no difference between its microphones and no self noise."""
    n = grid.N_BINS
    return device.Microphones(
        np.ones(n, dtype=np.complex64), np.linspace(0.0, grid.SAMPLE_RATE_HZ / 2, n), np.zeros(n), np.zeros(n), 0, 0, 0
    )


def recording(rng: np.random.Generator, hops: int, talk: np.ndarray) -> np.ndarray:
    """A floor on both channels everywhere and a louder talker on the hops of talk, (samples, mics) int16."""
    floor = 30.0 * rng.standard_normal((hops * speaker.HOP, 2))
    voice = np.repeat(talk, speaker.HOP)[:, None] * 2000.0 * rng.standard_normal((hops * speaker.HOP, 1))
    return np.clip(np.rint(floor + voice), -32768, 32767).astype(np.int16)


def test_noise_lands_at_the_snr_asked_over_the_talker_with_the_floor_taken_out() -> None:
    rng, hops = np.random.default_rng(3), 200
    talk = np.zeros(hops, dtype=bool)
    talk[50:120] = True
    pcm = recording(rng, hops, talk)
    rirs = np.zeros((2, 2, 4), dtype=np.float32)
    rirs[device.NOISE, :, 0] = 1.0
    out = speaker.with_noise(pcm, talk, rirs, rng.standard_normal(len(pcm)), 10.0, flat_pair())
    added = out.astype(np.float64) - pcm
    power = np.mean(pcm[:, 0].astype(np.float64).reshape(-1, speaker.HOP) ** 2, axis=1)
    talker = power[talk].mean() - power[~talk].mean()
    assert 10 * np.log10(talker / np.mean(added[:, 0] ** 2)) == pytest.approx(10.0, abs=0.05)
    assert np.max(np.abs(added[:, 0] - added[:, 1])) <= 1.0


def test_noise_saturates_as_the_board_does_and_needs_speech_and_silence() -> None:
    rng, hops = np.random.default_rng(4), 100
    talk = np.zeros(hops, dtype=bool)
    talk[20:60] = True
    pcm = recording(rng, hops, talk)
    rirs = np.zeros((2, 2, 1), dtype=np.float32)
    rirs[device.NOISE, :, 0] = 1.0
    music = rng.standard_normal(len(pcm))
    loud = speaker.with_noise(pcm, talk, rirs, music, -40.0, flat_pair())
    assert loud.dtype == np.int16 and loud.max() == 32767 and loud.min() == -32768
    with pytest.raises(ValueError, match="speech and hops without"):
        speaker.with_noise(pcm, np.zeros(hops, dtype=bool), rirs, music, 10.0, flat_pair())


def test_a_window_from_its_first_vad_hop_and_its_snr_over_its_lead() -> None:
    lead = np.full(3 * speaker.HOP, 0.01, dtype=np.float32)
    talk = np.full(2 * speaker.HOP, 0.1, dtype=np.float32)
    w = speaker.Window("owner", "x", np.concatenate([lead, talk]), True, ("s#0",), 3)
    assert speaker.speech_of(w).samples.tolist() == talk.tolist() and speaker.speech_of(w).lead_hops == 0
    assert speaker.snr_db(w) == pytest.approx(20.0)
    assert np.isnan(speaker.snr_db(speaker.speech_of(w)))
