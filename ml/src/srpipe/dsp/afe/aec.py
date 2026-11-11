"""aec of dsp_afe: multidelay block frequency-domain echo canceller, the step set by the residual (KEHOACH 3.5).

Mirror of aec.c in float32. Overlap-save on blocks of one hop with a transform of two, a filter of n_partitions
blocks per microphone, one partition's gradient constrained per block, and the learning rate of Valin 2007 taken
from how much of the error the echo estimate explains, so no double-talk detector is needed. The residual echo is
the larger of the filter's estimate and the learned speaker-to-mic coupling times the reference. Sums run in order.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from srpipe.dsp.afe.ns_omlsa import PI, cos_series
from srpipe.dsp.spec import fft
from srpipe.generated import afe, grid

f32 = np.float32
HOP = grid.HOP_SAMPLES
N_FFT = 2 * HOP
N_BINS = HOP + 1
ONE = f32(1.0)


@dataclass(frozen=True)
class AecConfig:
    """Field for field as dsp_afe_aec_config_t."""

    n_mics: int = 1
    n_partitions: int = afe.AEC_PARTITIONS
    bulk_delay_samples: int = 0


def check(cfg: AecConfig) -> None:
    """The checks of aec.c config_ok."""
    if not (1 <= cfg.n_mics <= 2 and cfg.n_partitions >= 2 and 0 <= cfg.bulk_delay_samples <= afe.AEC_BULK_DELAY_MAX):
        raise ValueError(f"bad aec configuration {cfg}")


def total(x: np.ndarray) -> np.float32:
    """The sum in array order, as the C loop adds it."""
    return np.cumsum(x, dtype=f32)[-1]


def power_of(bins: np.ndarray) -> np.ndarray:
    return bins.real * bins.real + bins.imag * bins.imag


def complex_of(re: np.ndarray, im: np.ndarray) -> np.ndarray:
    out = np.empty(re.shape, dtype=np.complex64)
    out.real = re
    out.imag = im
    return out


def db_power(db: float) -> np.float32:
    """A power ratio of decibels, from double as aec.c takes it."""
    return f32(10.0 ** (float(f32(db)) / 10.0))


def tail_keep() -> np.float32:
    """Each hop's share of the reference power left in the echo, from double as aec.c takes it."""
    return f32(np.exp(-float(HOP) / (float(grid.SAMPLE_RATE_HZ) * float(f32(afe.AEC_COUPLE_TAIL_S)))))


def hann() -> np.ndarray:
    """The window of the residual's spectrum, from double as aec.c builds it."""
    return np.array([0.5 - 0.5 * cos_series(2.0 * PI * i / N_FFT) for i in range(N_FFT)], dtype=f32)


class Canceller:
    """One microphone's filter and the statistics of its learning rate."""

    def __init__(self, n_partitions: int) -> None:
        self.weights = np.zeros((n_partitions, N_BINS), dtype=np.complex64)
        self.prop = np.full(n_partitions, f32(0.99) / f32(n_partitions), dtype=f32)
        self.err_smooth = np.zeros(N_BINS, dtype=f32)
        self.echo_smooth = np.zeros(N_BINS, dtype=f32)
        self.cross = f32(afe.AEC_PY_FLOOR)
        self.auto = f32(afe.AEC_PY_FLOOR)
        self.leak = f32(0.0)
        self.sum_adapt = f32(0.0)
        self.adapted = False
        self.echo_tail = np.zeros(HOP, dtype=f32)
        self.mic_tail = np.zeros(HOP, dtype=f32)
        self.couple = np.full(N_BINS, db_power(afe.AEC_COUPLE_START_DB), dtype=f32)
        self.mic_power = f32(0.0)
        self.err_power = f32(0.0)
        self.over = 0


