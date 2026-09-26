"""WAV reading and writing with the same int16 scaling the firmware uses (KEHOACH 3.1, 3.14)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from srpipe.generated import grid

INT16_SCALE = 32768.0


def to_float(pcm: np.ndarray) -> np.ndarray:
    """int16 samples to float32 in [-1, 1), dividing by 32768 exactly as the C side does."""
    return (pcm.astype(np.float32) / INT16_SCALE).astype(np.float32)


def to_int16(x: np.ndarray) -> np.ndarray:
    """float samples to int16 with rounding and saturation, the inverse of to_float."""
    return np.clip(np.round(np.asarray(x, dtype=np.float64) * INT16_SCALE), -32768, 32767).astype(np.int16)


def read_wav(path: Path, expect_rate_hz: int | None = grid.SAMPLE_RATE_HZ) -> tuple[np.ndarray, int]:
    """Samples as float32 shaped (frames, channels), plus the file's rate; refuse a rate other than expected."""
    pcm, rate = sf.read(str(path), dtype="int16", always_2d=True)
    if expect_rate_hz is not None and rate != expect_rate_hz:
        raise ValueError(f"{path}: {rate} Hz, expected {expect_rate_hz} Hz")
    return to_float(pcm), rate


def write_wav(path: Path, x: np.ndarray, rate_hz: int = grid.SAMPLE_RATE_HZ) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), to_int16(x), rate_hz, subtype="PCM_16")
