"""One syllable's F0 contour replaced by Praat's overlap-add, formants kept (KEHOACH 3.11): a clip's pitch by Praat,
a syllable's voiced stretch, its contour in semitones over the speaker's median F0 at even points of that stretch, and
the clip resynthesised over the syllable on another contour or on its own. Praat is the praat extra of pyproject."""

from __future__ import annotations

import math
from dataclasses import dataclass, fields

import numpy as np
import parselmouth
from parselmouth.praat import call

from srpipe.generated import grid

FS = grid.SAMPLE_RATE_HZ


@dataclass(frozen=True)
class RepitchConfig:
    """How a syllable is tracked and resynthesised, as the tone_flip section of command_ctc.yaml gives it."""

    points: int  # a contour's even points over a voiced stretch
    voiced_share: float  # fewest of a syllable's frames Praat must call voiced
    min_voiced_s: float
    pad_s: float  # resynthesised on either side of the stretch
    fade_s: float  # crossfade back into the clip at both ends
    time_step_s: float
    f0_range_hz: tuple[float, float]


def repitch_config(spec: dict) -> RepitchConfig:
    """The RepitchConfig of a config section holding its fields among others."""
    picked = {f.name: spec[f.name] for f in fields(RepitchConfig)}
    return RepitchConfig(**picked | {"f0_range_hz": tuple(picked["f0_range_hz"])})


@dataclass(frozen=True)
class Track:
    """Praat's pitch of a clip: frame centres in seconds and F0 in Hz, 0 where Praat calls a frame unvoiced."""

    times_s: np.ndarray
    f0_hz: np.ndarray


def track(x: np.ndarray, cfg: RepitchConfig) -> Track:
    """Praat's autocorrelation pitch of x (float, 16 kHz) at cfg's step and range."""
    pitch = parselmouth.Sound(x.astype(np.float64), sampling_frequency=FS).to_pitch(cfg.time_step_s, *cfg.f0_range_hz)
    return Track(np.asarray(pitch.xs()), np.asarray(pitch.selected_array["frequency"]))


def median_hz(tracks: list[Track]) -> float:
    """The median F0 over every voiced frame of tracks, a speaker's reference; NaN when none is voiced."""
    voiced = np.concatenate([t.f0_hz[t.f0_hz > 0] for t in tracks])
    return float(np.median(voiced)) if len(voiced) else math.nan


def voiced_stretch(t: Track, start_s: float, end_s: float, cfg: RepitchConfig) -> tuple[float, float] | None:
    """From the first to the last voiced frame of a syllable [start_s, end_s); None when Praat calls fewer than
    cfg.voiced_share of its frames voiced, or they span less than cfg.min_voiced_s: creak, or no clean periods."""
    inside = (t.times_s >= start_s) & (t.times_s < end_s)
    voiced = inside & (t.f0_hz > 0)
    if not voiced.any() or voiced.sum() < cfg.voiced_share * inside.sum():
        return None
    first, last = (float(v) for v in t.times_s[voiced][[0, -1]])
    return (first, last) if last - first >= cfg.min_voiced_s else None


def contour_st(t: Track, stretch: tuple[float, float], reference_hz: float, points: int) -> np.ndarray:
    """F0 over stretch in semitones above reference_hz at points even times, read across its voiced frames."""
    voiced = (t.times_s >= stretch[0]) & (t.times_s <= stretch[1]) & (t.f0_hz > 0)
    semitones = 12.0 * np.log2(t.f0_hz[voiced] / reference_hz)
    return np.interp(np.linspace(*stretch, points), t.times_s[voiced], semitones)


def contour_hz(semitones: np.ndarray, reference_hz: float) -> np.ndarray:
    """A contour in semitones above reference_hz, in Hz."""
    return reference_hz * 2.0 ** (semitones / 12.0)


def resynthesised(
    x: np.ndarray, stretch: tuple[float, float], target_hz: np.ndarray | None, cfg: RepitchConfig
) -> np.ndarray:
    """x (float, 16 kHz) through Praat's overlap-add over stretch and cfg.pad_s either side, the pitch points within
    stretch replaced by target_hz at even times, or kept when it is None; crossfaded back into x over cfg.fade_s."""
    a = max(0, round((stretch[0] - cfg.pad_s) * FS))
    b = min(len(x), round((stretch[1] + cfg.pad_s) * FS))
    sound = parselmouth.Sound(x[a:b].astype(np.float64), sampling_frequency=FS)
    manipulation = call(sound, "To Manipulation", cfg.time_step_s, *cfg.f0_range_hz)
    if target_hz is not None:
        tier = call(manipulation, "Extract pitch tier")
        start_s, end_s = stretch[0] - a / FS, stretch[1] - a / FS
        call(tier, "Remove points between", start_s, end_s)
        for at_s, hz in zip(np.linspace(start_s, end_s, len(target_hz)), target_hz, strict=True):
            call(tier, "Add point", float(at_s), float(hz))
        call([tier, manipulation], "Replace pitch tier")
    y = call(manipulation, "Get resynthesis (overlap-add)").values[0]
    y = np.pad(y, (0, max(0, b - a - len(y))))[: b - a]
    fade = round(cfg.fade_s * FS)
    ramp = np.linspace(0.0, 1.0, fade)
    y[:fade] = y[:fade] * ramp + x[a : a + fade] * (1.0 - ramp)
    y[len(y) - fade :] = y[len(y) - fade :] * ramp[::-1] + x[b - fade : b] * (1.0 - ramp[::-1])
    out = x.astype(np.float64)
    out[a:b] = y
    return out
