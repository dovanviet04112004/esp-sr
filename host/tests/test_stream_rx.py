"""stream_rx turns SRST frames into one WAV per channel and a list of seq gaps (KEHOACH 7.4)."""

from __future__ import annotations

import threading
import wave
from pathlib import Path

import pytest

from conftest import MODE_RAW_REF, expected_channel, frame
from srhost import stream_rx
from srhost.generated import grid, stream


def run_server(
    tmp_path: Path, board_factory, batches: list[list[bytes]], hops: int, hang: bool = False
) -> stream_rx.Summary:
    server = stream_rx.StreamServer("127.0.0.1", 0)
    recorder = stream_rx.Recorder(tmp_path)
    board = board_factory(server.port, batches, hang)
    stop = threading.Event()
    timer = threading.Timer(10.0, stop.set)
    timer.start()
    try:
        server.run(recorder, stop, duration_s=hops * grid.HOP_SAMPLES / grid.SAMPLE_RATE_HZ)
    finally:
        timer.cancel()
        server.close()
    board.thread.join(timeout=5)
    return recorder.close()


def read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, grid.SAMPLE_RATE_HZ)
        return wav.readframes(wav.getnframes())


def test_a_reconnect_after_a_blip_is_one_recording_with_the_gap_listed(tmp_path: Path, board_factory) -> None:
    before = list(range(0, 10))
    after = list(range(13, 20))
    batches = [[frame(s) for s in before], [frame(s) for s in after]]
    summary = run_server(tmp_path, board_factory, batches, hops=len(before) + len(after))
    assert summary.channels == ("ch0", "ch1", "ref")
    assert summary.frames == len(before) + len(after)
    assert summary.gaps == [stream_rx.Gap(len(before) * grid.HOP_SAMPLES, 10, 13)]
    for c, name in enumerate(summary.channels):
        assert read_wav(tmp_path / f"{name}.wav") == expected_channel(before + after, c)
    gaps = (tmp_path / "gaps.txt").read_text(encoding="utf-8").splitlines()
    assert gaps == ["offset_samples\texpected_seq\tgot_seq", f"{len(before) * grid.HOP_SAMPLES}\t10\t13"]


def test_a_new_connection_supersedes_one_whose_close_never_arrived(tmp_path: Path, board_factory) -> None:
    batches = [[frame(s) for s in range(0, 4)], [frame(s) for s in range(6, 10)]]
    summary = run_server(tmp_path, board_factory, batches, hops=8, hang=True)
    assert summary.frames == 8
    assert summary.gaps == [stream_rx.Gap(4 * grid.HOP_SAMPLES, 4, 6)]


def test_a_bad_frame_drops_the_connection_and_the_board_comes_back(tmp_path: Path, board_factory) -> None:
    batches = [[frame(0), frame(1, magic=0xDEADBEEF), frame(2)], [frame(2), frame(3)]]
    summary = run_server(tmp_path, board_factory, batches, hops=3)
    assert summary.frames == 3
    assert summary.gaps == [stream_rx.Gap(grid.HOP_SAMPLES, 1, 2)]


def test_a_mode_change_is_refused(tmp_path: Path, board_factory) -> None:
    batches = [[frame(0), frame(1, mode=2)], [frame(1), frame(2)]]
    summary = run_server(tmp_path, board_factory, batches, hops=3)
    assert summary.frames == 3
    assert summary.mode == MODE_RAW_REF


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"version": 9}, "not an SRST"),
        ({"mode": 0}, "not in frame.yaml"),
        ({"format": 7}, "not in frame.yaml"),
        ({"channels": 2}, "carries 3 channels"),
    ],
)
def test_headers_off_the_contract_are_refused(patch: dict, message: str) -> None:
    fields = dict(zip(stream.HEADER_FIELDS, stream.HEADER.unpack(frame(0)[: stream.HEADER_BYTES]), strict=True))
    fields.update(patch)
    with pytest.raises(stream_rx.FrameError, match=message):
        stream_rx.parse_header(stream.HEADER.pack(*fields.values()))


def test_seq_wraps_without_a_gap(tmp_path: Path) -> None:
    recorder = stream_rx.Recorder(tmp_path)
    for seq in (0xFFFFFFFE, 0xFFFFFFFF, 0):
        raw = frame(seq % 1000)
        header = stream_rx.parse_header(raw[: stream.HEADER_BYTES])
        recorder.add(
            stream_rx.Header(header.mode, header.t_us, seq, header.channels, 2, header.samples),
            raw[stream.HEADER_BYTES :],
        )
    assert recorder.close().gaps == []
