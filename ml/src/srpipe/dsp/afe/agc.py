"""agc of dsp_afe: a slow speech-level gain, then a look-ahead limiter (KEHOACH 3.10).

Mirrors firmware/components/dsp_afe/src/agc.c in float32. Constants are made once in double and rounded; each hop then
needs only products, sums, quotients and sqrt, so both sides match bit for bit. gain_db, a report, uses log10.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

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


def lookahead_samples(lookahead_ms: float) -> int:
    return int(np.rint(f32(lookahead_ms) * f32(grid.SAMPLE_RATE_HZ) / f32(MS_PER_S)))


class _Window:
    """The last n values with a count of those under 1: a window of ones needs no min and no sum."""

    def __init__(self, n: int) -> None:
        self.values = deque([f32(1.0)] * n, maxlen=n)
        self.under_one = 0

    def push(self, v: np.float32) -> None:
        if self.values[0] < f32(1.0):
            self.under_one -= 1
        self.values.append(v)
        if v < f32(1.0):
            self.under_one += 1

    def minimum(self) -> np.float32:
        if self.under_one == 0:
            return f32(1.0)
        low = self.values[0]
        for v in self.values:
            low = min(low, v)
        return low

    def mean(self) -> np.float32:
        if self.under_one == 0:
            return f32(1.0)
        total = f32(0.0)
        for v in self.values:
            total = total + v
        return total / f32(len(self.values))


class Agc:
    """Slow gain towards the target speech level, frozen without speech; then a limiter whose output lags by the
    look-ahead and never passes limit_dbfs."""

    def __init__(self, cfg: AgcConfig | None = None) -> None:
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
        self.delay = deque([f32(0.0)] * self.lookahead, maxlen=max(self.lookahead, 1))
        self.need = _Window(self.lookahead + 1)
        self.held = _Window(self.lookahead + 1)
        self.last_held = f32(1.0)

    def set_target(self, target_dbfs: float) -> None:
        """Target speech level in dBFS, square full scale; as NVS afe/agc_target_dbfs sets it."""
        self.target_power = db_to_power(target_dbfs)

    def _slow_gain(self, hop: np.ndarray, speech: bool) -> None:
        if not speech:
            return
        energy = f32(0.0)
        for v in hop:
            energy = energy + v * v
        power = energy / f32(len(hop))
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
        out = np.empty_like(x)
        for i, v in enumerate(x):
            s = self.gain * v
            magnitude = abs(s)
            self.need.push(self.ceiling / magnitude if magnitude > self.ceiling else f32(1.0))
            held = min(self.need.minimum(), self.last_held + self.release_step)
            self.last_held = held
            self.held.push(held)
            if self.lookahead:
                delayed = self.delay[0]
                self.delay.append(s)
            else:
                delayed = s
            out[i] = delayed * self.held.mean()
        return out, f32(20.0) * np.log10(self.gain)
