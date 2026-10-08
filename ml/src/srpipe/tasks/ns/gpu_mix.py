"""The Mixer's filters in torch, a whole batch at a time on the trainer's device (E9-T4, KEHOACH 3.9).

A loader worker draws and reads each example (data.Mixer.recipe) and collates the batch; Render filters it where the
nets learn: the RIRs, the microphones, the diffuse fields, the chain's digitising, then the slot's hpf and STFT. It is
data.Mixer.mixed and data.slot_powers at the precision scipy gives each, to rounding but for the self noise, drawn on
the device from a seed the example's own stream gives: the ns loss of a batch within 0.01% (tests/test_ns_gpu_mix.py).
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import torch
from scipy import fft as sfft
from scipy import signal
from torch import Tensor
from torch.nn import functional

from srpipe.core.audio_io import INT16_SCALE
from srpipe.dsp.afe import hpf
from srpipe.dsp.afe.chain import PCM_MAX, PCM_MIN
from srpipe.dsp.spec.window import sqrt_hann
from srpipe.generated import array, grid
from srpipe.scenes import device
from srpipe.tasks.ns import data

HOP, FFT_SIZE, FS = grid.HOP_SAMPLES, grid.FFT_SIZE, grid.SAMPLE_RATE_HZ
HPF_TAPS = 2048  # the hpf's impulse response past this sums under 1e-19
SPAN_STEP = 8 * HOP  # a batch's RIR span on a coarse grid: few cuFFT plans
SEED_END = 2**63
ROWS = ("dry", "source", "pair")  # the parts only some examples of a batch hold


def shared(shape: tuple[int, ...], dtype: torch.dtype = torch.float32) -> Tensor:
    return torch.zeros(shape, dtype=dtype).share_memory_()


def or_nan(value: float | None) -> float:
    return float("nan") if value is None else value


def collate(recipes: Sequence[data.Recipe], n: int) -> dict[str, Tensor]:
    """A batch of recipes as tensors in shared memory, which a loader worker hands over without a copy. RIRs pad to
    the batch's span, its longest RIR on a grid of SPAN_STEP; dry tracks and foreground sources end where the window
    ends, so one cut serves every example; <part>_rows lists the examples holding a part; pairs and tone stay int16."""
    span = -(-max(r.rirs.shape[-1] for r in recipes) // SPAN_STEP) * SPAN_STEP
    rows = {part: [i for i, r in enumerate(recipes) if getattr(r, part) is not None] for part in ROWS}
    out = {f"{part}_rows": torch.tensor(rows[part], dtype=torch.int64) for part in ROWS}
    out |= {
        "rirs": shared((len(recipes), *recipes[0].rirs.shape[:2], span)),
        "dry": shared((len(rows["dry"]), n + span)),
        "source": shared((len(rows["source"]), n + span)),
        "pair": shared((len(rows["pair"]), array.N_MICS, n), torch.int16),
        "tone": shared((len(recipes), array.N_MICS, n), torch.int16),
        "active": torch.from_numpy(np.stack([r.active for r in recipes])),
        "snr_db": torch.tensor([or_nan(r.snr_db) for r in recipes], dtype=torch.float64),
        "fg_dbfs": torch.tensor([or_nan(r.fg_dbfs) for r in recipes], dtype=torch.float64),
        "tone_dbfs": torch.tensor([r.tone_dbfs for r in recipes], dtype=torch.float64),
        "gain_db": torch.tensor([r.gain_db for r in recipes], dtype=torch.float64),
        "seed": torch.tensor([int(data.stream_at(r.noise_state).integers(SEED_END)) for r in recipes]),
    }
    for i, r in enumerate(recipes):
        out["rirs"][i, ..., : r.rirs.shape[-1]] = torch.from_numpy(r.rirs)
        out["tone"][i] = torch.from_numpy(r.tone)
    for part in ROWS:
        for k, i in enumerate(rows[part]):
            x = torch.from_numpy(getattr(recipes[i], part))
            if part == "pair":
                out[part][k] = x
            else:
                out[part][k, n + span - len(x) :] = x
    return out


def carried(tracks: Tensor, rirs: Tensor, span: int, n: int) -> Tensor:
    """Tracks (examples, n + span) through each example's RIRs (examples, mics, span), in their precision: the n
    samples after span of the full convolution, where the Mixer cuts each example after its own lead or taps."""
    size = sfft.next_fast_len(tracks.shape[-1] + span - 1, real=True)
    full = torch.fft.irfft(torch.fft.rfft(tracks, size)[:, None] * torch.fft.rfft(rirs, size), size)
    return full[..., span : span + n]


class Render:
    """data.Mixer.mixed and data.slot_powers for collated batches of examples of n samples, on one device. The
    talker's filters over a whole example run in float64, as float32 FFTs there leave it a floor ~120 dB under its
    peak that the loss's compression lifts (tests/test_ns_gpu_mix.py); the capture's floor sits far above float32's."""

    def __init__(self, mics: device.Microphones, n: int, where: str | torch.device) -> None:
        self.n, self.where = n, torch.device(where)
        self.self_noise_rms, self.chain_scale = mics.self_noise_rms, device.chain_scale(mics)
        self.pcm_scale = 2.0 ** (device.SLOT_FRACTION_BITS - mics.pcm_shift)
        self.louder = max(1.0, float(np.median(np.abs(mics.gains))))
        self.respond_size = sfft.next_fast_len(n + 2 * FFT_SIZE)
        freqs = np.fft.rfftfreq(self.respond_size, 1.0 / FS)
        level = np.interp(freqs, mics.freqs_hz, mics.level_db)
        response = 10.0 ** (level / 20.0) * np.exp(1j * np.interp(freqs, mics.freqs_hz, mics.phase_rad))
        self.response = self.both(response)
        self.pair_size = sfft.next_fast_len(n)
        across = 2.0 * np.fft.rfftfreq(self.pair_size, 1.0 / FS) * array.SPACING_M / array.SPEED_OF_SOUND_M_S
        coherence = np.sinc(across)
        self.coherence = self.put(coherence.astype(np.float32))
        self.apart = self.put(np.sqrt(1.0 - coherence**2).astype(np.float32))
        coef = hpf.coefficients().astype(np.float64)
        impulse = np.zeros(HPF_TAPS)
        impulse[0] = 1.0
        taps = signal.lfilter(coef[:3], np.concatenate([[1.0], coef[3:]]), impulse)
        self.hpf_size = sfft.next_fast_len(n + HPF_TAPS - 1, real=True)
        self.hpf = self.both(np.fft.rfft(taps, self.hpf_size))
        self.window = self.put(sqrt_hann(FFT_SIZE))
        self.balance = self.put(np.asarray(mics.gains, dtype=np.complex64))

    def put(self, x: np.ndarray) -> Tensor:
        return torch.from_numpy(x).to(self.where)

    def both(self, spectrum: np.ndarray) -> dict[torch.dtype, Tensor]:
        """A complex spectrum to multiply float32 and float64 signals' spectra by."""
        return {torch.float32: self.put(spectrum.astype(np.complex64)), torch.float64: self.put(spectrum)}

    def respond(self, air: Tensor) -> Tensor:
        """device.respond for every example (examples, mics, n), in air's precision."""
        spectrum = torch.fft.rfft(air[:, 0], self.respond_size) * self.response[air.dtype]
        ch0 = torch.fft.irfft(spectrum, self.respond_size)
        return torch.stack([ch0[:, : self.n], air[:, 1]], dim=1) / self.louder

    def diffuse(self, pairs: Tensor) -> Tensor:
        """device.diffuse_pair for every row of int16 pairs (rows, 2, n)."""
        u, v = (pairs.float() / INT16_SCALE).unbind(1)
        mixed = self.coherence * torch.fft.rfft(u, self.pair_size) + self.apart * torch.fft.rfft(v, self.pair_size)
        return torch.stack([u, torch.fft.irfft(mixed, self.pair_size)[:, : self.n]], dim=1)

    def level(self, x: Tensor) -> Tensor:
        """data.Mixer.level_of for every row of one channel (rows, n), float64."""
        return x.double().square().mean(-1).sqrt() * self.chain_scale

    def self_noise(self, seeds: Tensor) -> Tensor:
        """The microphones' white self noise (examples, mics, n), each example's from its own seed."""
        out = torch.empty((len(seeds), array.N_MICS, self.n), device=self.where)
        for row, seed in zip(out, seeds.tolist(), strict=True):
            row.normal_(generator=torch.Generator(self.where).manual_seed(seed))
        return out * self.self_noise_rms

    def slot(self, x: Tensor) -> Tensor:
        """device.slot_bins for every example (examples, mics, n): (examples, hops, N_BINS); the hpf over the whole
        example in x's precision, the STFT in float32, whose rounding stays within each frame."""
        y = torch.fft.irfft(torch.fft.rfft(x, self.hpf_size) * self.hpf[x.dtype], self.hpf_size)[..., : self.n]
        frames = functional.pad(y.float(), (FFT_SIZE - HOP, 0)).unfold(-1, FFT_SIZE, HOP)
        bins = torch.fft.rfft(frames * self.window, dim=-1)
        return 0.5 * (bins[:, 0] + bins[:, 1] * self.balance)

    def marked(self, rows: Tensor, count: int) -> Tensor:
        out = torch.zeros(count, dtype=torch.bool, device=self.where)
        out[rows] = True
        return out

    def mixed(self, b: dict[str, Tensor], seeds: Tensor) -> tuple[Tensor, Tensor, Tensor]:
        """The capture's int16 values and the talker alone on the chain's scale in float32 as Mixer.mixed rounds it
        (examples, mics, n), and which examples have a talker; every FFT runs over the whole batch, rows without the
        part at zero."""
        count, span = len(b["gain_db"]), b["rirs"].shape[-1]
        dry = torch.zeros((count, self.n + span), dtype=torch.float64, device=self.where)
        dry[b["dry_rows"]] = b["dry"].double()
        # The RIRs stay float32 as the bank holds them, which scipy's fftconvolve transforms in single precision.
        talker = carried(dry, b["rirs"][:, device.TALKER], span, self.n)
        source = torch.zeros((count, self.n + span), device=self.where)
        source[b["source_rows"]] = b["source"]
        noise = carried(source, b["rirs"][:, device.NOISE], span, self.n)
        pairs = torch.zeros_like(b["tone"])
        pairs[b["pair_rows"]] = b["pair"]
        tone, babble = self.diffuse(torch.cat([b["tone"], pairs])).split(count)
        talking, diffuse = self.marked(b["dry_rows"], count), self.marked(b["pair_rows"], count)
        heard = diffuse | self.marked(b["source_rows"], count)
        fg = torch.where(diffuse[:, None, None], babble, noise)
        responded = self.respond(talker)
        fg_resp, tone = self.respond(torch.cat([fg, tone])).split(count)
        fg_level = self.level(fg_resp[:, 0])
        if torch.any(heard & ~(fg_level > 0)):
            raise ValueError("a silent foreground")
        hops = talker[:, 0].square().reshape(count, -1, HOP).sum(-1)
        power = (hops * b["active"]).sum(-1) / (b["active"].sum(-1) * HOP)
        snr_gain = torch.sqrt(power / 10.0 ** (b["snr_db"] / 10.0) / fg[:, 0].double().square().mean(-1))
        gain = torch.where(heard, torch.where(talking, snr_gain, 10.0 ** (b["fg_dbfs"] / 20.0) / fg_level), 0.0)
        tone_gain = 10.0 ** (b["tone_dbfs"] / 20.0) / self.level(tone[:, 1])
        total = responded.float() + gain.float()[:, None, None] * fg_resp + tone_gain.float()[:, None, None] * tone
        g = 10.0 ** (b["gain_db"] / 20.0)[:, None, None]
        pcm = torch.floor((g.float() * total + self.self_noise(seeds)) * self.pcm_scale).clamp(PCM_MIN, PCM_MAX)
        return pcm, (g * self.chain_scale * responded).float(), talking

    def __call__(self, batch: dict[str, Tensor]) -> dict[str, Tensor]:
        """power and speech (examples, hops, N_BINS) at the slot, of the capture and of the talker alone, and vad
        (examples, hops), on the device."""
        b = {k: v.to(self.where, non_blocking=True) for k, v in batch.items() if k != "seed"}
        pcm, linear, talking = self.mixed(b, batch["seed"])
        power = self.slot(pcm / INT16_SCALE).abs().square()
        # cuFFT pairs a batch's real rows, so a silent row holds rounding of its neighbour's talker.
        speech = torch.where(talking[:, None, None], self.slot(linear.double()).abs().square(), 0.0)
        active = b["active"]
        vad = active | torch.cat([torch.zeros_like(active[:, :1]), active[:, :-1]], dim=1)
        return {"power": power, "speech": speech, "vad": vad.float()}
