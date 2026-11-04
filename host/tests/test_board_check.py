"""board_check turns the app's base64 frames into files and leaves its lines alone."""

from __future__ import annotations

import base64
from pathlib import Path

from srhost import board_check


def test_a_frame_lands_as_a_file_and_the_lines_around_it_stay(tmp_path: Path) -> None:
    jpeg = b"\xff\xd8 picture \xff\xd9"
    coded = base64.b64encode(jpeg).decode()
    text = f"CAMERA grab: ESP_OK\nJPEG_BEGIN {len(jpeg)}\n{coded[:8]}\n{coded[8:]}\nJPEG_END\nCAMERA rest: ESP_OK\n"
    kept = board_check.frames_out(text, tmp_path, "c")
    assert (tmp_path / "c1.jpg").read_bytes() == jpeg
    assert kept.startswith("CAMERA grab: ESP_OK\nframe ") and kept.endswith("CAMERA rest: ESP_OK\n")


def test_text_without_a_frame_passes_through(tmp_path: Path) -> None:
    assert board_check.frames_out("IR 3 change(s)\n", tmp_path, "r") == "IR 3 change(s)\n"
    assert not list(tmp_path.iterdir())
