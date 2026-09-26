"""TCP server for the board's audio stream: frames in, one WAV per channel and the seq gaps out (KEHOACH 7.4).

The board is the client and reconnects after a network blip. Every connection feeds the same recording,
so a blip shows up as a gap in seq, never as a second recording. Frames follow srhost.generated.stream.
Run alone for a quick look: uv run python -m srhost.stream_rx --out <dir> --duration-s 10
"""

from __future__ import annotations

import argparse
import json
import logging
import select
import socket
import sys
import threading
import wave
from array import array
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from srhost.config import ConfigError, record_config
from srhost.generated import grid, stream

POLL_S = 0.2
SEQ_MODULO = 1 << 32
MODE_OFF = 0
GAPS_FILE = "gaps.txt"
GAPS_HEADER = "offset_samples\texpected_seq\tgot_seq\n"
SUMMARY_FILE = "stream.json"

log = logging.getLogger(__name__)


class FrameError(ValueError):
    """A frame breaks contracts/stream/frame.yaml, or changes mode in the middle of a recording."""


@dataclass(frozen=True)
class Header:
    mode: int
    t_us: int
    seq: int
    channels: int
    sample_bytes: int
    samples: int

    @property
    def payload_bytes(self) -> int:
        return self.samples * self.channels * self.sample_bytes


@dataclass(frozen=True)
class Gap:
    """A break in seq: the frame that arrived and the one expected, at this sample of every WAV."""

    offset_samples: int
    expected_seq: int
    got_seq: int


@dataclass
class Summary:
    mode: int
    channels: tuple[str, ...]
    frames: int
    samples: int
    start_utc: str | None
    gaps: list[Gap] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return self.samples / grid.SAMPLE_RATE_HZ


def parse_header(raw: bytes) -> Header:
    """Check one frame header against the contract."""
    f = dict(zip(stream.HEADER_FIELDS, stream.HEADER.unpack(raw), strict=True))
    if f["magic"] != stream.MAGIC_U32 or f["version"] != stream.VERSION:
        raise FrameError(f"not an SRST v{stream.VERSION} frame: magic 0x{f['magic']:08x}, version {f['version']}")
    if f["mode"] not in stream.MODES or f["mode"] == MODE_OFF or f["format"] not in stream.FORMATS:
        raise FrameError(f"mode {f['mode']} or format {f['format']} is not in frame.yaml")
    if f["channels"] != stream.MODES[f["mode"]][1] or f["samples"] == 0:
        raise FrameError(f"mode {f['mode']} carries {stream.MODES[f['mode']][1]} channels, frame says {f['channels']}")
    return Header(f["mode"], f["t_us"], f["seq"], f["channels"], stream.FORMATS[f["format"]][1], f["samples"])


