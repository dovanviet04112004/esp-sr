"""The dsp_afe facade: interleaved int16 hops in, one clean channel and its figures out (KEHOACH 3.2, 4.5.5).

Mirror of dsp_afe.c: hpf, aec on a reference ("MR", "MMR"), the STFT per microphone, balance on ch1, doa searched every
second hop after a speech hop, the spatial stage (the plain mean, or gsc steered by doa), the ns floor's gains with
aec's residual echo in its noise, iSTFT, vad on the clean hop, its level, agc, back to int16. One microphone skips
balance, doa and the mean (ADR-0042). Modules follow ChainConfig.modules. Run: python -m srpipe.dsp.afe.chain in out"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from srpipe.dsp.afe import aec, agc, balance, bss, doa, gsc, hpf, ns_omlsa, vad
from srpipe.dsp.spec.stft import Istft, Stft
from srpipe.generated import afe, array, grid

FORMATS = {"M": 1, "MR": 2, "MM": array.N_MICS, "MMR": array.N_MICS + 1}
MODULES_BUILT = ("hpf", "balance", "aec", "doa", "ns_omlsa", "vad", "agc")
SPATIALS = ("none", "gsc", "bss")
BROADSIDE_DEG = np.float32(sum(array.DOA_RANGE_DEG) / 2.0)
PCM_FULL_SCALE = np.float32(32768.0)
PCM_MIN, PCM_MAX = -32768, 32767
INT8_MIN, INT8_MAX = -128, 127
LEVEL_MIN_DBFS = INT8_MIN

FLAG_GAP = 1 << 0
FLAG_CLIPPED = 1 << 1
FLAG_AEC_DIVERGED = 1 << 2
FLAG_NO_REF = 1 << 3


@dataclass(frozen=True)
class ChainConfig:
    """What app_boot gives dsp_afe_init: the modules built, calib/bal, and the afe/* settings. balance_gains is
    calib/bal, one complex gain per bin on ch1, or None for a board never calibrated, which runs without balance as
    the firmware does. spatial is dsp_afe_config_t.spatial: none for the plain mean, gsc for the canceller steered by
    doa, broadside while doa has no angle, bss for the gate on doa's angle with the settings bss gives, None
    for contracts/afe.yaml's. agc_start_db, for the board simulation only, is the gain the agc starts
    from (KEHOACH 1.2); None starts as the firmware does. aec_delay_samples is calib/aec_delay."""

    modules: tuple[str, ...] = afe.MODULES
    balance_gains: np.ndarray | None = None
    ns_floor_db: float = afe.NS_FLOOR_DB
    agc_target_dbfs: float = afe.AGC_TARGET_DBFS
    vad_aggressiveness: int = afe.VAD_AGGRESSIVENESS
    spatial: str = "none"
    bss: bss.BssConfig | None = None
    agc_start_db: float | None = None
    aec_delay_samples: int = 0


@dataclass(frozen=True)
class Frame:
    """One clean hop and its figures, field for field as dsp_afe_frame_t."""

    pcm: np.ndarray
    seq: int
    doa_deg: int
    doa_conf: int
    vad: int
    level_dbfs: int
    gain_db: int
    flags: int


def level_dbfs(hop: np.ndarray) -> int:
    """Mean power of a float hop against full scale, rounded, clamped to -128 .. 0 as int8."""
    # Summed in order, as dsp_afe.c sums: a BLAS dot splits the sum differently on each CPU.
    energy = np.add.accumulate(np.square(np.asarray(hop, dtype=np.float32)))[-1]
    mean = energy / np.float32(grid.HOP_SAMPLES)
    if not mean > 0:
        return LEVEL_MIN_DBFS
    db = np.float32(10.0) * agc.log10_f32(mean)
    if not db > LEVEL_MIN_DBFS:
        return LEVEL_MIN_DBFS
    return 0 if db >= 0.0 else int(np.rint(db))


def to_int8(value: np.float32) -> int:
    """Round half to even and saturate, as the facade's to_int8."""
    if value <= INT8_MIN:
        return INT8_MIN
    return INT8_MAX if value >= INT8_MAX else int(np.rint(value))


def to_pcm(hop: np.ndarray) -> np.ndarray:
    """Float hop to int16, rounding half to even and saturating, as lrintf with a clamp."""
    return np.clip(np.rint(hop * PCM_FULL_SCALE), PCM_MIN, PCM_MAX).astype(np.int16)


class Chain:
    """One instance, one hop at a time; starts from silence with seq 0."""

    def __init__(self, input_format: str = "MM", cfg: ChainConfig | None = None) -> None:
        cfg = cfg or ChainConfig()
        if input_format not in FORMATS:
            raise ValueError(f"input format {input_format!r} is not one of {sorted(FORMATS)}")
        if missing := sorted(set(cfg.modules) - set(MODULES_BUILT)):
            raise NotImplementedError(f"no Python reference yet for {', '.join(missing)}")
        if cfg.spatial not in SPATIALS:
            raise NotImplementedError(f"spatial {cfg.spatial!r} is none of {', '.join(SPATIALS)}")
        if cfg.spatial == "bss" and "doa" not in cfg.modules:
            raise ValueError("spatial bss gates on doa's angle, and doa is off")
        self.cfg = cfg
        self.n_channels = FORMATS[input_format]
        self.n_mics = input_format.count("M")
        if self.n_mics == 1 and cfg.spatial != "none":
            raise ValueError(f"spatial {cfg.spatial} needs two microphones")
        self._analysis = [Stft() for _ in range(self.n_mics)]
        self._synthesis = Istft()
        self._gains = None
        if self.n_mics == 2 and "balance" in cfg.modules and cfg.balance_gains is not None:
            self._gains = np.asarray(cfg.balance_gains, dtype=np.complex64)
        self._start_modules()
        self._seq = 0
        self._gap = False

    def _start_modules(self) -> None:
        on = self.cfg.modules
        self._hpf = hpf.Hpf() if "hpf" in on else None
        self._aec = None
        if "aec" in on and self.n_channels > self.n_mics:
            self._aec = aec.Aec(aec.AecConfig(self.n_mics, bulk_delay_samples=self.cfg.aec_delay_samples))
        self._doa = doa.Doa() if "doa" in on and self.n_mics == 2 else None
        self._gsc = gsc.Gsc() if self.cfg.spatial == "gsc" else None
        self._bss = bss.Bss(self.cfg.bss) if self.cfg.spatial == "bss" else None
        # bss's gate silences the other seat at vad, so doa searches on a raw vad of the mics' mean (KEHOACH 3.8).
        self._vad_in = vad.Vad(self.cfg.vad_aggressiveness) if self._bss is not None else None
        self._last_heard = False
        self._last_vad = False
        self._ns = None
        if "ns_omlsa" in on:
            self._ns = ns_omlsa.Omlsa()
            self._ns.set_floor(self.cfg.ns_floor_db)
        self._vad = vad.Vad(self.cfg.vad_aggressiveness) if "vad" in on else None
        self._agc = (
            agc.Agc(agc.AgcConfig(target_dbfs=self.cfg.agc_target_dbfs), self.cfg.agc_start_db) if "agc" in on else None
        )

    @property
    def separator(self) -> bss.Bss | None:
        """The bss of a chain whose spatial stage is bss, for reading its gate and seats; None otherwise."""
        return self._bss

    def reset(self) -> None:
        """Forget buffered samples and every module's state after a long gap; the next frame carries FLAG_GAP and seq
        runs on."""
        for st in self._analysis:
            st.reset()
        self._synthesis.reset()
        self._start_modules()
        self._gap = True

    def resume(self) -> None:
        """Forget only what holds samples from before a short gap, the STFT and iSTFT buffers, hpf's memory, aec's
        reference, vad's band filters and agc's look-ahead, and keep every estimate; the next frame carries FLAG_GAP
        and seq runs on (KEHOACH 4.5.5)."""
        for st in self._analysis:
            st.reset()
        self._synthesis.reset()
        if self._hpf is not None:
            self._hpf.reset()
        if self._aec is not None:
            self._aec.flush()
        if self._vad is not None:
            self._vad.flush()
        if self._vad_in is not None:
            self._vad_in.flush()
        if self._agc is not None:
            self._agc.flush()
        self._gap = True

    def process(self, interleaved: np.ndarray) -> Frame:
        """Take HOP_SAMPLES interleaved int16 samples of every channel and return the clean frame."""
        hop = np.asarray(interleaved, dtype=np.int16).reshape(grid.HOP_SAMPLES, self.n_channels)
        mics = hop[:, : self.n_mics]
        clipped = bool(np.any((mics == PCM_MAX) | (mics == PCM_MIN)))
        ref_silent = self.n_channels > self.n_mics and not hop[:, self.n_mics :].any()
        channels = hop.astype(np.float32) / PCM_FULL_SCALE
        samples = channels[:, : self.n_mics]
        if self._hpf is not None:
            samples = np.stack([self._hpf.process(m, samples[:, m]) for m in range(self.n_mics)], axis=-1)
        echo_power = None
        if self._aec is not None:
            cancelled, echo_power = self._aec.process(samples.T, channels[:, self.n_mics])
            samples = cancelled.T
        bins = [st.analyze(samples[:, m]) for m, st in enumerate(self._analysis)]
        if self._gains is not None:
            bins[1] = balance.apply(bins[1], self._gains)
        direction = doa.DoaResult(doa.ANGLE_UNKNOWN_DEG, 0)
        heard = self._last_heard if self._vad_in is not None else self._last_vad
        update = heard and self._seq % afe.DOA_UPDATE_EVERY_HOPS == 0
        if self._vad_in is not None:
            self._last_heard = self._vad_in.process(np.float32(0.5) * (samples[:, 0] + samples[:, 1])).raw
        if self._doa is not None:
            direction = self._doa.process(bins[0], bins[1], update)
        if self._gsc is not None:
            angle_deg = direction.angle_deg if direction.angle_deg >= 0 else BROADSIDE_DEG
            mixed = self._gsc.process(bins[0], bins[1], angle_deg, not self._last_vad)
        elif self._bss is not None:
            mixed, _ = self._bss.process(bins[0], bins[1], direction.angle_deg, direction.confidence, update)
        elif self.n_mics == 1:
            mixed = bins[0]
        else:
            mixed = np.float32(0.5) * (bins[0] + bins[1])
        if self._ns is not None:
            gains = self._ns.process(mixed.real * mixed.real + mixed.imag * mixed.imag, echo_power).gain
            mixed = (mixed.real * gains + 1j * (mixed.imag * gains)).astype(np.complex64)
        clean = self._synthesis.synthesize(mixed)
        speech = self._vad.process(clean).speech if self._vad is not None else False
        level = level_dbfs(clean)
        gain_db = np.float32(0.0)
        if self._agc is not None:
            clean, gain_db = self._agc.process(clean, speech)
        flags = (FLAG_GAP if self._gap else 0) | (FLAG_CLIPPED if clipped else 0) | (FLAG_NO_REF if ref_silent else 0)
        flags |= FLAG_AEC_DIVERGED if self._aec is not None and self._aec.diverged else 0
        frame = Frame(
            pcm=to_pcm(clean),
            seq=self._seq,
            doa_deg=direction.angle_deg,
            doa_conf=direction.confidence,
            vad=int(speech),
            level_dbfs=level,
            gain_db=to_int8(gain_db),
            flags=flags,
        )
        self._seq += 1
        self._gap = False
        self._last_vad = speech
        return frame


def render(wav_in: Path, wav_out: Path, cfg: ChainConfig) -> None:
    """Every whole hop of a one- or two-microphone int16 WAV through the chain into a mono int16 WAV of the clean
    frames."""
    x, rate = sf.read(wav_in, dtype="int16", always_2d=True)
    formats = {FORMATS[f]: f for f in ("M", "MM")}
    if rate != grid.SAMPLE_RATE_HZ or x.shape[1] not in formats:
        raise ValueError(
            f"{wav_in}: want 1 or {array.N_MICS} channels at {grid.SAMPLE_RATE_HZ} Hz, got {x.shape[1]} at {rate}"
        )
    chain = Chain(formats[x.shape[1]], cfg)
    hop = grid.HOP_SAMPLES
    clean = [chain.process(x[k * hop : (k + 1) * hop].reshape(-1)).pcm for k in range(len(x) // hop)]
    wav_out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(wav_out, np.concatenate(clean), rate, subtype="PCM_16")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wav_in", type=Path, help="one or two microphones, int16 at the grid's rate")
    parser.add_argument("wav_out", type=Path)
    parser.add_argument("--spatial", choices=SPATIALS, default="none")
    args = parser.parse_args(argv)
    render(args.wav_in, args.wav_out, ChainConfig(spatial=args.spatial))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
