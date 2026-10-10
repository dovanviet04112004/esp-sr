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
