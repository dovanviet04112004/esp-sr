"""Score one recorded session against srpipe and print its table (KEHOACH 4.6, 7.7).

Every session gets level, DC, peak, clipping and A-weighted floor per channel, and the level the product chain hands
agc. With doa_deg labelled, ch0 and ch1 also get the pair figures of srpipe.metrics.mic_pair (E2-T4, E2-T7). With
clean (stream mode 5), it is checked against the chain with every module off, skipping hops that overlap unseen audio.
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
from srpipe.dsp.afe.chain import Chain, ChainConfig
from srpipe.generated import afe, array
from srpipe.metrics import mic_pair

from srhost.config import REPO_ROOT
from srhost.generated import grid
from srhost.stream_rx import GAPS_FILE

# Analysis overlap plus synthesis overlap: the second clean hop is the first built only from seen audio.
WARMUP_HOPS = 2
PCM_FULL_SCALE = 32768.0
PCM_MIN, PCM_MAX = -32768, 32767
SAMPLE_BYTES = 2
BLOCK = 1 << 20
CHAIN_TOLERANCE = REPO_ROOT / "contracts" / "golden" / "chain" / "tolerance.yaml"
PARITY_CHANNELS = ("ch0", "ch1", "clean")
PAIR_CHANNELS = ("ch0", "ch1")
# Search twice the nominal reach, so a spacing up to double the contract still shows (KEHOACH 2.3).
MAX_LAG_SAMPLES = 2.0 * array.MAX_DELAY_SAMPLES
ENDFIRE_COS_MIN = 0.9
SIGN_COS_MIN = 0.5
# vad and agc carry state across a stream gap, so only a build without modules rebuilds from a session.
PLAIN = ChainConfig(modules=())
# agc does not change the level it is handed, so the level is measured without it.
INTO_AGC_MODULES = tuple(m for m in afe.MODULES if m != "agc")
LEVEL_PERCENTILES = (10, 50, 90)
COHERENCE_MIN = 0.9


@dataclass(frozen=True)
class ChannelFigures:
    name: str
    rms_dbfs: float
    dc_lsb: float
    peak_lsb: int
    clipped: int
    floor_a_dbfs: float


@dataclass(frozen=True)
class PairFigures:
    """ch0 and ch1 against a source at a labelled angle: measured and expected delay, spacing, per-band match."""

    doa_deg: int
    delay: mic_pair.PairDelay
    expected_tau_samples: float
    sign_matches: bool | None
    spacing_m: float | None
    fit_tau_samples: float
    fit_phase0_deg: float
    bands: list[mic_pair.BandFigures]


@dataclass(frozen=True)
class ParityFigures:
    """Board clean against srpipe's chain on the same ch0 and ch1, over the hops the host can rebuild."""

    hops_compared: int
    hops_skipped: int
    max_abs_lsb: int
    over_tolerance: int
    tolerance_lsb: float
    snr_db: float


@dataclass(frozen=True)
class FrontLevel:
    """level_dbfs of the product chain, the level agc is handed, over every hop of ch0 and ch1 (KEHOACH 3.10)."""

    balanced: bool
    percentiles_dbfs: tuple[int, ...]
    speech_share: float


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


def channel_figures(name: str, pcm: np.ndarray, floor_a_dbfs: float) -> ChannelFigures:
    n = max(len(pcm), 1)
    mean = float(pcm.sum(dtype=np.int64)) / n
    blocks = (pcm[i : i + BLOCK].astype(np.int64) for i in range(0, len(pcm), BLOCK))
    square_sum = float(sum(int(np.dot(block, block)) for block in blocks))
    power = max(square_sum / n - mean * mean, 0.0)
    rms_dbfs = 10.0 * np.log10(power / PCM_FULL_SCALE**2) if power > 0 else float("-inf")
    clipped = int(np.count_nonzero((pcm == PCM_MIN) | (pcm == PCM_MAX)))
    peak = max(abs(int(pcm.min(initial=0))), abs(int(pcm.max(initial=0))))
    return ChannelFigures(name, rms_dbfs, mean, peak, clipped, floor_a_dbfs)


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
        chain = Chain(cfg=PLAIN)
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


