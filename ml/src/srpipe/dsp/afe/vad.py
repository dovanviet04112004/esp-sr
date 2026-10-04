"""vad of dsp_afe: the WebRTC VAD in float32, one decision per hop, then a hangover (KEHOACH 3.10).

Mirrors firmware/components/dsp_afe/src/vad.c operation for operation. The design and every number come from WebRTC's
common_audio/vad (firmware/third_party/webrtc_vad, BSD-3) through contracts/afe.yaml; the three approximations it was
tuned on (log2 within an octave, 2^x between powers of two, integer log2 ratios) need only frexp, ldexp and ceil.
"""

from __future__ import annotations

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


def log2_linear(x: np.float32) -> np.float32:
    """log2 of x > 0 with the mantissa taken linearly within its octave, as WebRTC's LogOfEnergy."""
    mantissa, exponent = np.frexp(f32(x))
    return f32(exponent - 1) + (f32(2.0) * mantissa - f32(1.0))


def exp2_linear_neg(t: np.float32) -> np.float32:
    """2^-t for t >= 0 as WebRTC's GaussianProbability takes it: t in steps of 1/density_steps, linear between
    powers of two, and the result rounded down to a whole number of those steps, so it is 0 from 2^-10 on."""
    steps = f32(afe.VAD_DENSITY_STEPS)
    t_steps = np.floor(f32(t) * steps)
    whole = np.ceil(t_steps / steps)
    return f32(np.floor(np.ldexp(steps + (whole * steps - t_steps), -int(whole)))) / steps


def floor_log2(x: np.float32) -> int:
    return int(np.frexp(f32(x))[1]) - 1


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


