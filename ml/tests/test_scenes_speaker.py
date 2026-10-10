"""The speaker survey's figures: EER at the crossing, a threshold that lets at most the asked share of non-targets
through, and EER over the pairs of a set of windows."""

from __future__ import annotations

import numpy as np
import pytest

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
