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


def mqtt_env(**changes: str) -> dict[str, str]:
    base = {
        "SRHOST_MQTT_HOST": "localhost",
        "SRHOST_MQTT_PORT": "1883",
        "SRHOST_MQTT_USER": "srhost",
        "SRHOST_MQTT_PASS": "secret",
    }
    return {k: v for k, v in {**base, **changes}.items() if v is not None}


def test_the_broker_settings_hide_the_password() -> None:
    cfg = config.mqtt_config(mqtt_env())
    assert (cfg.host, cfg.port, cfg.user, cfg.password) == ("localhost", 1883, "srhost", "secret")
    assert "secret" not in repr(cfg)


def test_broker_variables_are_named_when_missing_or_malformed() -> None:
    with pytest.raises(config.ConfigError, match="SRHOST_MQTT_PASS"):
        config.mqtt_config(mqtt_env(SRHOST_MQTT_PASS=None))
    with pytest.raises(config.ConfigError, match="SRHOST_MQTT_PORT"):
        config.mqtt_config(mqtt_env(SRHOST_MQTT_PORT="0"))


def test_the_example_lists_every_variable_config_reads() -> None:
    example = config.read_dotenv(config.HOST_ROOT / ".env.example")
    assert set(config.RECORD_VARIABLES) | set(config.MQTT_VARIABLES) <= set(example)
