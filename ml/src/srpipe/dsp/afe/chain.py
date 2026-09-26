"""The dsp_afe facade: interleaved int16 hops in, one clean channel and its figures out (KEHOACH 3.2, 4.5.5).

Mirror of firmware/components/dsp_afe/src/dsp_afe.c with every module off: STFT per microphone, the
plain two-channel mean, iSTFT, back to int16. A module joins this chain when its own Python reference
lands (E7 to E10), before its C, so contracts/golden/chain/ always matches the default firmware build.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from srpipe.dsp.spec.stft import Istft, Stft
from srpipe.generated import array, grid

FORMATS = {"MM": array.N_MICS, "MMR": array.N_MICS + 1}
PCM_FULL_SCALE = np.float32(32768.0)
PCM_MIN, PCM_MAX = -32768, 32767
LEVEL_MIN_DBFS = -128
ANGLE_UNKNOWN_DEG = -1

FLAG_GAP = 1 << 0
FLAG_CLIPPED = 1 << 1


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


def to_pcm(hop: np.ndarray) -> np.ndarray:
    """Float hop to int16, rounding half to even and saturating, as lrintf with a clamp."""
    return np.clip(np.rint(hop * PCM_FULL_SCALE), PCM_MIN, PCM_MAX).astype(np.int16)


class Chain:
    """One instance, one hop at a time; starts from silence with seq 0."""

    def __init__(self, input_format: str = "MM") -> None:
        if input_format not in FORMATS:
            raise ValueError(f"input format {input_format!r} is not one of {sorted(FORMATS)}")
        if input_format == "MMR":
            raise NotImplementedError("MMR needs aec, which lands in E10")
        self.n_channels = FORMATS[input_format]
        self._analysis = [Stft() for _ in range(array.N_MICS)]
        self._synthesis = Istft()
        self._seq = 0
        self._gap = False

    def reset(self) -> None:
        """Forget buffered samples after a gap; the next frame carries FLAG_GAP and seq runs on."""
        for st in self._analysis:
            st.reset()
        self._synthesis.reset()
        self._gap = True

    def process(self, interleaved: np.ndarray) -> Frame:
        """Take HOP_SAMPLES interleaved int16 samples of every channel and return the clean frame."""
        hop = np.asarray(interleaved, dtype=np.int16).reshape(grid.HOP_SAMPLES, self.n_channels)
        mics = hop[:, : array.N_MICS]
        clipped = bool(np.any((mics == PCM_MAX) | (mics == PCM_MIN)))
        samples = hop.astype(np.float32) / PCM_FULL_SCALE
        bins = [st.analyze(samples[:, m]) for m, st in enumerate(self._analysis)]
        mixed = np.float32(0.5) * (bins[0] + bins[1])
        clean = self._synthesis.synthesize(mixed)
        flags = (FLAG_GAP if self._gap else 0) | (FLAG_CLIPPED if clipped else 0)
        frame = Frame(
            pcm=to_pcm(clean),
            seq=self._seq,
            doa_deg=ANGLE_UNKNOWN_DEG,
            doa_conf=0,
            vad=0,
            level_dbfs=level_dbfs(clean),
            gain_db=0,
            flags=flags,
        )
        self._seq += 1
        self._gap = False
        return frame
