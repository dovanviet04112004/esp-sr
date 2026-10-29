"""srpipe.core.repitch: a kept contour resynthesises near the original F0 and leaves the clip outside its stretch as it
was; a target contour moves F0 inside the stretch only, which a kept contour does not; a glide reads as semitones over
the reference; noise has no voiced stretch; a speaker's median skips unvoiced frames; the config keeps its fields."""

from __future__ import annotations

import math

import numpy as np
import pytest

pytest.importorskip("parselmouth")

from srpipe.core import repitch

FS = repitch.FS
CFG = repitch.repitch_config(
    {
        "points": 10,
        "voiced_share": 0.6,
        "min_voiced_s": 0.05,
        "pad_s": 0.2,
        "fade_s": 0.01,
        "time_step_s": 0.01,
        "f0_range_hz": [75, 600],
        "workers": 5,
    }
)
STRETCH = (0.4, 0.6)


def voiced(f0_hz: np.ndarray) -> np.ndarray:
    """Harmonics below 4 kHz of a voice whose F0 follows f0_hz sample by sample, falling 1/k in amplitude."""
    phase = 2.0 * np.pi * np.cumsum(f0_hz) / FS
    x = sum(np.sin(k * phase) / k * (k * f0_hz < 4000.0) for k in range(1, 40))
    return 0.5 * x / np.max(np.abs(x))


def median_in(x: np.ndarray, start_s: float, end_s: float) -> float:
    t = repitch.track(x, CFG)
    inside = (t.times_s >= start_s) & (t.times_s <= end_s) & (t.f0_hz > 0)
    return float(np.median(t.f0_hz[inside]))


@pytest.fixture(scope="module")
def steady() -> np.ndarray:
    return voiced(np.full(FS, 150.0))


def test_a_kept_contour_resynthesises_near_its_f0_and_leaves_the_rest(steady: np.ndarray) -> None:
    y = repitch.resynthesised(steady, STRETCH, None, CFG)
    assert median_in(y, *STRETCH) == pytest.approx(150.0, rel=0.02)
    a, b = round((STRETCH[0] - CFG.pad_s) * FS), round((STRETCH[1] + CFG.pad_s) * FS)
    assert np.array_equal(y[:a], steady[:a]) and np.array_equal(y[b:], steady[b:])
    assert len(y) == len(steady)


def test_a_target_contour_moves_f0_inside_the_stretch_only(steady: np.ndarray) -> None:
    target = np.full(CFG.points, 200.0)
    moved = repitch.resynthesised(steady, STRETCH, target, CFG)
    kept = repitch.resynthesised(steady, STRETCH, None, CFG)
    assert median_in(moved, 0.45, 0.55) == pytest.approx(200.0, rel=0.03)
    assert median_in(kept, 0.45, 0.55) != pytest.approx(200.0, rel=0.03)
    assert median_in(moved, 0.05, 0.15) == pytest.approx(150.0, rel=0.02)


def test_a_glide_reads_as_semitones_over_the_reference() -> None:
    f0 = np.interp(np.arange(FS) / FS, [0.0, STRETCH[0], STRETCH[1], 1.0], [120.0, 120.0, 180.0, 180.0])
    x = voiced(f0)
    t = repitch.track(x, CFG)
    stretch = repitch.voiced_stretch(t, *STRETCH, CFG)
    assert stretch is not None
    contour = repitch.contour_st(t, stretch, 150.0, CFG.points)
    assert len(contour) == CFG.points
    assert contour[0] == pytest.approx(12.0 * math.log2(120.0 / 150.0), abs=0.6)
    assert contour[-1] == pytest.approx(12.0 * math.log2(180.0 / 150.0), abs=0.6)
    assert np.all(np.diff(contour) > -0.2)
    assert np.allclose(repitch.contour_hz(contour, 150.0), 150.0 * 2.0 ** (contour / 12.0))


def test_noise_has_no_voiced_stretch(steady: np.ndarray) -> None:
    noise = np.random.default_rng(0).normal(0.0, 0.1, FS)
    assert repitch.voiced_stretch(repitch.track(noise, CFG), *STRETCH, CFG) is None
    assert repitch.voiced_stretch(repitch.track(steady, CFG), *STRETCH, CFG) is not None


def test_a_speakers_median_skips_unvoiced_frames() -> None:
    tracks = [
        repitch.Track(np.arange(4) * 0.01, np.array([0.0, 100.0, 0.0, 120.0])),
        repitch.Track(np.arange(2) * 0.01, np.array([140.0, 0.0])),
    ]
    assert repitch.median_hz(tracks) == 120.0
    assert math.isnan(repitch.median_hz([repitch.Track(np.zeros(1), np.zeros(1))]))


def test_the_config_takes_its_own_fields() -> None:
    assert CFG.f0_range_hz == (75, 600)
    assert not hasattr(CFG, "workers")
