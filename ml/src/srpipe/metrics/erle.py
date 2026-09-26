"""Echo return loss enhancement of aec: how far the canceller lowers the echo (KEHOACH 3.11, 3.15)."""

from __future__ import annotations

import numpy as np

from srpipe.generated import grid
from srpipe.metrics.sisdr import as_pair

ERLE_FRAME_S = 0.1


def power_ratio_db(num: np.ndarray, den: np.ndarray) -> float:
    den_power = float(den @ den)
    if den_power == 0.0:
        return float("inf")
    return float(10.0 * np.log10(float(num @ num) / den_power))


def erle_db(residual: np.ndarray, mic: np.ndarray) -> float:
    """ERLE over the whole run: microphone energy over residual energy, in dB; far end talking alone."""
    res, mic = as_pair(residual, mic)
    return power_ratio_db(mic, res)


def erle_track_db(
    residual: np.ndarray, mic: np.ndarray, frame_s: float = ERLE_FRAME_S, fs_hz: int = grid.SAMPLE_RATE_HZ
) -> np.ndarray:
    """ERLE per frame_s, to see how fast the canceller converges; NaN where the microphone is silent."""
    res, mic = as_pair(residual, mic)
    frame = round(frame_s * fs_hz)
    n_frames = len(mic) // frame
    track = np.full(n_frames, np.nan)
    for i in range(n_frames):
        span = slice(i * frame, (i + 1) * frame)
        if np.any(mic[span]):
            track[i] = power_ratio_db(mic[span], res[span])
    return track