class _MinimumTracker:
    """The smallest values of the last hops per band, their median smoothed, as WebRTC's FindMinimum."""

    def __init__(self) -> None:
        self.values = np.full((BANDS, afe.VAD_MIN_TRACK_VALUES), afe.VAD_MIN_TRACK_EMPTY_DB, dtype=np.float32)
        self.ages = np.zeros((BANDS, afe.VAD_MIN_TRACK_VALUES), dtype=np.int32)
        self.mean = np.full(BANDS, afe.VAD_MIN_TRACK_START_DB, dtype=np.float32)

    def update(self, band: int, x: np.float32, hops_modelled: int) -> np.float32:
        values, ages = self.values[band], self.ages[band]
        last = afe.VAD_MIN_TRACK_VALUES - 1
        for i in range(afe.VAD_MIN_TRACK_VALUES):
            if ages[i] != afe.VAD_MIN_TRACK_WINDOW_HOPS:
                ages[i] += 1
            else:
                values[i:last] = values[i + 1 :].copy()
                ages[i:last] = ages[i + 1 :].copy()
                ages[last] = afe.VAD_MIN_TRACK_WINDOW_HOPS + 1
                values[last] = afe.VAD_MIN_TRACK_EMPTY_DB
        position = next((i for i in range(afe.VAD_MIN_TRACK_VALUES) if x < values[i]), -1)
        if position >= 0:
            values[position + 1 :] = values[position:last].copy()
            ages[position + 1 :] = ages[position:last].copy()
            values[position] = x
            ages[position] = 1
        median = f32(afe.VAD_MIN_TRACK_START_DB)
        if hops_modelled > afe.VAD_MIN_TRACK_MEDIAN_INDEX:
            median = values[afe.VAD_MIN_TRACK_MEDIAN_INDEX]
        elif hops_modelled > 0:
            median = values[0]
        if hops_modelled == 0:
            keep = f32(afe.VAD_MIN_SMOOTH_FIRST)
        elif median < self.mean[band]:
            keep = f32(afe.VAD_MIN_SMOOTH_DOWN)
        else:
            keep = f32(afe.VAD_MIN_SMOOTH_UP)
        self.mean[band] = keep * self.mean[band] + (f32(1.0) - keep) * median
        return self.mean[band]


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

    def reset(self) -> None:
        self.downsample = _HalfBand(afe.VAD_DOWNSAMPLE_ALLPASS)
        self.splits = [_HalfBand(afe.VAD_SPLIT_ALLPASS) for _ in range(SPLIT_STAGES)]
        self.low_band_hpf = _LowBandHighPass()
        self.tracker = _MinimumTracker()
        self.noise_means = _table(afe.VAD_NOISE_MEANS_DB)
        self.speech_means = _table(afe.VAD_SPEECH_MEANS_DB)
        self.noise_stds = _table(afe.VAD_NOISE_STDS_DB)
        self.speech_stds = _table(afe.VAD_SPEECH_STDS_DB)
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
        total = f32(0.0)
        # WebRTC's order, top band first; the sum only meets min_energy, but the order keeps it bit exact.
        for band, x in (
            (5, band_2_3k),
            (4, band_3_4k),
            (3, band_1_2k),
            (2, band_500_1k),
            (1, band_250_500),
            (0, band_80_250),
        ):
            energy = f32(0.0)
            for v in x:
                energy = energy + v * v
            total = total + energy
            offset = f32(afe.VAD_BAND_OFFSET_DB[band])
            if energy > f32(0.0):
                level = f32(afe.VAD_DB_PER_LOG2) * log2_linear(energy)
                levels[band] = max(level, f32(0.0)) + offset
            else:
                levels[band] = offset
        return levels, total

    def process(self, hop: np.ndarray) -> VadHop:
        """Classify one frame of frame_samples; speech stays on for the hangover after the last raw decision."""
        if len(hop) != self.frame_samples:
            raise ValueError(f"a frame is {self.frame_samples} samples, got {len(hop)}")
        levels, total = self.features(hop)
        raw = False
        if total > f32(afe.VAD_MIN_ENERGY):
            raw = self._decide_and_adapt(levels)
        if raw:
            self.hangover_left = self.hangover_hops
            speech = True
        elif self.hangover_left > 0:
            self.hangover_left -= 1
            speech = True
        else:
            speech = False
        return VadHop(speech, raw, levels)

    def _decide_and_adapt(self, levels: np.ndarray) -> bool:
        noise_w, speech_w = NOISE_WEIGHTS, SPEECH_WEIGHTS
        noise = [
            _likelihoods(x, noise_w[:, b], self.noise_means[:, b], self.noise_stds[:, b]) for b, x in enumerate(levels)
        ]
        speech = [
            _likelihoods(x, speech_w[:, b], self.speech_means[:, b], self.speech_stds[:, b])
            for b, x in enumerate(levels)
        ]
        raw = False
        llr_sum = f32(0.0)
        floor = f32(afe.VAD_LIKELIHOOD_FLOOR)
        for b in range(BANDS):
            llr = floor_log2(max(speech[b].total, floor)) - floor_log2(max(noise[b].total, floor))
            raw = raw or f32(llr) > self.local_threshold
            llr_sum = llr_sum + f32(llr * afe.VAD_SPECTRUM_WEIGHTS[b])
        raw = raw or llr_sum >= self.global_threshold
        for b, x in enumerate(levels):
            self._adapt_band(b, x, raw, noise[b], speech[b], noise_w[:, b], speech_w[:, b])
        self.hops_modelled += 1
        return raw

    def _adapt_band(
        self,
        b: int,
        x: np.float32,
        raw: bool,
        noise: _Likelihoods,
        speech: _Likelihoods,
        noise_w: np.ndarray,
        speech_w: np.ndarray,
    ) -> None:
        one = f32(1.0)
        floor_db = self.tracker.update(b, x, self.hops_modelled)
        noise_global = noise_w[0] * self.noise_means[0, b] + noise_w[1] * self.noise_means[1, b]
        gamma_noise = noise.posteriors(first_when_low=True)
        gamma_speech = speech.posteriors(first_when_low=False)
        for k in range(GAUSSIANS):
            mn, ms = self.noise_means[k, b], self.speech_means[k, b]
            sn, ss = self.noise_stds[k, b], self.speech_stds[k, b]
            moved = mn
            if not raw:
                moved = mn + (gamma_noise[k] * noise.delta[k]) * f32(afe.VAD_NOISE_MEAN_STEP)
            moved = moved + f32(afe.VAD_FLOOR_PULL) * (floor_db - noise_global)
            moved = max(moved, f32(afe.VAD_MEAN_MIN_DB[k]))
            moved = min(moved, f32(afe.VAD_NOISE_GAUSSIAN_MAX_DB[k * BANDS + b]))
            self.noise_means[k, b] = moved
            if raw:
                ms2 = ms + (gamma_speech[k] * speech.delta[k]) * f32(afe.VAD_SPEECH_MEAN_STEP)
                ms2 = max(ms2, f32(afe.VAD_MEAN_MIN_DB[k]))
                self.speech_means[k, b] = min(ms2, f32(afe.VAD_SPEECH_GAUSSIAN_MAX_DB[b]))
                grown = ss + ((gamma_speech[k] * (speech.delta[k] * (x - ms) - one)) / ss) * f32(
                    afe.VAD_SPEECH_STD_STEP
                )
                self.speech_stds[k, b] = max(grown, f32(afe.VAD_MIN_STD_DB))
            else:
                grown = sn + ((gamma_noise[k] * (noise.delta[k] * (x - mn) - one)) / sn) * f32(afe.VAD_NOISE_STD_STEP)
                self.noise_stds[k, b] = max(grown, f32(afe.VAD_MIN_STD_DB))
        self._separate(b, noise_w, speech_w)

    def _separate(self, b: int, noise_w: np.ndarray, speech_w: np.ndarray) -> None:
        """Keep the speech model above the noise model by min_separation_db, and both under their caps."""
        noise_global = noise_w[0] * self.noise_means[0, b] + noise_w[1] * self.noise_means[1, b]
        speech_global = speech_w[0] * self.speech_means[0, b] + speech_w[1] * self.speech_means[1, b]
        gap = speech_global - noise_global
        separation = f32(afe.VAD_MIN_SEPARATION_DB[b])
        if gap < separation:
            short = separation - gap
            self.speech_means[:, b] = self.speech_means[:, b] + f32(afe.VAD_SEPARATION_SPEECH_SHARE) * short
            self.noise_means[:, b] = self.noise_means[:, b] - f32(afe.VAD_SEPARATION_NOISE_SHARE) * short
            noise_global = noise_w[0] * self.noise_means[0, b] + noise_w[1] * self.noise_means[1, b]
            speech_global = speech_w[0] * self.speech_means[0, b] + speech_w[1] * self.speech_means[1, b]
        speech_cap = f32(afe.VAD_SPEECH_MEAN_MAX_DB[b])
        if speech_global > speech_cap:
            self.speech_means[:, b] = self.speech_means[:, b] - (speech_global - speech_cap)
        noise_cap = f32(afe.VAD_NOISE_MEAN_MAX_DB[b])
        if noise_global > noise_cap:
            self.noise_means[:, b] = self.noise_means[:, b] - (noise_global - noise_cap)


