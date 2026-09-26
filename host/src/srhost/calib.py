"""Estimate balance from frontal white noise sessions, check it across placements, write it to a board (KEHOACH 3.4).

estimate pools the sessions with srpipe.dsp.afe.balance, checks each placement against gains estimated from
the others, and only then saves docs/measurements/calib/<board>_balance.csv. write sends that file to the
console of firmware/test_apps/calib, which stores NVS calib/bal and reads back a CRC32 compared here.
Run: uv run --extra score python -m srhost.calib estimate <session> <session>... | write <csv> --port <tty>
"""

from __future__ import annotations

import argparse
import struct
import sys
import time
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import serial
from srpipe.dsp.afe import balance
from srpipe.generated import grid
from srpipe.metrics import mic_pair

from srhost.config import REPO_ROOT, ConfigError, record_config
from srhost.score import read_wav

CALIB_DIR = REPO_ROOT / "docs" / "measurements" / "calib"
MIN_PLACEMENTS = 2
MAX_LEVEL_ERROR_DB = 1.0
COHERENCE_MIN = 0.9
BINS_PER_LINE = 6
BAUD = 115200
READY_TIMEOUT_S = 15.0
REPLY_TIMEOUT_S = 3.0
PROMPT = b"calib> "
COLUMNS = "bin,freq_hz,re,im,level_db,phase_deg"


@dataclass(frozen=True)
class Check:
    """One placement levelled by gains estimated without it."""

    session: str
    worst_level_db: float
    worst_phase_deg: float
    bands: list[mic_pair.BandFigures]

    @property
    def passed(self) -> bool:
        return self.worst_level_db < MAX_LEVEL_ERROR_DB


def session_stats(session: Path) -> mic_pair.PairStats:
    ch0, ch1 = read_wav(session / "ch0.wav"), read_wav(session / "ch1.wav")
    n = min(len(ch0), len(ch1))
    return mic_pair.pair_stats(ch0[:n], ch1[:n], frame_samples=grid.FFT_SIZE)


def cross_check(names: list[str], stats: list[mic_pair.PairStats]) -> list[Check]:
    """Leave each placement out, estimate from the rest, and measure what is left on the one left out."""
    checks = []
    for i, name in enumerate(names):
        others = balance.estimate(stats[:i] + stats[i + 1 :])
        bands = mic_pair.pair_bands(balance.compensated(stats[i], others.gains))
        coherent = [b for b in bands if b.coherence >= COHERENCE_MIN] or bands
        checks.append(
            Check(
                session=name,
                worst_level_db=max(abs(b.level_diff_db) for b in coherent),
                worst_phase_deg=max(abs(b.phase_diff_deg) for b in coherent),
                bands=bands,
            )
        )
    return checks


