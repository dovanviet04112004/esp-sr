"""WAV reading and writing with the same int16 scaling the firmware uses (KEHOACH 3.1, 3.14)."""

from __future__ import annotations

import io
import math
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import soundfile as sf
from scipy import signal

from srpipe.generated import grid

INT16_SCALE = 32768.0
PARQUET_AUDIO_COLUMN = "audio"
SPANS_DIR = Path("interim") / "spans"  # under the data root: spans decoded once (KEHOACH 1.2)


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


def ramped(x: np.ndarray, ramp_s: float) -> np.ndarray:
    """x faded in and out over ramp_s at the grid's rate by a raised cosine: a cut starts and ends without a step."""
    n = min(round(ramp_s * grid.SAMPLE_RATE_HZ), len(x) // 2)
    if n == 0:
        return x
    ramp = 0.5 - 0.5 * np.cos(np.pi * (np.arange(n) + 0.5) / n)
    y = np.array(x, dtype=np.float64)
    y[:n] *= ramp
    y[len(y) - n :] *= ramp[::-1]
    return y


def to_grid_rate(x: np.ndarray, rate_hz: int) -> np.ndarray:
    """Mono float64 at the grid's rate, by polyphase resampling when the rate differs."""
    if rate_hz == grid.SAMPLE_RATE_HZ:
        return x
    step = math.gcd(rate_hz, grid.SAMPLE_RATE_HZ)
    return signal.resample_poly(x, grid.SAMPLE_RATE_HZ // step, rate_hz // step)


class ItemReader:
    """Clean speech by the item name of a split file, mono float64 at the grid's rate.

    An item is a file under raw/ (WAV, FLAC, MP3), or <parquet under raw/>#<row> for corpora packed with an audio
    column of encoded bytes; either may end in @<start>-<end>, that span of it in seconds. A file not under raw/ is read
    from the data root's interim/spans/, where spans are decoded once. Parquet rows stream from the last one read.
    """

    def __init__(self, raw_root: Path) -> None:
        self.raw_root = raw_root
        self._path: Path | None = None
        self._file: pq.ParquetFile | None = None
        self._starts = np.zeros(1, dtype=np.int64)
        self._rows: Iterator[pa.RecordBatch] | None = None
        self._next = 0
        self._last: tuple[int, bytes] = (-1, b"")

    def _row_bytes(self, path: Path, row: int) -> bytes:
        if path != self._path:
            self._file = pq.ParquetFile(path)
            meta = self._file.metadata
            self._starts = np.cumsum([0] + [meta.row_group(g).num_rows for g in range(meta.num_row_groups)])
            self._path, self._rows, self._last = path, None, (-1, b"")
        if not 0 <= row < self._starts[-1]:
            raise IndexError(f"{path}: row {row} outside 0..{self._starts[-1] - 1}")
        if row == self._last[0]:
            return self._last[1]
        group = int(np.searchsorted(self._starts, row, side="right")) - 1
        if self._rows is None or row < self._next or self._starts[group] > self._next:
            groups = list(range(group, len(self._starts) - 1))
            self._rows = None
            pa.default_memory_pool().release_unused()
            self._rows = self._file.iter_batches(batch_size=1, row_groups=groups, columns=[PARQUET_AUDIO_COLUMN])
            self._next = int(self._starts[group])
        while self._next <= row:
            value = next(self._rows).column(0)[0].as_py()
            self._next += 1
        self._last = (row, value["bytes"])
        return self._last[1]

    def native(self, item: str) -> tuple[np.ndarray, int]:
        """Mono float64 at the item's own rate, and that rate."""
        whole, _, span = item.partition("@")
        name, _, row = whole.partition("#")
        path = self.raw_root / name
        if not row and not path.exists() and (self.raw_root.parent / SPANS_DIR / name).exists():
            path = self.raw_root.parent / SPANS_DIR / name
        source = io.BytesIO(self._row_bytes(path, int(row))) if row else path
        x, rate = sf.read(source, dtype="float64", always_2d=True)
        x = x.mean(axis=1)
        if span:
            start_s, end_s = (float(v) for v in span.split("-"))
            x = x[round(start_s * rate) : round(end_s * rate)]
        return x, rate

    def read(self, item: str) -> np.ndarray:
        return to_grid_rate(*self.native(item))
