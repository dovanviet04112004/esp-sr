"""Three pitch features of a stream from SwiftF0 (Nieradzik 2025), on the scales of Kaldi's: the pitch_source trial of
KEHOACH 3.11, whose runs never deploy, since the board tracks pitch with Kaldi's online tracker."""

from __future__ import annotations

from typing import Any

import numpy as np

from srpipe.core.audio_io import to_float
from srpipe.generated import grid

SOURCE = "swiftf0"
DELTA_WINDOW = 2  # frames each side, as Kaldi's delta


def detector() -> Any:
    """SwiftF0 on one thread that sleeps between calls: one per simulation worker."""
    import swift_f0  # the swiftf0 extra of pyproject.toml

    return swift_f0.SwiftF0(threads=1, spin=False)


def pov_feature(confidence: np.ndarray) -> np.ndarray:
    """Kaldi's POV feature of an NCCF, (1.0001 - c)^0.15 - 1, of SwiftF0's confidence."""
    return (1.0001 - np.clip(confidence, -1.0, 1.0)) ** 0.15 - 1.0


def windowed_mean(values: np.ndarray, weights: np.ndarray, left: int, right: int) -> tuple[np.ndarray, np.ndarray]:
    """Per frame the weighted mean of values over frames [t - left, t + right] and the weight it holds."""
    held = np.concatenate([[0.0], np.cumsum(weights)])
    summed = np.concatenate([[0.0], np.cumsum(weights * values)])
    t = np.arange(len(values))
    low, high = np.maximum(0, t - left), np.minimum(len(values), t + right + 1)
    weight = held[high] - held[low]
    return (summed[high] - summed[low]) / np.where(weight > 0, weight, 1.0), weight


def features(clean: np.ndarray, spec: dict, detect: Any) -> np.ndarray:
    """(hops, 3) float32 of a stream's clean int16 samples, a row per grid hop as SwiftF0 gives a frame per hop: POV
    of the confidence, log F0 less its confidence-weighted mean over the voiced frames normalization_left_s before
    and normalization_right_s after, and its delta; Kaldi's three scales. Unvoiced frames carry log F0 drawn straight
    between the voiced ones; a stream with no voiced frame gives zero pitch and delta."""
    hops = len(clean) // grid.HOP_SAMPLES
    found = detect.detect(
        to_float(clean[: hops * grid.HOP_SAMPLES]), grid.SAMPLE_RATE_HZ, spec["min_f0_hz"], spec["max_f0_hz"]
    )
    confidence = np.asarray(found.confidence[:hops], dtype=np.float64)
    voiced = confidence >= spec["confidence"]
    if not voiced.any():
        pitch_dims = np.zeros((hops, 2))
    else:
        at = np.flatnonzero(voiced)
        log_f0 = np.interp(np.arange(hops), at, np.log(np.asarray(found.pitch_hz[:hops], dtype=np.float64)[at]))
        hops_per_s = grid.SAMPLE_RATE_HZ / grid.HOP_SAMPLES
        left = round(spec["normalization_left_s"] * hops_per_s)
        right = round(spec["normalization_right_s"] * hops_per_s)
        mean, weight = windowed_mean(log_f0, np.where(voiced, confidence, 0.0), left, right)
        normalised = np.where(weight > 0, log_f0 - mean, 0.0) * spec["pitch_scale"]
        padded = np.pad(log_f0, DELTA_WINDOW, mode="edge")
        taps = np.arange(-DELTA_WINDOW, DELTA_WINDOW + 1)
        delta = sum(j * padded[DELTA_WINDOW + j : DELTA_WINDOW + j + hops] for j in taps) / np.sum(taps * taps)
        pitch_dims = np.stack([normalised, delta * spec["delta_pitch_scale"]], axis=1)
    pov = pov_feature(confidence)[:, None] * spec["pov_scale"]
    return np.concatenate([pov, pitch_dims], axis=1).astype(np.float32)
