"""Shared by every pytest app on board B; make's ON_BOARD reaches it with --confcutdir (KEHOACH 2.1)."""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "esp32s3: runs on board B, an ESP32-S3")
    config.addinivalue_line("filterwarnings", "ignore::DeprecationWarning:serial.rfc2217")
    config.addinivalue_line("filterwarnings", "ignore:record_xml_attribute:pytest.PytestExperimentalApiWarning")


@pytest.fixture
def app(app):
    """pytest-embedded's app; esptool writes only images whose MD5 on board B differs, so a rerun skips the flash."""
    app.flash_args["write_flash_args"].append("--skip-flashed")
    # pytest-embedded spells it hard_reset, which esptool 5 deprecates; hard-reset is esptool's default anyway.
    app.flash_args["extra_esptool_args"].pop("after", None)
    return app
