"""agc of dsp_afe: a slow speech-level gain, then a look-ahead limiter (KEHOACH 3.10).

Mirrors firmware/components/dsp_afe/src/agc.c in float32. Constants are made once in double and rounded; each hop then
needs only products, sums, quotients and sqrt, so both sides match bit for bit. gain_db, a report, uses log10.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numba
import numpy as np

from srpipe.generated import afe, grid

f32 = np.float32
MS_PER_S = 1000.0


@dataclass(frozen=True)
class AgcConfig:
    """Fields of dsp_afe_agc_config_t; defaults from contracts/afe.yaml."""

    target_dbfs: float = afe.AGC_TARGET_DBFS
    gain_min_db: float = afe.AGC_GAIN_MIN_DB
    gain_max_db: float = afe.AGC_GAIN_MAX_DB
    level_tau_s: float = afe.AGC_LEVEL_TAU_S
    level_gate_db: float = afe.AGC_LEVEL_GATE_DB
    level_fall_db_per_s: float = afe.AGC_LEVEL_FALL_DB_PER_S
    up_db_per_s: float = afe.AGC_UP_DB_PER_S
    down_db_per_s: float = afe.AGC_DOWN_DB_PER_S
    limit_dbfs: float = afe.AGC_LIMIT_DBFS
    lookahead_ms: float = afe.AGC_LOOKAHEAD_MS
    release_ms: float = afe.AGC_RELEASE_MS


def db_to_amplitude(db: float) -> np.float32:
    """10^(db/20) in double, rounded once to float32, as agc.c computes its constants."""
    return f32(10.0 ** (float(f32(db)) / 20.0))


def db_to_power(db: float) -> np.float32:
    """10^(db/10) in double, rounded once to float32."""
    return f32(10.0 ** (float(f32(db)) / 10.0))


def log10_f32(x: float) -> np.float32:
    """log10 in double, rounded once to float32: a correctly rounded log10f, the same on every CPU.

    numpy's float32 log10 takes another SIMD path on AVX-512 machines and moves the last bit there.
    """
    return f32(math.log10(float(x)))


def lookahead_samples(lookahead_ms: float) -> int:
    return int(np.rint(f32(lookahead_ms) * f32(grid.SAMPLE_RATE_HZ) / f32(MS_PER_S)))


@numba.njit
def _energy(hop: np.ndarray) -> np.float32:
    """Sum of squares in float32, sample by sample, as agc.c sums it."""
    energy = np.float32(0.0)
    for v in hop:
        energy = energy + v * v
    return energy


@numba.njit
def _limit(
    x: np.ndarray,
    gain: np.float32,
    ceiling: np.float32,
    release_step: np.float32,
    need: np.ndarray,
    need_at: np.ndarray,
    held: np.ndarray,
    delay: np.ndarray,
    ints: np.ndarray,
    floats: np.ndarray,
) -> np.ndarray:
    """x scaled by gain and limited sample by sample, lagging by the look-ahead, as agc.c loops. need, need_at: the
    sliding minimum of the last n wanted gains, a ring of rising values and their sample counts from ints[0], ints[1]
    of them: O(1) a sample on average, the scan's minimum since min rounds nothing. held: the last n held gains, oldest
    at ints[4], ints[3] under one, summed afresh each hop so rounding cannot build up, mean exactly 1 while all are 1.
    delay: the look-ahead's samples from ints[5]. ints[2] counts samples; floats: last held gain, running sum."""
    n = len(held)
    ring = len(need)
    one = np.float32(1.0)
    total = np.float32(0.0)
    for j in range(n):
        total = total + held[(ints[4] + j) % n]
    floats[1] = total
    out = np.empty_like(x)
    for i in range(len(x)):
        s = gain * x[i]
        magnitude = abs(s)
        wanted = ceiling / magnitude if magnitude > ceiling else one
        head, size, count = ints[0], ints[1], ints[2]
        while size > 0 and need[(head + size - 1) % ring] >= wanted:
            size -= 1
        slot = (head + size) % ring
        need[slot] = wanted
        need_at[slot] = count
        size += 1
        if need_at[head] <= count - n:
            head = (head + 1) % ring
            size -= 1
        count += 1
        ints[0], ints[1], ints[2] = head, size, count
        # The window starts full of ones, so until n pushes its minimum is at most 1.
        lowest = min(need[head], one) if count < n else need[head]
        kept = min(lowest, floats[0] + release_step)
        floats[0] = kept
        at = ints[4]
        oldest = held[at]
        if oldest < one:
            ints[3] -= 1
        held[at] = kept
        ints[4] = (at + 1) % n
        if kept < one:
            ints[3] += 1
        floats[1] = (floats[1] - oldest) + kept
        mean = one if ints[3] == 0 else floats[1] / np.float32(n)
        if n > 1:
            at = ints[5]
            delayed = delay[at]
            delay[at] = s
            ints[5] = (at + 1) % (n - 1)
        else:
            delayed = s
        out[i] = delayed * mean
    return out