class Aec:
    """The chain's echo stage: mic and ref in, the residual and the echo left per bin out."""

    def __init__(self, cfg: AecConfig | None = None, learn: bool = True) -> None:
        self.cfg = cfg or AecConfig()
        check(self.cfg)
        self.learn = learn
        self.window = hann()
        self.keep = tail_keep()
        self.couple_max = db_power(afe.AEC_COUPLE_MAX_DB)
        self.couple_peak = db_power(afe.AEC_COUPLE_PEAK_DB)
        self.hold_blocks = round(afe.AEC_COUPLE_HOLD_S * grid.SAMPLE_RATE_HZ / HOP)
        self.reset()

    def reset(self) -> None:
        n = self.cfg.n_partitions
        self.spectra = np.zeros((n, N_BINS), dtype=np.complex64)
        self.head = 0
        self.previous = np.zeros(HOP, dtype=f32)
        self.delay = np.zeros(self.cfg.bulk_delay_samples + HOP, dtype=f32)
        self.power = np.zeros(N_BINS, dtype=f32)
        self.ref_power = np.zeros(N_BINS, dtype=f32)
        self.blocks = 0
        self.silent_blocks = n + 1
        self.mics = [Canceller(n) for _ in range(self.cfg.n_mics)]
        self.diverged = False
        self.resets = 0

    def flush(self) -> None:
        """Forget the reference from before a short gap; every weight and estimate stays."""
        self.spectra[:] = 0
        self.previous[:] = 0
        self.delay[:] = 0
        self.silent_blocks = self.cfg.n_partitions + 1
        self.ref_power[:] = 0
        for c in self.mics:
            c.echo_tail[:] = 0
            c.mic_tail[:] = 0

    def delayed(self, ref: np.ndarray) -> np.ndarray:
        bulk = self.cfg.bulk_delay_samples
        self.delay[bulk:] = ref
        out = self.delay[:HOP].copy()
        self.delay[:bulk] = self.delay[HOP : HOP + bulk].copy()
        return out

    def filtered(self, weights: np.ndarray) -> np.ndarray:
        """Sum of each partition's spectrum times its weights, newest first, in that order. Parts multiply one by
        one, as aec.c does: numpy's complex product fuses them on some CPUs and rounds differently."""
        n = self.cfg.n_partitions
        re = np.zeros(N_BINS, dtype=f32)
        im = np.zeros(N_BINS, dtype=f32)
        for j in range(n):
            x, w = self.spectra[(self.head + j) % n], weights[j]
            re = re + (x.real * w.real - x.imag * w.imag)
            im = im + (x.real * w.imag + x.imag * w.real)
        return complex_of(re, im)

    def rates(self, c: Canceller, err_bins: np.ndarray, echo_bins: np.ndarray, sums: tuple) -> np.ndarray:
        """Each bin's step for this block (Valin 2007): how much of the error is residual echo."""
        far, echo, err, cross_te = sums
        err_power = power_of(err_bins)
        echo_power = power_of(echo_bins)
        err_dev = err_power - c.err_smooth
        echo_dev = echo_power - c.echo_smooth
        pey = total(err_dev * echo_dev)
        pyy = total(echo_dev * echo_dev)
        average = f32(afe.AEC_SPEC_AVERAGE)
        c.err_smooth = (ONE - average) * c.err_smooth + average * err_power
        c.echo_smooth = (ONE - average) * c.echo_smooth + average * echo_power
        pyy = np.sqrt(pyy)
        pey = pey / (pyy + f32(afe.AEC_TINY))
        err_floor = max(err, f32(afe.AEC_ERR_FLOOR))
        alpha = min(f32(afe.AEC_BETA0) * echo, f32(afe.AEC_BETA_MAX) * err_floor) / err_floor
        c.cross = (ONE - alpha) * c.cross + alpha * pey
        c.auto = (ONE - alpha) * c.auto + alpha * pyy
        c.auto = max(c.auto, f32(afe.AEC_PY_FLOOR))
        c.cross = min(max(c.cross, f32(afe.AEC_MIN_LEAK) * c.auto), c.auto)
        c.leak = c.cross / c.auto
        rer = (f32(afe.AEC_FAR_SHARE) * far + f32(afe.AEC_ECHO_SHARE) * c.leak * echo) / err_floor
        rer = max(rer, cross_te * cross_te / (f32(afe.AEC_ERR_FLOOR) + err_floor * echo))
        rer = min(rer, f32(afe.AEC_RER_MAX))
        if not c.adapted and c.sum_adapt > f32(self.cfg.n_partitions) and c.leak * echo > f32(0.03) * echo:
            c.adapted = True
        floor = f32(afe.AEC_POWER_FLOOR)
        if c.adapted:
            e = err_power + f32(afe.AEC_E_FLOOR)
            r = np.minimum(c.leak * echo_power, f32(0.5) * e)
            r = f32(0.7) * r + f32(0.3) * rer * e
            return r / (e * (self.power + floor))
        rate = f32(0.0)
        if far > f32(afe.AEC_FAR_MIN):
            rate = min(f32(0.25) * far, f32(0.25) * err_floor) / err_floor
        c.sum_adapt = c.sum_adapt + rate
        return rate / (self.power + floor)

    def update(self, c: Canceller, err_bins: np.ndarray, rate: np.ndarray) -> None:
        n = self.cfg.n_partitions
        e_re = err_bins.real * rate
        e_im = err_bins.imag * rate
        for j in range(n):
            x, w, prop = self.spectra[(self.head + j) % n], c.weights[j], c.prop[j]
            c.weights[j] = complex_of(
                w.real + prop * (x.real * e_re + x.imag * e_im), w.imag + prop * (x.real * e_im - x.imag * e_re)
            )
        # The gradient of one partition a block is held to a block's length: all of them would cost 2 n transforms.
        which = self.blocks % n
        taps = fft.inverse(c.weights[which], N_FFT)
        taps[HOP:] = f32(0.0)
        c.weights[which] = fft.forward(taps)
        sizes = np.array([np.sqrt(ONE + total(power_of(w))) for w in c.weights], dtype=f32)
        sizes = sizes + f32(0.1) * max(sizes.max(), ONE)
        c.prop = f32(0.99) * sizes / total(sizes)

    def watch(self, c: Canceller, mic_energy: np.float32, err_energy: np.float32) -> None:
        """Smooth the two powers and start the filter over when the error stays above the microphone."""
        keep = f32(afe.AEC_ERLE_KEEP)
        c.mic_power = keep * c.mic_power + (ONE - keep) * mic_energy
        c.err_power = keep * c.err_power + (ONE - keep) * err_energy
        c.over = c.over + 1 if err_energy > f32(afe.AEC_DIVERGE_RATIO) * mic_energy else 0
        if c.over >= afe.AEC_DIVERGE_BLOCKS:
            c.weights[:] = 0
            c.adapted = False
            c.sum_adapt = f32(0.0)
            c.over = 0
            self.diverged = True
            self.resets += 1

    def couple_step(self, c: Canceller, mic_power: np.ndarray, ref_now: np.ndarray, peak: np.float32) -> None:
        """Move the coupling of each bin near the reference's peak towards mic / accumulated reference power."""
        near = (ref_now > f32(afe.AEC_COUPLE_REF_MIN)) & (ref_now > peak)
        ratio = np.minimum(mic_power / np.where(near, self.ref_power, ONE), self.couple_max)
        rate = np.where(ratio > c.couple, f32(afe.AEC_COUPLE_UP), f32(afe.AEC_COUPLE_DOWN))
        c.couple = np.where(near, c.couple + rate * (ratio - c.couple), c.couple).astype(f32)

    def process(self, mic: np.ndarray, ref: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """One hop: (n_mics, HOP) and (HOP,) in, the residual of each microphone and the echo power per bin out."""
        far = self.delayed(np.asarray(ref, dtype=f32))
        self.silent_blocks = self.silent_blocks + 1 if not far.any() else 0
        out = np.asarray(mic, dtype=f32).copy()
        residual = np.zeros(N_BINS, dtype=f32)
        if self.silent_blocks > self.cfg.n_partitions:
            for m, c in enumerate(self.mics):
                c.mic_tail = out[m].copy()
            if self.silent_blocks > self.hold_blocks:
                self.ref_power[:] = 0
                return out, residual
            self.ref_power = self.keep * self.ref_power
            for c in self.mics:
                residual = np.maximum(residual, c.couple * self.ref_power)
            return out, residual
        n = self.cfg.n_partitions
        self.head = (self.head - 1) % n
        ref_now = power_of(fft.forward(np.concatenate([self.previous, far]) * self.window))
        self.ref_power = self.keep * self.ref_power + ref_now
        peak = ref_now.max() * self.couple_peak
        spectrum = fft.forward(np.concatenate([self.previous, far]))
        self.spectra[self.head] = spectrum
        self.previous = far
        smooth = f32(afe.AEC_POWER_SMOOTH) / f32(n)
        self.power = (ONE - smooth) * self.power + smooth * power_of(spectrum)
        self.diverged = False
        far_energy = total(far * far)
        for m, c in enumerate(self.mics):
            echo_bins = self.filtered(c.weights)
            echo = fft.inverse(echo_bins, N_FFT)[HOP:]
            err = out[m] - echo
            mic_energy = total(out[m] * out[m])
            err_energy = total(err * err)
            echo_energy = total(echo * echo)
            cross = total(err * echo)
            err_bins = fft.forward(np.concatenate([np.zeros(HOP, dtype=f32), err]))
            if self.learn:
                rate = self.rates(
                    c,
                    err_bins,
                    fft.forward(np.concatenate([np.zeros(HOP, dtype=f32), echo])),
                    (far_energy, echo_energy, err_energy, cross),
                )
                self.update(c, err_bins, rate)
            self.watch(c, mic_energy, err_energy)
            mic_power = power_of(fft.forward(np.concatenate([c.mic_tail, out[m]]) * self.window))
            c.mic_tail = out[m].copy()
            if self.learn:
                self.couple_step(c, mic_power, ref_now, peak)
            out[m] = err
            leak = min(f32(2.0) * c.leak, ONE)
            tail = np.concatenate([c.echo_tail, echo])
            c.echo_tail = echo
            residual = np.maximum(residual, leak * power_of(fft.forward(tail * self.window)))
            residual = np.maximum(residual, c.couple * self.ref_power)
        self.blocks += 1
        return out, residual

    def erle_db(self) -> float:
        """Smoothed echo return loss enhancement, the mean over the microphones."""
        ratios = [10.0 * np.log10((c.mic_power + 1e-12) / (c.err_power + 1e-12)) for c in self.mics]
        return float(np.mean(ratios))
