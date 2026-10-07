"""srpipe.scenes.swift_pitch: SwiftF0's frames on Kaldi's three scales; a first syllable above the next reads above
its reference once the mean looks ahead, and at it when it does not; unvoiced frames bridged; no voiced frame, no pitch;
device.pitch_of keeps Kaldi's tracker unless a pitch_source names SwiftF0; front_of takes the contract's shared keys."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from srpipe.core.config import PITCH_SHARED, contract_front, front_of
from srpipe.dsp.spec.pitch import PitchConfig, PitchTracker
from srpipe.generated import grid
from srpipe.scenes import device, swift_pitch

SPEC = {k: contract_front()["pitch"][k] for k in PITCH_SHARED} | {
    "source": swift_pitch.SOURCE,
    "confidence": 0.5,
    "normalization_right_s": 0.4,
}


@dataclass
class Found:
    pitch_hz: np.ndarray
    confidence: np.ndarray


class Fixed:
    """A detector answering every stream with the same frames, keeping what it was asked."""

    def __init__(self, pitch_hz: np.ndarray, confidence: np.ndarray) -> None:
        self.found = Found(np.asarray(pitch_hz, dtype=float), np.asarray(confidence, dtype=float))
        self.asked: list[tuple] = []

    def detect(self, audio: np.ndarray, rate: float, fmin: float, fmax: float) -> Found:
        self.asked.append((len(audio), audio.dtype, rate, fmin, fmax))
        return self.found


def silent(hops: int) -> np.ndarray:
    return np.zeros(hops * grid.HOP_SAMPLES, dtype=np.int16)


def command() -> tuple[np.ndarray, np.ndarray]:
    """Silence, a first syllable at 200 Hz, its stop closure, a second at 130 Hz, silence; a frame a hop."""
    hz = np.r_[np.full(20, 80.0), np.full(8, 200.0), np.full(6, 90.0), np.full(20, 130.0), np.full(20, 80.0)]
    voiced = np.r_[np.zeros(20), np.ones(8), np.zeros(6), np.ones(20), np.zeros(20)]
    return hz, voiced


def test_a_high_first_syllable_reads_above_its_reference_only_when_the_mean_looks_ahead() -> None:
    hz, voiced = command()
    ahead = swift_pitch.features(silent(len(hz)), SPEC, Fixed(hz, voiced))
    causal = swift_pitch.features(silent(len(hz)), SPEC | {"normalization_right_s": 0.0}, Fixed(hz, voiced))
    assert ahead.shape == (len(hz), 3) and ahead.dtype == np.float32
    assert ahead[20:28, 1].mean() > 0.4
    assert np.abs(causal[20:28, 1]).max() < 1e-6
    assert ahead[34:54, 1].mean() < 0.0


def test_the_voicing_dim_is_kaldis_pov_feature_of_the_confidence() -> None:
    hz, voiced = command()
    out = swift_pitch.features(silent(len(hz)), SPEC, Fixed(hz, voiced))
    assert out[20, 0] == pytest.approx(SPEC["pov_scale"] * ((1.0001 - 1.0) ** 0.15 - 1.0), rel=1e-5)
    assert out[0, 0] == pytest.approx(SPEC["pov_scale"] * (1.0001**0.15 - 1.0), abs=1e-6)


def test_unvoiced_frames_carry_log_f0_drawn_between_the_voiced_ones() -> None:
    hz, voiced = command()
    out = swift_pitch.features(silent(len(hz)), SPEC | {"normalization_right_s": 0.0}, Fixed(hz, voiced))
    bridge = (np.log(130.0) - np.log(200.0)) / 7 * SPEC["delta_pitch_scale"]
    assert out[30:32, 2] == pytest.approx(bridge, rel=1e-5)


def test_a_stream_with_no_voiced_frame_gives_zero_pitch_and_delta() -> None:
    hz = np.full(30, 120.0)
    out = swift_pitch.features(silent(30), SPEC, Fixed(hz, np.full(30, 0.3)))
    assert np.array_equal(out[:, 1:], np.zeros((30, 2), dtype=np.float32))


def test_the_delta_of_a_steady_glide_is_its_slope_on_kaldis_scale() -> None:
    slope = 0.01
    hz = 150.0 * np.exp(slope * np.arange(40))
    out = swift_pitch.features(silent(40), SPEC, Fixed(hz, np.ones(40)))
    assert out[5:35, 2] == pytest.approx(slope * SPEC["delta_pitch_scale"], rel=1e-4)


def test_the_detector_hears_the_whole_hops_in_float_at_the_grid_rate_within_the_f0_range() -> None:
    fixed = Fixed(np.full(12, 150.0), np.ones(12))
    swift_pitch.features(np.zeros(12 * grid.HOP_SAMPLES + 100, dtype=np.int16), SPEC, fixed)
    assert fixed.asked == [
        (12 * grid.HOP_SAMPLES, np.float32, grid.SAMPLE_RATE_HZ, SPEC["min_f0_hz"], SPEC["max_f0_hz"])
    ]


def test_pitch_of_runs_kaldis_tracker_unless_the_spec_names_swiftf0(monkeypatch: pytest.MonkeyPatch) -> None:
    rng = np.random.default_rng(7)
    clean = (rng.standard_normal(40 * grid.HOP_SAMPLES) * 3000).astype(np.int16)
    kaldi = contract_front()["pitch"]
    tracker = PitchTracker(PitchConfig(**kaldi))
    assert np.array_equal(device.pitch_of(kaldi)(clean), device.stream_pitch(tracker, clean))
    fixed = Fixed(np.full(40, 150.0), np.ones(40))
    monkeypatch.setattr(swift_pitch, "detector", lambda: fixed)
    assert np.array_equal(device.pitch_of(SPEC)(clean), swift_pitch.features(clean, SPEC, fixed))


def test_front_of_tracks_pitch_by_a_pitch_source_with_the_contracts_range_left_context_and_scales() -> None:
    source = {"source": swift_pitch.SOURCE, "confidence": 0.5, "normalization_right_s": 0.4}
    front = front_of({"pitch_source": source})
    assert front["features"] == contract_front()["features"]
    assert front["pitch"] == {k: contract_front()["pitch"][k] for k in PITCH_SHARED} | source
    assert front_of({}) == contract_front()
    learnt = {"features": {"n_bands": 40}, "pitch": {}}
    assert front_of({"listen": learnt, "pitch_source": source}) is learnt