def front_level(
    ch0: np.ndarray,
    ch1: np.ndarray,
    gap_offsets: list[int],
    gains: np.ndarray | None,
    modules: tuple[str, ...] = INTO_AGC_MODULES,
) -> FrontLevel:
    """Run the product chain up to agc over each stretch between gaps, as the board restarts it, skipping warm-up."""
    hop = grid.HOP_SAMPLES
    bounds = [0, *gap_offsets, min(len(ch0), len(ch1))]
    cfg = ChainConfig(modules=modules, balance_gains=gains)
    levels, speech = [], []
    for start, end in pairwise(bounds):
        chain = Chain(cfg=cfg)
        for k, at in enumerate(range(start, end - hop + 1, hop)):
            frame = chain.process(np.column_stack([ch0[at : at + hop], ch1[at : at + hop]]).reshape(-1))
            if k >= WARMUP_HOPS:
                levels.append(frame.level_dbfs)
                speech.append(frame.vad)
    if not levels:
        raise ValueError("no hop of ch0 and ch1 outlasts the warm-up")
    percentiles = tuple(round(float(np.percentile(levels, q))) for q in LEVEL_PERCENTILES)
    return FrontLevel(gains is not None, percentiles, float(np.mean(speech)))


def board_gains(board: str) -> np.ndarray | None:
    """The balance file srhost.calib wrote for this board, as calib/bal holds it, or None for a board not calibrated."""
    # srhost.calib reads its sessions through this module, so it is imported only here.
    from srhost.calib import CALIB_DIR, read_csv

    path = CALIB_DIR / f"{board}_balance.csv"
    return read_csv(path) if path.exists() else None


@dataclass(frozen=True)
class Score:
    meta: dict
    channels: list[ChannelFigures]
    parity: ParityFigures | None
    pair: PairFigures | None
    trimmed_samples: int = 0
    front: FrontLevel | None = None


def pair_figures(stats: mic_pair.PairStats, doa_deg: int) -> PairFigures:
    """Figures of a source at doa_deg: the sign is judged off broadside, the spacing only near the ends."""
    cos = float(np.cos(np.radians(doa_deg)))
    delay = mic_pair.gcc_phat_delay(stats, MAX_LAG_SAMPLES)
    fit_tau, fit_phase0 = mic_pair.linear_phase_fit(stats, delay.tau_samples)
    return PairFigures(
        doa_deg=doa_deg,
        delay=delay,
        expected_tau_samples=mic_pair.expected_tau_samples(doa_deg),
        sign_matches=bool(np.sign(delay.tau_samples) == np.sign(cos)) if abs(cos) >= SIGN_COS_MIN else None,
        spacing_m=mic_pair.spacing_from_tau_m(delay.tau_samples, doa_deg) if abs(cos) >= ENDFIRE_COS_MIN else None,
        fit_tau_samples=fit_tau,
        fit_phase0_deg=fit_phase0,
        bands=mic_pair.pair_bands(stats, fit_tau),
    )


