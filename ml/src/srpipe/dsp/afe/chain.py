"""The dsp_afe facade: interleaved int16 hops in, one clean channel and its figures out (KEHOACH 3.2, 4.5.5).

Mirror of firmware/components/dsp_afe/src/dsp_afe.c: hpf per microphone, STFT per microphone, balance on ch1,
the plain two-channel mean, iSTFT, vad on the clean hop, its level, agc, back to int16. The modules follow
ChainConfig.modules, by default the product's list of contracts/afe.yaml that firmware/sdkconfig.afe turns on;
contracts/golden/chain/ runs with none of them, contracts/golden/chain_modules/ with that list.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from srpipe.dsp.afe import agc, balance, hpf, vad
from srpipe.dsp.spec.stft import Istft, Stft
from srpipe.generated import afe, array, grid

FORMATS = {"MM": array.N_MICS, "MMR": array.N_MICS + 1}
MODULES_BUILT = ("hpf", "balance", "vad", "agc")
PCM_FULL_SCALE = np.float32(32768.0)
PCM_MIN, PCM_MAX = -32768, 32767
INT8_MIN, INT8_MAX = -128, 127
LEVEL_MIN_DBFS = INT8_MIN
ANGLE_UNKNOWN_DEG = -1

FLAG_GAP = 1 << 0
FLAG_CLIPPED = 1 << 1


@dataclass(frozen=True)
class ChainConfig:
    """What app_boot gives dsp_afe_init: the modules built, calib/bal, and the afe/* settings.

    balance_gains is calib/bal, one complex gain per bin on ch1, or None for a board never calibrated, which
    runs without balance as the firmware does.
    """

    modules: tuple[str, ...] = afe.MODULES
    balance_gains: np.ndarray | None = None
    ns_floor_db: float = afe.NS_FLOOR_DB
    agc_target_dbfs: float = afe.AGC_TARGET_DBFS
    vad_aggressiveness: int = afe.VAD_AGGRESSIVENESS


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
    energy = np.float32(np.dot(hop, hop))
    with np.errstate(divide="ignore"):
        db = np.float32(10.0) * np.log10(energy / np.float32(grid.HOP_SAMPLES))
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
        if input_format == "MMR":
            raise NotImplementedError("MMR needs aec, which lands in E10")
        if missing := sorted(set(cfg.modules) - set(MODULES_BUILT)):
            raise NotImplementedError(f"no Python reference yet for {', '.join(missing)}")
        self.cfg = cfg
        self.n_channels = FORMATS[input_format]
        self._analysis = [Stft() for _ in range(array.N_MICS)]
        self._synthesis = Istft()
        self._gains = None
        if "balance" in cfg.modules and cfg.balance_gains is not None:
            self._gains = np.asarray(cfg.balance_gains, dtype=np.complex64)
        self._start_modules()
        self._seq = 0
        self._gap = False

    def _start_modules(self) -> None:
        on = self.cfg.modules
        self._hpf = hpf.Hpf() if "hpf" in on else None
        self._vad = vad.Vad(self.cfg.vad_aggressiveness) if "vad" in on else None
        self._agc = agc.Agc(agc.AgcConfig(target_dbfs=self.cfg.agc_target_dbfs)) if "agc" in on else None

    def reset(self) -> None:
        """Forget buffered samples and every module's state after a gap; the next frame carries FLAG_GAP and seq
        runs on."""
        for st in self._analysis:
            st.reset()
        self._synthesis.reset()
        self._start_modules()
        self._gap = True

    def process(self, interleaved: np.ndarray) -> Frame:
        """Take HOP_SAMPLES interleaved int16 samples of every channel and return the clean frame."""
        hop = np.asarray(interleaved, dtype=np.int16).reshape(grid.HOP_SAMPLES, self.n_channels)
        mics = hop[:, : array.N_MICS]
        clipped = bool(np.any((mics == PCM_MAX) | (mics == PCM_MIN)))
        samples = hop.astype(np.float32) / PCM_FULL_SCALE
        if self._hpf is not None:
            samples = np.stack([self._hpf.process(m, samples[:, m]) for m in range(array.N_MICS)], axis=-1)
        bins = [st.analyze(samples[:, m]) for m, st in enumerate(self._analysis)]
        if self._gains is not None:
            bins[1] = balance.apply(bins[1], self._gains)
        mixed = np.float32(0.5) * (bins[0] + bins[1])
        clean = self._synthesis.synthesize(mixed)
        speech = self._vad.process(clean).speech if self._vad is not None else False
        level = level_dbfs(clean)
        gain_db = np.float32(0.0)
        if self._agc is not None:
            clean, gain_db = self._agc.process(clean, speech)
        flags = (FLAG_GAP if self._gap else 0) | (FLAG_CLIPPED if clipped else 0)
        frame = Frame(
            pcm=to_pcm(clean),
            seq=self._seq,
            doa_deg=ANGLE_UNKNOWN_DEG,
            doa_conf=0,
            vad=int(speech),
            level_dbfs=level,
            gain_db=to_int8(gain_db),
            flags=flags,
        )
        self._seq += 1
        self._gap = False
        return frame
