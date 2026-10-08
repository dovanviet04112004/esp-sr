"""vad of dsp_afe: the WebRTC VAD in float32, one decision per hop, then a hangover (KEHOACH 3.10).

Mirrors firmware/components/dsp_afe/src/vad.c operation for operation. The design and every number come from WebRTC's
common_audio/vad (firmware/third_party/webrtc_vad, BSD-3) through contracts/afe.yaml; the three approximations it was
tuned on (log2 within an octave, 2^x between powers of two, integer log2 ratios) need only frexp, ldexp and ceil.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numba
import numpy as np

from srpipe.generated import afe, grid

f32 = np.float32
BANDS = afe.VAD_BANDS
GAUSSIANS = afe.VAD_GAUSSIANS
MAX_AGGRESSIVENESS = len(afe.VAD_LOCAL_THRESHOLD) - 1
PCM_FULL_SCALE = f32(32768.0)
MS_PER_S = 1000
# Six bands come from four halvings of the 8 kHz signal, itself half the grid rate.
FRAME_MULTIPLE_SAMPLES = 32
SPLIT_STAGES = 5


def _table(values: tuple[float, ...]) -> np.ndarray:
    return np.array(values, dtype=np.float32).reshape(GAUSSIANS, BANDS)


NOISE_WEIGHTS = _table(afe.VAD_NOISE_WEIGHTS)
SPEECH_WEIGHTS = _table(afe.VAD_SPEECH_WEIGHTS)


NOISE_GAUSSIAN_MAX_DB = np.array(afe.VAD_NOISE_GAUSSIAN_MAX_DB, dtype=np.float32)
SPEECH_GAUSSIAN_MAX_DB = np.array(afe.VAD_SPEECH_GAUSSIAN_MAX_DB, dtype=np.float32)
MEAN_MIN_DB = np.array(afe.VAD_MEAN_MIN_DB, dtype=np.float32)
MIN_SEPARATION_DB = np.array(afe.VAD_MIN_SEPARATION_DB, dtype=np.float32)
SPEECH_MEAN_MAX_DB = np.array(afe.VAD_SPEECH_MEAN_MAX_DB, dtype=np.float32)
NOISE_MEAN_MAX_DB = np.array(afe.VAD_NOISE_MEAN_MAX_DB, dtype=np.float32)
BAND_OFFSET_DB = np.array(afe.VAD_BAND_OFFSET_DB, dtype=np.float32)
SPECTRUM_WEIGHTS = np.array(afe.VAD_SPECTRUM_WEIGHTS, dtype=np.int64)
DENSITY_STEPS = f32(afe.VAD_DENSITY_STEPS)
DB_PER_LOG2 = f32(afe.VAD_DB_PER_LOG2)
LOG2_E = f32(afe.VAD_LOG2_E)
EXPONENT_LIMIT = f32(afe.VAD_EXPONENT_LIMIT)
LIKELIHOOD_FLOOR = f32(afe.VAD_LIKELIHOOD_FLOOR)
POSTERIOR_FLOOR = f32(afe.VAD_POSTERIOR_FLOOR)
NOISE_MEAN_STEP = f32(afe.VAD_NOISE_MEAN_STEP)
SPEECH_MEAN_STEP = f32(afe.VAD_SPEECH_MEAN_STEP)
FLOOR_PULL = f32(afe.VAD_FLOOR_PULL)
SPEECH_STD_STEP = f32(afe.VAD_SPEECH_STD_STEP)
NOISE_STD_STEP = f32(afe.VAD_NOISE_STD_STEP)
MIN_STD_DB = f32(afe.VAD_MIN_STD_DB)
SEPARATION_SPEECH_SHARE = f32(afe.VAD_SEPARATION_SPEECH_SHARE)
SEPARATION_NOISE_SHARE = f32(afe.VAD_SEPARATION_NOISE_SHARE)
MIN_TRACK_VALUES = afe.VAD_MIN_TRACK_VALUES
MIN_TRACK_WINDOW_HOPS = afe.VAD_MIN_TRACK_WINDOW_HOPS
MIN_TRACK_EMPTY_DB = f32(afe.VAD_MIN_TRACK_EMPTY_DB)
MIN_TRACK_START_DB = f32(afe.VAD_MIN_TRACK_START_DB)
MIN_TRACK_MEDIAN_INDEX = afe.VAD_MIN_TRACK_MEDIAN_INDEX
MIN_SMOOTH_FIRST = f32(afe.VAD_MIN_SMOOTH_FIRST)
MIN_SMOOTH_DOWN = f32(afe.VAD_MIN_SMOOTH_DOWN)
MIN_SMOOTH_UP = f32(afe.VAD_MIN_SMOOTH_UP)


@numba.njit
def log2_linear(x: np.float32) -> np.float32:
    """log2 of x > 0 with the mantissa taken linearly within its octave, as WebRTC's LogOfEnergy."""
    mantissa, exponent = math.frexp(x)
    return np.float32(exponent - 1) + (np.float32(2.0) * np.float32(mantissa) - np.float32(1.0))


