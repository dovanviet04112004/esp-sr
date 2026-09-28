"""doa: plane waves land on their grid angle, swapping the microphones mirrors the angle, nothing is claimed before a
search or from silence, and configurations the firmware refuses are refused here too."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from srpipe.dsp import emit_golden
from srpipe.dsp.afe import doa
from srpipe.dsp.spec.stft import analyze_signal
from srpipe.generated import grid

HOPS = 48


def searched(pair: np.ndarray, cfg: doa.DoaConfig | None = None) -> doa.DoaResult:
    bins = [analyze_signal(pair[:, m].astype(np.float32)) for m in range(2)]
    searcher = doa.Doa(cfg)
    result = searcher.result
    for h in range(HOPS):
        result = searcher.process(bins[0][h], bins[1][h], h % 2 == 0)
    return result


@pytest.mark.parametrize("angle_deg", [10.0, 30.0, 60.0, 90.0, 120.0, 150.0, 170.0])
def test_a_plane_wave_lands_within_one_grid_step(angle_deg: float) -> None:
    rng = np.random.default_rng(int(angle_deg))
    pair = emit_golden.plane_wave(rng.uniform(-0.3, 0.3, HOPS * grid.HOP_SAMPLES), angle_deg)
    result = searched(pair)
    assert abs(result.angle_deg - angle_deg) <= doa.DoaConfig().grid_step_deg
    assert 0 < result.confidence <= doa.CONFIDENCE_MAX


def test_swapping_the_microphones_mirrors_the_angle() -> None:
    rng = np.random.default_rng(7)
    pair = emit_golden.plane_wave(rng.uniform(-0.3, 0.3, HOPS * grid.HOP_SAMPLES), 40.0)
    assert searched(pair).angle_deg + searched(pair[:, ::-1]).angle_deg == 180


def test_no_angle_before_a_search_or_from_silence() -> None:
    searcher = doa.Doa()
    zeros = np.zeros(grid.N_BINS, dtype=np.complex64)
    ones = np.ones(grid.N_BINS, dtype=np.complex64)
    assert searcher.process(ones, ones, update=False).angle_deg == doa.ANGLE_UNKNOWN_DEG
    assert doa.Doa().process(zeros, zeros, update=True) == doa.DoaResult(doa.ANGLE_UNKNOWN_DEG, 0)


def test_refuses_what_the_firmware_refuses() -> None:
    for bad in (
        replace(doa.DoaConfig(), grid_step_deg=7.0),
        replace(doa.DoaConfig(), band_min_hz=3010.0, band_max_hz=3020.0),
        replace(doa.DoaConfig(), band_max_hz=9000.0),
        replace(doa.DoaConfig(), smooth_tau_s=0.0),
    ):
        with pytest.raises(ValueError):
            doa.Doa(bad)


def test_the_band_holds_the_bins_of_its_edges() -> None:
    first, last = doa.band_bins(replace(doa.DoaConfig(), band_min_hz=2000.0, band_max_hz=8000.0))
    assert (first, last) == (64, 256)
    assert doa.band_bins(replace(doa.DoaConfig(), band_min_hz=0.0))[0] == 1