class Agc:
    """Slow gain towards the target speech level, frozen without speech; then a limiter whose output lags by the
    look-ahead and never passes limit_dbfs. start_db, for the board simulation, is the gain it starts from with a
    speech level to match, as a board that heard speech before; None starts as the firmware does."""

    def __init__(self, cfg: AgcConfig | None = None, start_db: float | None = None) -> None:
        cfg = cfg or AgcConfig()
        if not (cfg.target_dbfs <= 0 and cfg.gain_min_db <= cfg.gain_max_db and cfg.level_tau_s > 0) or not (
            cfg.level_gate_db > 0 and cfg.level_fall_db_per_s >= 0
        ):
            raise ValueError(f"bad agc configuration {cfg}")
        if not (cfg.up_db_per_s > 0 and cfg.down_db_per_s > 0 and cfg.limit_dbfs <= 0 and cfg.release_ms > 0):
            raise ValueError(f"bad agc configuration {cfg}")
        hop_s = grid.HOP_SAMPLES / grid.SAMPLE_RATE_HZ
        self.level_keep = f32(math.exp(-hop_s / float(f32(cfg.level_tau_s))))
        self.up = db_to_amplitude(float(f32(cfg.up_db_per_s)) * hop_s)
        self.down = db_to_amplitude(-float(f32(cfg.down_db_per_s)) * hop_s)
        self.gate = db_to_power(-cfg.level_gate_db)
        self.fall = db_to_power(-float(f32(cfg.level_fall_db_per_s)) * hop_s)
        self.gain_min = db_to_amplitude(cfg.gain_min_db)
        self.gain_max = db_to_amplitude(cfg.gain_max_db)
        self.ceiling = db_to_amplitude(cfg.limit_dbfs)
        self.lookahead = lookahead_samples(cfg.lookahead_ms)
        self.release_step = f32(1.0 / (float(f32(cfg.release_ms)) * grid.SAMPLE_RATE_HZ / MS_PER_S))
        self.set_target(cfg.target_dbfs)
        # Start the level at the target less the most gain, so quiet speech passes the gate at once.
        self.speech_power = self.target_power / (self.gain_max * self.gain_max)
        self.gain = f32(1.0)
        if start_db is not None:
            self.gain = db_to_amplitude(start_db)
            self.speech_power = self.target_power / (self.gain * self.gain)
        self.flush()

    def flush(self) -> None:
        """Empty the look-ahead and release the limiter, the state that holds samples, as after a short gap; the slow
        gain and the speech level stay (KEHOACH 4.5.5)."""
        n = self.lookahead + 1
        # One slot more than the window: a new value lands while the one it pushes out still sits there.
        self.need = np.zeros(n + 1, dtype=np.float32)
        self.need_at = np.zeros(n + 1, dtype=np.int64)
        self.held = np.ones(n, dtype=np.float32)
        self.delay = np.zeros(max(self.lookahead, 1), dtype=np.float32)
        self.ints = np.zeros(6, dtype=np.int64)
        self.floats = np.array([1.0, n], dtype=np.float32)

    def set_target(self, target_dbfs: float) -> None:
        """Target speech level in dBFS, square full scale; as NVS afe/agc_target_dbfs sets it."""
        self.target_power = db_to_power(target_dbfs)

    def _slow_gain(self, hop: np.ndarray, speech: bool) -> None:
        if not speech:
            return
        power = _energy(hop) / f32(len(hop))
        if power >= self.gate * self.speech_power:
            self.speech_power = self.level_keep * self.speech_power + (f32(1.0) - self.level_keep) * power
        else:
            self.speech_power = self.fall * self.speech_power
        wanted = self.gain_max
        if self.speech_power > f32(0.0):
            wanted = f32(np.sqrt(self.target_power / self.speech_power))
        wanted = min(max(wanted, self.gain_min), self.gain_max)
        if self.gain < wanted:
            self.gain = min(self.gain * self.up, wanted)
        elif self.gain > wanted:
            self.gain = max(self.gain * self.down, wanted)

    def process(self, hop: np.ndarray, speech: bool) -> tuple[np.ndarray, np.float32]:
        """One hop scaled and limited, lagging by the look-ahead, and the slow gain applied to it in dB."""
        x = np.asarray(hop, dtype=np.float32)
        self._slow_gain(x, speech)
        out = _limit(
            x,
            self.gain,
            self.ceiling,
            self.release_step,
            self.need,
            self.need_at,
            self.held,
            self.delay,
            self.ints,
            self.floats,
        )
        return out, f32(20.0) * log10_f32(self.gain)
