"""Every environment variable of host/, read and checked in one place (CLAUDE.md 4.4, KEHOACH 4.6, 4.9).

Values come from the process environment first, then host/.env. A missing or malformed variable stops
the tool at start with its name; host/.env.example lists every one.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

HOST_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = HOST_ROOT.parent
DEVICE_MANIFESTS = REPO_ROOT / "ml" / "data" / "manifests" / "device"
BOARD_PATTERN = re.compile(r"^[a-z0-9_]+$")
PORT_MAX = 65535
RECORD_VARIABLES = ("SRPIPE_DATA_ROOT", "SRHOST_BOARD", "SRHOST_STREAM_BIND", "SRHOST_STREAM_PORT")


class ConfigError(RuntimeError):
    """A variable is missing or malformed; the message names it and says where to set it."""


@dataclass(frozen=True)
class RecordConfig:
    """What recording from a board needs: where raw/ lives, which board, where to listen."""

    data_root: Path
    board: str
    stream_bind: str
    stream_port: int

    @property
    def manifest(self) -> Path:
        return DEVICE_MANIFESTS / f"{self.board}.csv"


def read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    pairs = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            pairs[key.strip()] = value.strip()
    return pairs


def record_config(env: dict[str, str] | None = None) -> RecordConfig:
    """The recording settings, or ConfigError naming every variable to fix."""
    values = env if env is not None else {**read_dotenv(HOST_ROOT / ".env"), **os.environ}
    missing = [name for name in RECORD_VARIABLES if not values.get(name)]
    if missing:
        raise ConfigError(f"set {', '.join(missing)} in the environment or host/.env (see host/.env.example)")
    data_root = Path(values["SRPIPE_DATA_ROOT"])
    if not data_root.is_absolute() or not data_root.is_dir():
        raise ConfigError(f"SRPIPE_DATA_ROOT={data_root} is not an existing absolute directory")
    board = values["SRHOST_BOARD"]
    if not BOARD_PATTERN.match(board):
        raise ConfigError(f"SRHOST_BOARD={board!r} must be lower case letters, digits and _")
    port = values["SRHOST_STREAM_PORT"]
    if not port.isdigit() or not 1 <= int(port) <= PORT_MAX:
        raise ConfigError(f"SRHOST_STREAM_PORT={port!r} is not a TCP port")
    return RecordConfig(
        data_root=data_root, board=board, stream_bind=values["SRHOST_STREAM_BIND"], stream_port=int(port)
    )
