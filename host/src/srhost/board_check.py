"""Run board C's blocks through test_apps/board_c and print what each answers (KEHOACH 2.2, 3.19).

Each block is one letter; the camera's frames land in --out, under the data root by default, never in the repo.
Run: uv run python -m srhost.board_check --port <port> [--blocks iacglr] [--out <folder>]
"""

from __future__ import annotations

import argparse
import base64
import re
import sys
import time
from pathlib import Path

import serial

from srhost.config import ConfigError, record_config
from srhost.vision import IMAGES_DIR
from srhost.wifi import Console

COMMAND_MARK = b"!"
ALL_BLOCKS = "iacglr"
BLOCK_TIMEOUT_S = {"a": 15.0, "c": 120.0, "g": 12.0, "i": 12.0, "l": 8.0, "r": 12.0}
READY_S = 2.0
JPEG = re.compile(r"JPEG_BEGIN (\d+)\n(.*?)JPEG_END\n?", re.S)
OUT_FOLDER = "board_c_check"


def frames_out(text: str, folder: Path, block: str) -> str:
    """Write each JPEG between its marks to folder; the text with a line in its place."""
    count = 0

    def keep(found: re.Match[str]) -> str:
        nonlocal count
        data = base64.b64decode(found.group(2).replace("\n", ""))
        count += 1
        path = folder / f"{block}{count}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"frame {len(data)} of {found.group(1)} bytes: {path}\n"

    return JPEG.sub(keep, text)


def run_block(console: Console, block: str, folder: Path) -> bool:
    console.seen = b""
    console.port.write(COMMAND_MARK + block.encode())
    done = console.wait(re.compile(b"DONE " + block.encode()), BLOCK_TIMEOUT_S[block])
    raw = done.string[: done.start()] if done else console.seen
    print(frames_out(raw.decode(errors="replace").replace("\r", ""), folder, block), flush=True)
    return done is not None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", required=True, help="board C's serial port, the bridge's URL by default")
    parser.add_argument(
        "--blocks", default=ALL_BLOCKS, help=f"letters of {ALL_BLOCKS}: i2c, audio, camera, gps, lamp, ir"
    )
    parser.add_argument("--out", type=Path, help="folder for the camera's frames")
    args = parser.parse_args(argv)
    try:
        folder = args.out or record_config().data_root / IMAGES_DIR / OUT_FOLDER
        console = Console(args.port)
    except (ConfigError, serial.SerialException) as err:
        print(f"board_check: {err}", file=sys.stderr)
        return 2
    try:
        time.sleep(READY_S)
        missing = [block for block in args.blocks if not run_block(console, block, folder)]
    finally:
        console.close()
    if missing:
        print(f"board_check: no end from {' '.join(missing)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
