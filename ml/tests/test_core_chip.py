"""srpipe.core.config.chip: one chip a process, chip.yaml's default unless SRPIPE_CHIP names another (ADR-0046)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from srpipe.core import config, run_dir

DAY = date(2026, 11, 20)


def test_the_s3_is_the_default_with_its_int8_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(config.CHIP_ENV, raising=False)
    assert config.chip()["name"] == "esp32s3"
    assert config.int8_dir() == "int8"
    assert config.chip_is_default()


def test_srpipe_chip_picks_the_s31_and_its_own_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.CHIP_ENV, "esp32s31")
    assert config.chip()["name"] == "esp32s31"
    assert config.int8_dir() == "int8_esp32s31"
    assert not config.chip_is_default()


def test_esp_ppq_targets_the_chip_of_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("esp_ppq")
    from srpipe.compress.quant import ptq_espdl

    monkeypatch.delenv(config.CHIP_ENV, raising=False)
    assert ptq_espdl.target() == "esp32s3"
    monkeypatch.setenv(config.CHIP_ENV, "esp32s31")
    assert ptq_espdl.target() == "esp32s31"


def test_an_unknown_chip_stops_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.CHIP_ENV, "esp32s2")
    with pytest.raises(SystemExit):
        config.chip()


def test_a_run_for_another_chip_never_takes_the_s3s_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = {"seed": 1}
    monkeypatch.delenv(config.CHIP_ENV, raising=False)
    s3 = run_dir.create_run_dir(tmp_path, "branch", cfg, today=DAY, repo=tmp_path)
    monkeypatch.setenv(config.CHIP_ENV, "esp32s31")
    s31 = run_dir.create_run_dir(tmp_path, "branch", cfg, today=DAY, repo=tmp_path)
    assert s3.name != s31.name
    assert "chip" not in yaml.safe_load((s3 / "config.resolved.yaml").read_text(encoding="utf-8"))
    assert yaml.safe_load((s31 / "config.resolved.yaml").read_text(encoding="utf-8"))["chip"] == "esp32s31"


def test_a_build_heard_on_another_chips_pitch_row_records_that_chip(monkeypatch: pytest.MonkeyPatch) -> None:
    from srpipe.scenes import device

    on_row = {"pitch": {"source": "swiftf0", "int8": {"run": "r", "row": "kl"}}}
    on_float = {"pitch": {"source": "swiftf0"}}
    monkeypatch.delenv(config.CHIP_ENV, raising=False)
    assert device.heard_chip(on_row, pitch=True) == {}
    monkeypatch.setenv(config.CHIP_ENV, "esp32s31")
    assert device.heard_chip(on_row, pitch=True) == {"chip": "esp32s31"}
    assert device.heard_chip(on_float, pitch=True) == {}
    assert device.heard_chip(on_row, pitch=False) == {}


def test_a_rebuild_on_another_chips_row_never_takes_the_s3s_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("esp_ppq")
    from srpipe.tasks.pitch import quant

    paths = {"processed": Path("p")}
    monkeypatch.delenv(config.CHIP_ENV, raising=False)
    s3 = quant.rebuilt_root(paths, "v9", "kl", stream=True)
    on_float = quant.rebuilt_root(paths, "v9", quant.FLOAT_ROW)
    monkeypatch.setenv(config.CHIP_ENV, "esp32s31")
    assert s3 == Path("p/command/v9_resim_kl_stream")
    assert quant.rebuilt_root(paths, "v9", "kl", stream=True) == Path("p/command/v9_resim_kl_esp32s31_stream")
    assert quant.rebuilt_root(paths, "v9", quant.FLOAT_ROW) == on_float
