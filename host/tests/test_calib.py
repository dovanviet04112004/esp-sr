"""calib estimates, cross-checks and writes balance the way plan 3.4 says, bit for bit on float32."""

from __future__ import annotations

import json
import struct
import wave
import zlib
from pathlib import Path

import numpy as np
import pytest
from srpipe.generated import grid

from srhost import calib

FS = grid.SAMPLE_RATE_HZ


def session(tmp_path: Path, name: str, level: float, seed: int) -> Path:
    """Silence, then white noise that ch1 hears `level` times louder and 0.2 samples earlier, then silence."""
    rng = np.random.default_rng(seed)
    x = np.concatenate(
        [1e-4 * rng.standard_normal(FS), 0.02 * rng.standard_normal(5 * FS), 1e-4 * rng.standard_normal(FS)]
    )
    spectrum = np.fft.rfft(x) * level * np.exp(2j * np.pi * np.fft.rfftfreq(len(x)) * 0.2)
    out = tmp_path / name
    out.mkdir()
    for ch, signal in (("ch0", x), ("ch1", np.fft.irfft(spectrum, n=len(x)))):
        with wave.open(str(out / f"{ch}.wav"), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(FS)
            wav.writeframes(np.round(signal * 32768).astype("<i2").tobytes())
    (out / "session.json").write_text(json.dumps({"session": name}), encoding="utf-8")
    return out


def gains(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(grid.N_BINS) + 1j * rng.standard_normal(grid.N_BINS)).astype(np.complex64)


def test_the_blob_follows_storage_format() -> None:
    g = gains()
    data = calib.blob(g)
    assert len(data) == grid.N_BINS * 2 * 4
    assert struct.unpack_from("<ff", data, 8 * 3) == (float(g[3].real), float(g[3].imag))


def test_console_lines_fit_and_carry_every_float32_exactly() -> None:
    g = gains(1)
    lines = calib.put_lines(g)
    assert len(lines) == -(-grid.N_BINS // calib.BINS_PER_LINE)
    assert max(len(line) for line in lines) < 256
    values = [np.float32(v) for line in lines for v in line.split()[3:]]
    assert np.array(values, dtype=np.float32).tobytes() == g.view(np.float32).tobytes()


def test_the_file_round_trips_bit_for_bit(tmp_path: Path) -> None:
    est = calib.balance.Balance(gains(2), -0.1, -2.2, 1000)
    path = tmp_path / "board_b_balance.csv"
    calib.write_csv(path, est, ["a", "b"])
    assert calib.read_csv(path).tobytes() == est.gains.tobytes()


def test_placements_that_agree_pass_and_an_outlier_blocks_the_estimate(tmp_path: Path) -> None:
    same = [session(tmp_path, f"s{i}", 3.5, seed=i) for i in range(2)]
    stats = [calib.session_stats(s) for s in same]
    assert all(c.passed for c in calib.cross_check(["s0", "s1"], stats))
    odd = calib.session_stats(session(tmp_path, "odd", 3.5 * 10 ** (3 / 20), seed=9))
    checks = calib.cross_check(["s0", "s1", "odd"], [*stats, odd])
    assert not checks[2].passed
    assert not all(c.passed for c in checks)


class FakeBoard:
    """A serial port that answers like test_apps/calib: 'ok ...' or 'error ...' lines, then the prompt; an empty
    line gets only a prompt. A board that reset on open has printed its boot prompt already."""

    def __init__(self, reset_on_open: bool = True) -> None:
        self.pending = bytearray(calib.PROMPT if reset_on_open else b"")
        self.in_waiting = len(self.pending)
        self.values: dict[int, tuple[float, float]] = {}
        self.stored: bytes | None = None
        self.shift: int | None = None

    def read(self, n: int) -> bytes:
        out, self.pending = bytes(self.pending[:n]), self.pending[n:]
        self.in_waiting = len(self.pending)
        return out

    def reset_input_buffer(self) -> None:
        self.pending.clear()
        self.in_waiting = 0

    def answer(self, words: list[str]) -> str | None:
        if not words:
            return None
        if words[:2] == ["bal", "clear"]:
            self.values.clear()
            return "ok cleared"
        if words[:2] == ["bal", "put"]:
            first, numbers = int(words[2]), [np.float32(v) for v in words[3:]]
            for i in range(0, len(numbers), 2):
                self.values[first + i // 2] = (numbers[i], numbers[i + 1])
            return f"ok put {first}"
        if words[:2] == ["bal", "commit"]:
            self.stored = b"".join(struct.pack("<ff", *self.values[k]) for k in range(grid.N_BINS))
            return "ok committed"
        if words[:2] == ["bal", "show"] and self.stored is not None:
            return f"ok bal ver 1 at 0 crc 0x{zlib.crc32(self.stored):08x} bins {grid.N_BINS}"
        if words[:2] == ["shift", "set"] and 8 <= int(words[2]) <= 16:
            self.shift = int(words[2])
            return f"ok shift {self.shift}"
        return "error usage"

    def write(self, data: bytes) -> None:
        reply = self.answer(data.decode().split())
        self.pending += (b"" if reply is None else (reply + "\r\n").encode()) + calib.PROMPT
        self.in_waiting = len(self.pending)

    def close(self) -> None:
        pass


def test_writing_sends_every_bin_and_the_crc_comes_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    board = FakeBoard()
    monkeypatch.setattr(calib.serial, "Serial", lambda *a, **k: board)
    est = calib.balance.Balance(gains(3), 0.0, 0.0, 1)
    path = tmp_path / "b.csv"
    calib.write_csv(path, est, ["x", "y"])
    assert calib.main(["write", str(path), "--port", "/dev/null"]) == 0
    assert board.stored == calib.blob(est.gains)


def test_an_error_reply_stops_the_write() -> None:
    board = FakeBoard()
    board.pending = bytearray(b"error bin 300 out of range\r\n" + calib.PROMPT)
    board.in_waiting = len(board.pending)
    board.write = lambda data: None
    with pytest.raises(RuntimeError, match="out of range"):
        calib.command(board, "bal put 300 1 0")


@pytest.mark.parametrize("reset_on_open", [True, False])
def test_the_console_is_reached_whether_or_not_opening_resets_the_board(
    monkeypatch: pytest.MonkeyPatch, reset_on_open: bool
) -> None:
    board = FakeBoard(reset_on_open)
    monkeypatch.setattr(calib.serial, "Serial", lambda *a, **k: board)
    assert calib.main(["shift", "13", "--port", "/dev/null"]) == 0
    assert board.shift == 13


def test_a_shift_the_board_refuses_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    board = FakeBoard()
    monkeypatch.setattr(calib.serial, "Serial", lambda *a, **k: board)
    assert calib.main(["shift", "7", "--port", "/dev/null"]) == 2
    assert board.shift is None