@numba.njit
def exp2_linear_neg(t: np.float32) -> np.float32:
    """2^-t for t >= 0 as WebRTC's GaussianProbability takes it: t in steps of 1/density_steps, linear between
    powers of two, and the result rounded down to a whole number of those steps, so it is 0 from 2^-10 on."""
    t_steps = np.floor(np.float32(t) * DENSITY_STEPS)
    whole = np.ceil(t_steps / DENSITY_STEPS)
    scaled = math.ldexp(DENSITY_STEPS + (whole * DENSITY_STEPS - t_steps), -int(whole))
    return np.float32(np.floor(scaled)) / DENSITY_STEPS


@numba.njit
def floor_log2(x: np.float32) -> int:
    return math.frexp(x)[1] - 1


def hangover_hops(hangover_ms: int, hop_samples: int = grid.HOP_SAMPLES) -> int:
    """Whole hops nearest to hangover_ms, in integer arithmetic as the C side computes it."""
    return (hangover_ms * grid.SAMPLE_RATE_HZ + MS_PER_S * hop_samples // 2) // (MS_PER_S * hop_samples)


@dataclass(frozen=True)
class VadHop:
    """One hop: speech after the hangover, the raw GMM decision, and the six band levels in dB."""

    speech: bool
    raw: bool
    features: np.ndarray


@numba.njit
def _half_band(x: np.ndarray, c: np.ndarray, s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Even samples through the all-pass y = c[0] x + s[0], s[0] = x - c[0] y, odd through c[1] and s[1]: their mean
    is the low half, their half difference the high half, each at half the rate; s left as the last pair leaves it."""
    half = np.float32(0.5)
    high = np.empty(len(x) // 2, dtype=np.float32)
    low = np.empty(len(x) // 2, dtype=np.float32)
    for n in range(len(x) // 2):
        yu = c[0] * x[2 * n] + s[0]
        s[0] = x[2 * n] - c[0] * yu
        yl = c[1] * x[2 * n + 1] + s[1]
        s[1] = x[2 * n + 1] - c[1] * yl
        high[n] = half * yu - half * yl
        low[n] = half * yu + half * yl
    return high, low


class _HalfBand:
    """Even samples through one all-pass, odd through the other: their mean is the low half, their half
    difference the high half, each at half the rate."""

    def __init__(self, coefficients: tuple[float, float]) -> None:
        self.c = np.array(coefficients, dtype=np.float32)
        self.s = np.zeros(2, dtype=np.float32)

    def split(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return _half_band(np.asarray(x, dtype=np.float32), self.c, self.s)


@numba.njit
def _direct_form_one(x: np.ndarray, b: np.ndarray, a: np.ndarray, state: np.ndarray) -> np.ndarray:
    """x through b and a in direct form I; state x1, x2, y1, y2, left as the end of x leaves it."""
    out = np.empty_like(x)
    x1, x2, y1, y2 = state[0], state[1], state[2], state[3]
    for n in range(len(x)):
        xn = x[n]
        y = b[0] * xn + b[1] * x1 + b[2] * x2 - a[0] * y1 - a[1] * y2
        x2, x1 = x1, xn
        y2, y1 = y1, y
        out[n] = y
    state[0], state[1], state[2], state[3] = x1, x2, y1, y2
    return out


class _LowBandHighPass:
    """80 Hz high-pass at 500 Hz in direct form I, WebRTC's filter of the lowest band."""

    def __init__(self) -> None:
        self.b = np.array(afe.VAD_LOW_BAND_HPF_B, dtype=np.float32)
        self.a = np.array(afe.VAD_LOW_BAND_HPF_A, dtype=np.float32)
        self.state = np.zeros(4, dtype=np.float32)

    def run(self, x: np.ndarray) -> np.ndarray:
        return _direct_form_one(np.asarray(x, dtype=np.float32), self.b, self.a, self.state)


@numba.njit
def _track_minimum(values: np.ndarray, ages: np.ndarray, means: np.ndarray, band: int, x: np.float32, hops: int):
    """The smallest values of the last hops of one band, their median smoothed into means[band], as WebRTC's
    FindMinimum; values and ages rows of that band, rising, as it keeps them."""
    v, a = values[band], ages[band]
    last = MIN_TRACK_VALUES - 1
    for i in range(MIN_TRACK_VALUES):
        if a[i] != MIN_TRACK_WINDOW_HOPS:
            a[i] += 1
        else:
            for j in range(i, last):
                v[j] = v[j + 1]
                a[j] = a[j + 1]
            a[last] = MIN_TRACK_WINDOW_HOPS + 1
            v[last] = MIN_TRACK_EMPTY_DB
    position = -1
    for i in range(MIN_TRACK_VALUES):
        if x < v[i]:
            position = i
            break
    if position >= 0:
        for j in range(last, position, -1):
            v[j] = v[j - 1]
            a[j] = a[j - 1]
        v[position] = x
        a[position] = 1
    median = MIN_TRACK_START_DB
    if hops > MIN_TRACK_MEDIAN_INDEX:
        median = v[MIN_TRACK_MEDIAN_INDEX]
    elif hops > 0:
        median = v[0]
    if hops == 0:
        keep = MIN_SMOOTH_FIRST
    elif median < means[band]:
        keep = MIN_SMOOTH_DOWN
    else:
        keep = MIN_SMOOTH_UP
    means[band] = keep * means[band] + (np.float32(1.0) - keep) * median
    return means[band]


@numba.njit
def _band_energy(x: np.ndarray) -> np.float32:
    energy = np.float32(0.0)
    for v in x:
        energy = energy + v * v
    return energy


@numba.njit
def _band_levels(bands: tuple, levels: np.ndarray) -> np.float32:
    """levels[band] in dB for the band arrays given top band first, WebRTC's order; their summed energy."""
    total = np.float32(0.0)
    for i in range(BANDS):
        band = BANDS - 1 - i
        energy = _band_energy(bands[i])
        total = total + energy
        if energy > np.float32(0.0):
            levels[band] = max(DB_PER_LOG2 * log2_linear(energy), np.float32(0.0)) + BAND_OFFSET_DB[band]
        else:
            levels[band] = BAND_OFFSET_DB[band]
    return total


@numba.njit
def _likelihoods(x: np.float32, weights: np.ndarray, means: np.ndarray, stds: np.ndarray) -> np.ndarray:
    """Weighted densities of the two Gaussians of one class in one band, then (x - m) / s^2 of each."""
    out = np.empty(2 * GAUSSIANS, dtype=np.float32)
    for k in range(GAUSSIANS):
        inv_std = np.float32(1.0) / stds[k]
        diff = x - means[k]
        d = (inv_std * inv_std) * diff
        exponent = np.float32(0.5) * (d * diff)
        density = np.float32(0.0)
        if exponent < EXPONENT_LIMIT:
            density = exp2_linear_neg(LOG2_E * exponent)
        out[k] = weights[k] * (inv_std * density)
        out[GAUSSIANS + k] = d
    return out


@numba.njit
def _posteriors(weighted: np.ndarray, first_when_low: bool):
    """Share of each Gaussian; below posterior_floor the noise class gives all to the first, speech none."""
    total = weighted[0] + weighted[1]
    if total >= POSTERIOR_FLOOR:
        first = weighted[0] / total
        return first, np.float32(1.0) - first
    if first_when_low:
        return np.float32(1.0), np.float32(0.0)
    return np.float32(0.0), np.float32(0.0)


@numba.njit
def _separate(b: int, noise_means: np.ndarray, speech_means: np.ndarray) -> None:
    """Keep the speech model above the noise model by min_separation_db, and both under their caps."""
    noise_global = NOISE_WEIGHTS[0, b] * noise_means[0, b] + NOISE_WEIGHTS[1, b] * noise_means[1, b]
    speech_global = SPEECH_WEIGHTS[0, b] * speech_means[0, b] + SPEECH_WEIGHTS[1, b] * speech_means[1, b]
    gap = speech_global - noise_global
    if gap < MIN_SEPARATION_DB[b]:
        short = MIN_SEPARATION_DB[b] - gap
        for k in range(GAUSSIANS):
            speech_means[k, b] = speech_means[k, b] + SEPARATION_SPEECH_SHARE * short
        for k in range(GAUSSIANS):
            noise_means[k, b] = noise_means[k, b] - SEPARATION_NOISE_SHARE * short
        noise_global = NOISE_WEIGHTS[0, b] * noise_means[0, b] + NOISE_WEIGHTS[1, b] * noise_means[1, b]
        speech_global = SPEECH_WEIGHTS[0, b] * speech_means[0, b] + SPEECH_WEIGHTS[1, b] * speech_means[1, b]
    if speech_global > SPEECH_MEAN_MAX_DB[b]:
        for k in range(GAUSSIANS):
            speech_means[k, b] = speech_means[k, b] - (speech_global - SPEECH_MEAN_MAX_DB[b])
    if noise_global > NOISE_MEAN_MAX_DB[b]:
        for k in range(GAUSSIANS):
            noise_means[k, b] = noise_means[k, b] - (noise_global - NOISE_MEAN_MAX_DB[b])


@numba.njit
def _decide_and_adapt(
    levels: np.ndarray,
    models: tuple,
    tracker: tuple,
    hops: int,
    local_threshold: np.float32,
    global_threshold: np.float32,
):
    """The raw GMM decision on one hop's levels, then each band's models adapted to it; models are the noise and
    speech means and deviations (gaussians, bands), tracker the minimum tracker's values, ages and means."""
    noise_means, speech_means, noise_stds, speech_stds = models
    noise = np.empty((BANDS, 2 * GAUSSIANS), dtype=np.float32)
    speech = np.empty((BANDS, 2 * GAUSSIANS), dtype=np.float32)
    for b in range(BANDS):
        noise[b] = _likelihoods(levels[b], NOISE_WEIGHTS[:, b], noise_means[:, b], noise_stds[:, b])
        speech[b] = _likelihoods(levels[b], SPEECH_WEIGHTS[:, b], speech_means[:, b], speech_stds[:, b])
    raw = False
    llr_sum = np.float32(0.0)
    for b in range(BANDS):
        speech_total = max(speech[b, 0] + speech[b, 1], LIKELIHOOD_FLOOR)
        noise_total = max(noise[b, 0] + noise[b, 1], LIKELIHOOD_FLOOR)
        llr = floor_log2(speech_total) - floor_log2(noise_total)
        raw = raw or np.float32(llr) > local_threshold
        llr_sum = llr_sum + np.float32(llr * SPECTRUM_WEIGHTS[b])
    raw = raw or llr_sum >= global_threshold
    one = np.float32(1.0)
    for b in range(BANDS):
        x = levels[b]
        floor_db = _track_minimum(tracker[0], tracker[1], tracker[2], b, x, hops)
        noise_global = NOISE_WEIGHTS[0, b] * noise_means[0, b] + NOISE_WEIGHTS[1, b] * noise_means[1, b]
        gamma_noise = _posteriors(noise[b], True)
        gamma_speech = _posteriors(speech[b], False)
        for k in range(GAUSSIANS):
            mn, ms = noise_means[k, b], speech_means[k, b]
            sn, ss = noise_stds[k, b], speech_stds[k, b]
            moved = mn
            if not raw:
                moved = mn + (gamma_noise[k] * noise[b, GAUSSIANS + k]) * NOISE_MEAN_STEP
            moved = moved + FLOOR_PULL * (floor_db - noise_global)
            moved = max(moved, MEAN_MIN_DB[k])
            moved = min(moved, NOISE_GAUSSIAN_MAX_DB[k * BANDS + b])
            noise_means[k, b] = moved
            if raw:
                ms2 = ms + (gamma_speech[k] * speech[b, GAUSSIANS + k]) * SPEECH_MEAN_STEP
                ms2 = max(ms2, MEAN_MIN_DB[k])
                speech_means[k, b] = min(ms2, SPEECH_GAUSSIAN_MAX_DB[b])
                grown = ss + ((gamma_speech[k] * (speech[b, GAUSSIANS + k] * (x - ms) - one)) / ss) * SPEECH_STD_STEP
                speech_stds[k, b] = max(grown, MIN_STD_DB)
            else:
                grown = sn + ((gamma_noise[k] * (noise[b, GAUSSIANS + k] * (x - mn) - one)) / sn) * NOISE_STD_STEP
                noise_stds[k, b] = max(grown, MIN_STD_DB)
        _separate(b, noise_means, speech_means)
    return raw


class Vad:
    """WebRTC's GMM detector on one hop at a time, then the plan's hangover; starts from WebRTC's initial model."""

    def __init__(
        self,
        aggressiveness: int = 0,
        hangover_ms: int = afe.VAD_HANGOVER_MS,
        frame_samples: int = grid.HOP_SAMPLES,
    ) -> None:
        if not 0 <= aggressiveness <= MAX_AGGRESSIVENESS:
            raise ValueError(f"aggressiveness {aggressiveness} outside 0 .. {MAX_AGGRESSIVENESS}")
        if frame_samples % FRAME_MULTIPLE_SAMPLES:
            raise ValueError(f"frame of {frame_samples} samples is not a multiple of {FRAME_MULTIPLE_SAMPLES}")
        self.frame_samples = frame_samples
        self.local_threshold = f32(afe.VAD_LOCAL_THRESHOLD[aggressiveness])
        self.global_threshold = f32(afe.VAD_GLOBAL_THRESHOLD[aggressiveness])
        self.hangover_hops = hangover_hops(hangover_ms, frame_samples)
        self.reset()

    def flush(self) -> None:
        """Clear the band-split filters, the only state that holds samples, as after a short gap; the models, the
        minimum tracks and the hangover stay (KEHOACH 4.5.5)."""
        self.downsample = _HalfBand(afe.VAD_DOWNSAMPLE_ALLPASS)
        self.splits = [_HalfBand(afe.VAD_SPLIT_ALLPASS) for _ in range(SPLIT_STAGES)]
        self.low_band_hpf = _LowBandHighPass()

    def reset(self) -> None:
        self.flush()
        self.tracker = (
            np.full((BANDS, MIN_TRACK_VALUES), MIN_TRACK_EMPTY_DB, dtype=np.float32),
            np.zeros((BANDS, MIN_TRACK_VALUES), dtype=np.int32),
            np.full(BANDS, MIN_TRACK_START_DB, dtype=np.float32),
        )
        self.models = (
            _table(afe.VAD_NOISE_MEANS_DB),
            _table(afe.VAD_SPEECH_MEANS_DB),
            _table(afe.VAD_NOISE_STDS_DB),
            _table(afe.VAD_SPEECH_STDS_DB),
        )
        self.hops_modelled = 0
        self.hangover_left = 0

    def features(self, hop: np.ndarray) -> tuple[np.ndarray, np.float32]:
        """Six band levels in dB and the summed band energy, from one hop of float samples in -1 .. 1."""
        pcm = np.asarray(hop, dtype=np.float32) * PCM_FULL_SCALE
        narrow = self.downsample.split(pcm)[1]
        high_2k, low_2k = self.splits[0].split(narrow)
        # The 2-4 kHz half comes out mirrored, so its high output is 2-3 kHz: WebRTC's band 5, whatever its notes say.
        band_2_3k, band_3_4k = self.splits[1].split(high_2k)
        band_1_2k, low_1k = self.splits[2].split(low_2k)
        band_500_1k, low_500 = self.splits[3].split(low_1k)
        band_250_500, low_250 = self.splits[4].split(low_500)
        band_80_250 = self.low_band_hpf.run(low_250)
        levels = np.empty(BANDS, dtype=np.float32)
        top_first = (band_2_3k, band_3_4k, band_1_2k, band_500_1k, band_250_500, band_80_250)
        return levels, _band_levels(top_first, levels)

    def process(self, hop: np.ndarray) -> VadHop:
        """Classify one frame of frame_samples; speech stays on for the hangover after the last raw decision."""
        if len(hop) != self.frame_samples:
            raise ValueError(f"a frame is {self.frame_samples} samples, got {len(hop)}")
        levels, total = self.features(hop)
        raw = False
        if total > f32(afe.VAD_MIN_ENERGY):
            raw = _decide_and_adapt(
                levels, self.models, self.tracker, self.hops_modelled, self.local_threshold, self.global_threshold
            )
            self.hops_modelled += 1
        if raw:
            self.hangover_left = self.hangover_hops
            speech = True
        elif self.hangover_left > 0:
            self.hangover_left -= 1
            speech = True
        else:
            speech = False
        return VadHop(speech, raw, levels)
