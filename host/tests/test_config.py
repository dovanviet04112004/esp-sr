"""config names every missing or malformed variable and resolves the manifest of the board (CLAUDE.md 4.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from srhost import config


def env(tmp_path: Path, **changes: str) -> dict[str, str]:
    base = {
        "SRPIPE_DATA_ROOT": str(tmp_path),
        "SRHOST_BOARD": "board_b",
        "SRHOST_STREAM_BIND": "0.0.0.0",
        "SRHOST_STREAM_PORT": "7700",
    }
    return {k: v for k, v in {**base, **changes}.items() if v is not None}


def test_a_complete_environment_gives_the_board_manifest(tmp_path: Path) -> None:
    cfg = config.record_config(env(tmp_path))
    assert (cfg.data_root, cfg.stream_port) == (tmp_path, 7700)
    assert cfg.manifest == config.REPO_ROOT / "ml" / "data" / "manifests" / "device" / "board_b.csv"
    assert cfg.manifest.exists()


def test_every_missing_variable_is_named(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match="SRHOST_BOARD, SRHOST_STREAM_PORT"):
        config.record_config(env(tmp_path, SRHOST_BOARD=None, SRHOST_STREAM_PORT=None))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SRHOST_STREAM_PORT", "70000"),
        ("SRHOST_STREAM_PORT", "port"),
        ("SRHOST_BOARD", "Board B"),
        ("SRPIPE_DATA_ROOT", "data"),
    ],
)
def test_malformed_values_are_refused(tmp_path: Path, name: str, value: str) -> None:
    with pytest.raises(config.ConfigError, match=name):
        config.record_config(env(tmp_path, **{name: value}))


def test_the_example_lists_every_variable_config_reads() -> None:
    example = config.read_dotenv(config.HOST_ROOT / ".env.example")
    assert set(config.RECORD_VARIABLES) <= set(example)