def write_csv(path: Path, est: balance.Balance, names: list[str]) -> None:
    freqs = np.arange(grid.N_BINS) * grid.SAMPLE_RATE_HZ / grid.FFT_SIZE
    level = 20.0 * np.log10(np.abs(est.gains))
    phase = np.degrees(np.angle(est.gains))
    lines = [
        f"# sessions: {' '.join(names)}",
        f"# tau_samples: {est.tau_samples:.4f}  phase0_deg: {est.phase0_deg:.3f}  active_frames: {est.active_frames}",
        f"# estimated_utc: {datetime.now(UTC).isoformat(timespec='seconds')}",
        COLUMNS,
        *(
            f"{k},{freqs[k]:.2f},{g.real:.9g},{g.imag:.9g},{level[k]:.3f},{phase[k]:.3f}"
            for k, g in enumerate(est.gains)
        ),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_csv(path: Path) -> np.ndarray:
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
    if rows[0] != COLUMNS or len(rows) != grid.N_BINS + 1:
        raise ValueError(f"{path} is not a {grid.N_BINS}-bin balance file")
    values = [row.split(",") for row in rows[1:]]
    return np.array([complex(float(v[2]), float(v[3])) for v in values], dtype=np.complex64)


def blob(gains: np.ndarray) -> bytes:
    """calib/bal as storage_format.h lays it out: re, im float32 little endian per bin."""
    return b"".join(struct.pack("<ff", float(g.real), float(g.imag)) for g in gains.astype(np.complex64))


def put_lines(gains: np.ndarray) -> list[str]:
    lines = []
    for first in range(0, len(gains), BINS_PER_LINE):
        chunk = gains[first : first + BINS_PER_LINE]
        values = " ".join(f"{float(v):.9g}" for g in chunk for v in (g.real, g.imag))
        lines.append(f"bal put {first} {values}")
    return lines


def read_until(port: serial.Serial, marker: bytes, timeout_s: float) -> bytes:
    got = b""
    deadline = time.monotonic() + timeout_s
    while marker not in got:
        if time.monotonic() > deadline:
            raise TimeoutError(f"no {marker!r} from the board; is test_apps/calib flashed?")
        got += port.read(port.in_waiting or 1)
    return got


def command(port: serial.Serial, line: str) -> str:
    """Send one console line and return the board's 'ok ...' reply; an 'error ...' reply raises."""
    port.write(line.encode() + b"\r\n")
    reply = read_until(port, PROMPT, REPLY_TIMEOUT_S).decode(errors="replace")
    answers = [r.strip() for r in reply.splitlines() if r.strip().startswith(("ok", "error"))]
    if not answers or answers[-1].startswith("error"):
        raise RuntimeError(f"{line.split(' ')[0:2]}: {answers[-1] if answers else reply.strip()}")
    return answers[-1]


def write_to_board(gains: np.ndarray, port_name: str) -> str:
    port = serial.Serial(port_name, BAUD, timeout=0.1)
    try:
        read_until(port, PROMPT, READY_TIMEOUT_S)
        command(port, "bal clear")
        for line in put_lines(gains):
            command(port, line)
        command(port, f"bal commit {int(time.time())}")
        return command(port, "bal show")
    finally:
        port.close()


def estimate_main(args: argparse.Namespace) -> int:
    names = [s.name for s in args.sessions]
    if len(names) < MIN_PLACEMENTS:
        print(f"calib: need sessions from at least {MIN_PLACEMENTS} loudspeaker placements (KEHOACH 3.4)")
        return 2
    stats = [session_stats(s) for s in args.sessions]
    est = balance.estimate(stats)
    print(f"pooled {est.active_frames} frames: tau {est.tau_samples:+.3f} samples, phase0 {est.phase0_deg:+.2f} deg")
    checks = cross_check(names, stats)
    for check in checks:
        verdict = "pass" if check.passed else "FAIL"
        print(
            f"left out {check.session}: worst |level| {check.worst_level_db:.2f} dB,"
            f" worst |phase| {check.worst_phase_deg:.1f} deg over coherent bands -> {verdict}"
        )
        for b in check.bands:
            print(
                f"  {b.low_hz:.0f}-{b.high_hz:.0f} Hz: {b.level_diff_db:+.2f} dB {b.phase_diff_deg:+.1f} deg"
                f" coherence {b.coherence:.3f}"
            )
    if not all(c.passed for c in checks):
        print("calib: the cross-check failed, nothing written")
        return 3
    out = args.out or CALIB_DIR / f"{record_config().board}_balance.csv"
    write_csv(out, est, names)
    print(f"written {out}")
    return 0


def write_main(args: argparse.Namespace) -> int:
    gains = read_csv(args.csv)
    expected = zlib.crc32(blob(gains))
    reply = write_to_board(gains, args.port)
    print(f"board: {reply}")
    if f"crc 0x{expected:08x}" not in reply:
        print(f"calib: the board holds a different blob; expected crc 0x{expected:08x}")
        return 3
    print(f"calib/bal written and read back, crc 0x{expected:08x}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="action", required=True)
    est = sub.add_parser("estimate", help="estimate, cross-check and save the balance file")
    est.add_argument("sessions", nargs="+", type=Path, help="frontal white noise sessions, one per placement")
    est.add_argument("--out", type=Path, help="balance file; docs/measurements/calib/<board>_balance.csv otherwise")
    wr = sub.add_parser("write", help="send a balance file to test_apps/calib and verify it")
    wr.add_argument("csv", type=Path)
    wr.add_argument("--port", required=True, help="serial port of the board, e.g. /dev/ttyUSB0")
    args = parser.parse_args(argv)
    try:
        return estimate_main(args) if args.action == "estimate" else write_main(args)
    except (ConfigError, OSError, ValueError, RuntimeError, TimeoutError, serial.SerialException) as err:
        print(f"calib: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