class Recorder:
    """One WAV per channel of the first frame's mode, in out_dir; later frames must keep that mode."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.summary = Summary(mode=MODE_OFF, channels=(), frames=0, samples=0, start_utc=None)
        self._files = ExitStack()
        self._wavs: list[wave.Wave_write] = []
        self._next_seq: int | None = None

    def add(self, header: Header, pcm: bytes) -> None:
        if not self._wavs:
            self._open(header.mode)
        elif header.mode != self.summary.mode:
            raise FrameError(f"mode {header.mode} after mode {self.summary.mode}")
        if self._next_seq is not None and header.seq != self._next_seq:
            self.summary.gaps.append(Gap(self.summary.samples, self._next_seq, header.seq))
        self._next_seq = (header.seq + 1) % SEQ_MODULO
        samples = array("h")
        samples.frombytes(pcm)
        if sys.byteorder != "little":
            samples.byteswap()
        for channel, wav in enumerate(self._wavs):
            wav.writeframes(samples[channel :: header.channels].tobytes())
        self.summary.frames += 1
        self.summary.samples += header.samples

    def close(self) -> Summary:
        self._files.close()
        if self.summary.frames:
            rows = "".join(f"{g.offset_samples}\t{g.expected_seq}\t{g.got_seq}\n" for g in self.summary.gaps)
            (self.out_dir / GAPS_FILE).write_text(GAPS_HEADER + rows, encoding="utf-8")
        return self.summary

    def _open(self, mode: int) -> None:
        self.summary.mode = mode
        self.summary.channels = stream.MODES[mode][2]
        self.summary.start_utc = datetime.now(UTC).isoformat(timespec="seconds")
        for name in self.summary.channels:
            wav = wave.Wave_write(self._files.enter_context((self.out_dir / f"{name}.wav").open("wb")))
            self._files.callback(wav.close)
            wav.setnchannels(1)
            wav.setsampwidth(stream.FORMATS[0][1])
            wav.setframerate(grid.SAMPLE_RATE_HZ)
            self._wavs.append(wav)


class StreamServer:
    """Listens on bind:port for the board; port 0 takes a free port, read back from .port."""

    def __init__(self, bind: str, port: int) -> None:
        self._sock = socket.create_server((bind, port))
        self._sock.settimeout(POLL_S)
        self.port = self._sock.getsockname()[1]

    def close(self) -> None:
        self._sock.close()

    def run(self, recorder: Recorder, stop: threading.Event, duration_s: float | None = None) -> None:
        """Take the board back after every disconnect until stop is set or duration_s of audio has arrived."""
        limit = None if duration_s is None else round(duration_s * grid.SAMPLE_RATE_HZ)
        while not stop.is_set() and (limit is None or recorder.summary.samples < limit):
            try:
                conn, peer = self._sock.accept()
            except TimeoutError:
                continue
            log.info("board connected from %s", peer[0])
            with conn:
                conn.settimeout(POLL_S)
                self._serve(conn, recorder, stop, limit)
            log.info("board disconnected after %d frames", recorder.summary.frames)

    def _superseded(self) -> bool:
        return bool(select.select([self._sock], [], [], 0)[0])

    def _serve(self, conn: socket.socket, recorder: Recorder, stop: threading.Event, limit: int | None) -> None:
        while limit is None or recorder.summary.samples < limit:
            raw = _read_exact(conn, stream.HEADER_BYTES, stop, self._superseded)
            if raw is None:
                return
            try:
                header = parse_header(raw)
                pcm = _read_exact(conn, header.payload_bytes, stop, self._superseded)
                if pcm is None:
                    return
                recorder.add(header, pcm)
            except FrameError as err:
                log.warning("dropping the connection: %s", err)
                return


def _read_exact(conn: socket.socket, n: int, stop: threading.Event, superseded: Callable[[], bool]) -> bytes | None:
    """n bytes, or None once the peer closes, stop is set, or it falls silent while a newer connection waits."""
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        if stop.is_set():
            return None
        try:
            chunk = conn.recv_into(view[got:])
        except TimeoutError:
            # A board that lost its link reconnects, and its old socket may never see the FIN.
            if superseded():
                return None
            continue
        except OSError:
            return None
        if chunk == 0:
            return None
        got += chunk
    return bytes(buf)


def summary_json(summary: Summary) -> dict:
    return {**asdict(summary), "duration_s": round(summary.duration_s, 3)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="empty directory for the WAV files")
    parser.add_argument("--duration-s", type=float, default=None, help="stop after this much audio; Ctrl-C otherwise")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        cfg = record_config()
    except ConfigError as err:
        print(f"stream_rx: {err}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    server = StreamServer(cfg.stream_bind, cfg.stream_port)
    recorder = Recorder(args.out)
    log.info("listening on %s:%d", cfg.stream_bind, server.port)
    try:
        server.run(recorder, threading.Event(), args.duration_s)
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
        summary = recorder.close()
    (args.out / SUMMARY_FILE).write_text(json.dumps(summary_json(summary), indent=2) + "\n", encoding="utf-8")
    print(f"{summary.frames} frames, {summary.duration_s:.1f} s, {len(summary.gaps)} gap(s) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
