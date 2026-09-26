"""A fake board for the stream tests: builds SRST frames and sends them the way net_stream will."""

from __future__ import annotations

import socket
import threading
import time
from array import array
from collections.abc import Iterable

import pytest

from srhost.generated import grid, stream

MODE_RAW_REF = 3
S16LE = 0


def frame(seq: int, mode: int = MODE_RAW_REF, samples: int = grid.HOP_SAMPLES, magic: int = stream.MAGIC_U32) -> bytes:
    """One frame whose sample i of channel c is (seq * 7 + i * 3 + c * 1000) wrapped to int16."""
    channels = stream.MODES[mode][1]
    pcm = array(
        "h", [((seq * 7 + i * 3 + c * 1000 + 32768) % 65536) - 32768 for i in range(samples) for c in range(channels)]
    )
    t_us = seq * grid.HOP_US
    return stream.HEADER.pack(magic, stream.VERSION, mode, t_us, seq, channels, S16LE, samples) + pcm.tobytes()


def expected_channel(seqs: Iterable[int], channel: int, samples: int = grid.HOP_SAMPLES) -> bytes:
    values = [((s * 7 + i * 3 + channel * 1000 + 32768) % 65536) - 32768 for s in seqs for i in range(samples)]
    return array("h", values).tobytes()


class FakeBoard:
    """Sends batches of frames to 127.0.0.1:port, one TCP connection per batch, like a board that reconnects.

    With hang set, every connection but the last stays open after its batch, as a socket whose FIN was lost.
    """

    def __init__(self, port: int, batches: list[list[bytes]], hang: bool = False) -> None:
        self._port = port
        self._batches = batches
        self._hang = hang
        self._open: list[socket.socket] = []
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        for index, batch in enumerate(self._batches):
            conn = socket.create_connection(("127.0.0.1", self._port))
            conn.sendall(b"".join(batch))
            if self._hang and index < len(self._batches) - 1:
                self._open.append(conn)
                time.sleep(0.3)
            else:
                conn.close()
        for conn in self._open:
            conn.close()


@pytest.fixture
def board_factory():
    boards: list[FakeBoard] = []

    def start(port: int, batches: list[list[bytes]], hang: bool = False) -> FakeBoard:
        board = FakeBoard(port, batches, hang)
        board.thread.start()
        boards.append(board)
        return board

    yield start
    for board in boards:
        board.thread.join(timeout=5)
