"""WAV reading and writing with the same int16 scaling the firmware uses (KEHOACH 3.1, 3.14)."""

from __future__ import annotations

import io
import math
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import soundfile as sf
from scipy import signal

from srpipe.generated import grid

INT16_SCALE = 32768.0
PARQUET_AUDIO_COLUMN = "audio"


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


def to_grid_rate(x: np.ndarray, rate_hz: int) -> np.ndarray:
    """Mono float64 at the grid's rate, by polyphase resampling when the rate differs."""
    if rate_hz == grid.SAMPLE_RATE_HZ:
        return x
    step = math.gcd(rate_hz, grid.SAMPLE_RATE_HZ)
    return signal.resample_poly(x, grid.SAMPLE_RATE_HZ // step, rate_hz // step)


class ItemReader:
    """Clean speech by the item name of a split file, mono float64 at the grid's rate.

    An item is a file under raw/ (WAV, FLAC, MP3), or <parquet under raw/>#<row> for corpora packed with an audio
    column of encoded bytes. The last row group read is kept, as a split lists a parquet's rows in order.
    """

    def __init__(self, raw_root: Path) -> None:
        self.raw_root = raw_root
        self._path: Path | None = None
        self._starts = np.zeros(1, dtype=np.int64)
        self._group = -1
        self._audio = None

    def _row_bytes(self, path: Path, row: int) -> bytes:
        if path != self._path:
            meta = pq.ParquetFile(path).metadata
            self._starts = np.cumsum([0] + [meta.row_group(g).num_rows for g in range(meta.num_row_groups)])
            self._path, self._group = path, -1
        if not 0 <= row < self._starts[-1]:
            raise IndexError(f"{path}: row {row} outside 0..{self._starts[-1] - 1}")
        group = int(np.searchsorted(self._starts, row, side="right")) - 1
        if group != self._group:
            self._audio = pq.ParquetFile(path).read_row_group(group, columns=[PARQUET_AUDIO_COLUMN]).column(0)
            self._group = group
        return self._audio[row - self._starts[group]].as_py()["bytes"]

    def read(self, item: str) -> np.ndarray:
        name, _, row = item.partition("#")
        path = self.raw_root / name
        source = io.BytesIO(self._row_bytes(path, int(row))) if row else path
        x, rate = sf.read(source, dtype="float64", always_2d=True)
        return to_grid_rate(x.mean(axis=1), rate)