@dataclass(frozen=True)
class _Likelihoods:
    """Weighted densities of the two Gaussians of one class in one band, their sum, and (x - m) / s^2 of each."""

    weighted: tuple[np.float32, np.float32]
    delta: tuple[np.float32, np.float32]

    @property
    def total(self) -> np.float32:
        return self.weighted[0] + self.weighted[1]

    def posteriors(self, first_when_low: bool) -> tuple[np.float32, np.float32]:
        """Share of each Gaussian; below posterior_floor the noise class gives all to the first, speech none."""
        total = self.total
        if total >= f32(afe.VAD_POSTERIOR_FLOOR):
            first = self.weighted[0] / total
            return first, f32(1.0) - first
        return (f32(1.0), f32(0.0)) if first_when_low else (f32(0.0), f32(0.0))


def _likelihoods(x: np.float32, weights: np.ndarray, means: np.ndarray, stds: np.ndarray) -> _Likelihoods:
    weighted, delta = [], []
    for k in range(GAUSSIANS):
        inv_std = f32(1.0) / stds[k]
        diff = x - means[k]
        d = (inv_std * inv_std) * diff
        exponent = f32(0.5) * (d * diff)
        density = f32(0.0)
        if exponent < f32(afe.VAD_EXPONENT_LIMIT):
            density = exp2_linear_neg(f32(afe.VAD_LOG2_E) * exponent)
        weighted.append(weights[k] * (inv_std * density))
        delta.append(d)
    return _Likelihoods((weighted[0], weighted[1]), (delta[0], delta[1]))
