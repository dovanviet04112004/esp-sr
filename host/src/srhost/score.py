"""Score one recorded session against srpipe and print its table (KEHOACH 4.6, 7.7).

Every session gets level, DC, peak and clipping per channel. A session with ch0, ch1 and clean (stream
mode 5) also gets the parity of the board's clean channel with srpipe's chain mirror; the hops right
after the start and after each seq gap depend on audio the host never saw, so they are skipped.
Run: uv run --extra score python -m srhost.score <session directory>
"""

from __future__ import annotations

import argparse
import json
import sys
import wave
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import numpy as np
import yaml
from srpipe.dsp.afe.chain import Chain

from srhost.config import REPO_ROOT
from srhost.generated import grid
from srhost.stream_rx import GAPS_FILE

# Analysis overlap plus synthesis overlap: the second clean hop is the first built only from seen audio.
WARMUP_HOPS = 2
PCM_FULL_SCALE = 32768.0
PCM_MIN, PCM_MAX = -32768, 32767
SAMPLE_BYTES = 2
CHAIN_TOLERANCE = REPO_ROOT / "contracts" / "golden" / "chain" / "tolerance.yaml"
PARITY_CHANNELS = ("ch0", "ch1", "clean")


@dataclass(frozen=True)
class ChannelFigures:
    name: str
    rms_dbfs: float
    dc_lsb: float
    peak_lsb: int
    clipped: int


@dataclass(frozen=True)
class ParityFigures:
    """Board clean against srpipe's chain on the same ch0 and ch1, over the hops the host can rebuild."""

    hops_compared: int
    hops_skipped: int
    max_abs_lsb: int
    over_tolerance: int
    tolerance_lsb: float
    snr_db: float


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (grid.SAMPLE_RATE_HZ, 1, SAMPLE_BYTES):
            raise ValueError(f"{path} is not {grid.SAMPLE_RATE_HZ} Hz mono int16")
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")


def read_gap_offsets(session: Path) -> list[int]:
    path = session / GAPS_FILE
    if not path.exists():
        return []
    rows = path.read_text(encoding="utf-8").splitlines()[1:]
    return [int(row.split("\t")[0]) for row in rows if row.strip()]


def channel_figures(name: str, pcm: np.ndarray) -> ChannelFigures:
    x = pcm.astype(np.float64)
    ac = x - x.mean()
    power = float(ac @ ac) / max(len(ac), 1)
    rms_dbfs = 10.0 * np.log10(power / PCM_FULL_SCALE**2) if power > 0 else float("-inf")
    clipped = int(np.count_nonzero((pcm == PCM_MIN) | (pcm == PCM_MAX)))
    return ChannelFigures(name, rms_dbfs, float(x.mean()), int(np.abs(x).max(initial=0)), clipped)


def chain_tolerance_lsb() -> float:
    return float(yaml.safe_load(CHAIN_TOLERANCE.read_text(encoding="utf-8"))["tensors"]["pcm"]["max_abs"])


def chain_parity(ch0: np.ndarray, ch1: np.ndarray, clean: np.ndarray, gap_offsets: list[int]) -> ParityFigures:
    """Run the mirror over each stretch between gaps, fresh at every stretch, and compare hop by hop."""
    hop = grid.HOP_SAMPLES
    bounds = [0, *gap_offsets, len(clean)]
    tolerance = chain_tolerance_lsb()
    compared = skipped = over = 0
    max_abs = 0
    signal_power = error_power = 0.0
    for start, end in pairwise(bounds):
        chain = Chain()
        for k, at in enumerate(range(start, end - hop + 1, hop)):
            mics = np.column_stack([ch0[at : at + hop], ch1[at : at + hop]]).reshape(-1)
            mirror = chain.process(mics).pcm.astype(np.int32)
            if k < WARMUP_HOPS:
                skipped += 1
                continue
            board = clean[at : at + hop].astype(np.int32)
            error = board - mirror
            compared += 1
            max_abs = max(max_abs, int(np.abs(error).max()))
            over += int(np.count_nonzero(np.abs(error) > tolerance))
            signal_power += float(board.astype(np.float64) @ board)
            error_power += float(error.astype(np.float64) @ error)
    snr_db = 10.0 * np.log10(signal_power / error_power) if error_power > 0 else float("inf")
    return ParityFigures(compared, skipped, max_abs, over, tolerance, snr_db)


def score(session: Path) -> tuple[dict, list[ChannelFigures], ParityFigures | None]:
    meta = json.loads((session / "session.json").read_text(encoding="utf-8"))
    pcm = {p.stem: read_wav(p) for p in sorted(session.glob("*.wav"))}
    if not pcm:
        raise ValueError(f"{session} holds no WAV")
    figures = [channel_figures(name, samples) for name, samples in pcm.items()]
    parity = None
    if all(name in pcm for name in PARITY_CHANNELS):
        parity = chain_parity(pcm["ch0"], pcm["ch1"], pcm["clean"], read_gap_offsets(session))
    return meta, figures, parity


def table(meta: dict, figures: list[ChannelFigures], parity: ParityFigures | None) -> str:
    lines = [
        f"session {meta['session']}  kind {meta['kind']}  fw {meta['fw']}  pcm_shift {meta['pcm_shift']}"
        f"  seq_gaps {meta['seq_gaps']}",
        "",
        "| Channel | RMS dBFS | DC LSB | Peak LSB | Clipped |",
        "|---|---|---|---|---|",
        *(f"| {f.name} | {f.rms_dbfs:.1f} | {f.dc_lsb:.1f} | {f.peak_lsb} | {f.clipped} |" for f in figures),
    ]
    if parity is not None:
        lines += [
            "",
            "| Hops compared | Hops skipped | Max error LSB | Samples over tolerance | Tolerance LSB | SNR dB |",
            "|---|---|---|---|---|---|",
            f"| {parity.hops_compared} | {parity.hops_skipped} | {parity.max_abs_lsb} | {parity.over_tolerance}"
            f" | {parity.tolerance_lsb:g} | {parity.snr_db:.1f} |",
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("session", type=Path, help="a directory under raw/device/<board>/")
    args = parser.parse_args(argv)
    try:
        print(table(*score(args.session)))
    except (OSError, ValueError, KeyError) as err:
        print(f"score: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