def score(session: Path) -> Score:
    meta = json.loads((session / "session.json").read_text(encoding="utf-8"))
    pcm = {p.stem: read_wav(p) for p in sorted(session.glob("*.wav"))}
    if not pcm:
        raise ValueError(f"{session} holds no WAV")
    # A receiver stopped inside a frame leaves the last frame on some channels only; score the common part.
    common = min(len(samples) for samples in pcm.values())
    trimmed = max(len(samples) for samples in pcm.values()) - common
    pcm = {name: samples[:common] for name, samples in pcm.items()}
    floors = {}
    stats = None
    if all(name in pcm for name in PAIR_CHANNELS):
        stats = mic_pair.pair_stats(pcm["ch0"], pcm["ch1"])
        floors = {name: mic_pair.noise_floor_dbfs(stats, i) for i, name in enumerate(PAIR_CHANNELS)}
    for name in pcm.keys() - floors.keys():
        floors[name] = mic_pair.noise_floor_dbfs(mic_pair.pair_stats(pcm[name], pcm[name]), 0)
    channels = [channel_figures(name, samples, floors[name]) for name, samples in pcm.items()]
    parity = None
    if all(name in pcm for name in PARITY_CHANNELS):
        parity = chain_parity(pcm["ch0"], pcm["ch1"], pcm["clean"], read_gap_offsets(session))
    pair = None
    front = None
    if stats is not None:
        front = front_level(pcm["ch0"], pcm["ch1"], read_gap_offsets(session), board_gains(meta["board"]))
        if meta.get("doa_deg") is not None:
            pair = pair_figures(stats, int(meta["doa_deg"]))
    return Score(meta, channels, parity, pair, trimmed, front)


def pair_lines(pair: PairFigures) -> list[str]:
    verdict = {True: "matches the label", False: "OPPOSITE to the label", None: "not judged near broadside"}
    spacing = f"{1000 * pair.spacing_m:.1f} mm" if pair.spacing_m is not None else "not judged away from the ends"
    return [
        "",
        f"source at {pair.doa_deg} deg: tau = t0 - t1 = {pair.delay.tau_samples:+.2f} samples"
        f" ({1e6 * pair.delay.tau_s:+.1f} us, PHAT peak {pair.delay.peak:.2f}, {pair.delay.frames} frames),"
        f" contract expects {pair.expected_tau_samples:+.2f}; sign {verdict[pair.sign_matches]}; spacing {spacing}",
        f"phase line 200 Hz - {array.ALIAS_HZ:.0f} Hz: tau {pair.fit_tau_samples:+.2f} samples,"
        f" constant phase {pair.fit_phase0_deg:+.1f} deg",
        "",
        "| Band Hz | ch1 - ch0 dB | Phase deg | Phase after tau deg | Coherence |",
        "|---|---|---|---|---|",
        *(
            f"| {b.low_hz:.0f}-{b.high_hz:.0f} | {b.level_diff_db:+.2f} | {b.phase_diff_deg:+.1f}"
            f" | {b.phase_after_delay_deg:+.1f} | {b.coherence:.3f}{'' if b.coherence >= COHERENCE_MIN else ' (low)'} |"
            for b in pair.bands
        ),
    ]


def table(result: Score) -> str:
    meta = result.meta
    lines = [
        f"session {meta['session']}  kind {meta['kind']}  fw {meta['fw']}  pcm_shift {meta['pcm_shift']}"
        f"  seq_gaps {meta['seq_gaps']}",
        *(
            [f"channels differ in length: the last {result.trimmed_samples} samples of the longer ones are left out"]
            if result.trimmed_samples
            else []
        ),
        "",
        "| Channel | RMS dBFS | DC LSB | Peak LSB | Clipped | Floor dBFS(A) |",
        "|---|---|---|---|---|---|",
        *(
            f"| {f.name} | {f.rms_dbfs:.1f} | {f.dc_lsb:.1f} | {f.peak_lsb} | {f.clipped} | {f.floor_a_dbfs:.1f} |"
            for f in result.channels
        ),
    ]
    if result.front is not None:
        front = result.front
        balance = "through the board's balance file" if front.balanced else "without balance: no file for this board"
        lines += [
            "",
            f"level into agc, {balance}",
            "",
            f"| {' | '.join(f'p{q} dBFS' for q in LEVEL_PERCENTILES)} | Speech hops (vad) |",
            f"|{'---|' * (len(LEVEL_PERCENTILES) + 1)}",
            f"| {' | '.join(str(v) for v in front.percentiles_dbfs)} | {100 * front.speech_share:.1f} % |",
        ]
    if result.pair is not None:
        lines += pair_lines(result.pair)
    if result.parity is not None:
        parity = result.parity
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
        print(table(score(args.session)))
    except (OSError, ValueError, KeyError) as err:
        print(f"score: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
