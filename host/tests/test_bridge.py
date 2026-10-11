"""bridge plays esptool's reset sequences whole and finds board B's COM port by its USB id."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from serial.rfc2217 import (
    COM_PORT_OPTION,
    SET_CONTROL,
    SET_CONTROL_DTR_OFF,
    SET_CONTROL_DTR_ON,
    SET_CONTROL_RTS_OFF,
    SET_CONTROL_RTS_ON,
)

from srhost import bridge

BOOTLOADER = [("dtr", False), ("rts", True), ("dtr", True), ("rts", False), ("dtr", False)]
HARD = [("rts", True), ("rts", False)]


class Lines:
    """Stand-in for the COM port that records every DTR and RTS change in order."""

    def __init__(self) -> None:
        self.changes: list[tuple[str, bool]] = []

    @property
    def dtr(self) -> bool:
        return next((v for line, v in reversed(self.changes) if line == "dtr"), False)

    @dtr.setter
    def dtr(self, value: bool) -> None:
        self.changes.append(("dtr", value))

    @property
    def rts(self) -> bool:
        return next((v for line, v in reversed(self.changes) if line == "rts"), False)

    @rts.setter
    def rts(self, value: bool) -> None:
        self.changes.append(("rts", value))


class Wire:
    def __init__(self) -> None:
        self.sent = b""

    def write(self, data: bytes) -> None:
        self.sent += data


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> bridge.EspPortManager:
    for name in ("ENTER_BOOT_S", "RESET_DELAY_S", "HOLD_S"):
        monkeypatch.setattr(bridge, name, 0)
    return bridge.EspPortManager(Lines(), Wire())


def control(manager: bridge.EspPortManager, *codes: bytes) -> list[tuple[str, bool]]:
    before = len(manager.serial.changes)
    for code in codes:
        manager._telnet_process_subnegotiation(COM_PORT_OPTION + SET_CONTROL + code)
        for thread in manager.resets:
            thread.join(1)
    return manager.serial.changes[before:]


def test_dtr_on_plays_the_bootloader_reset_whole(manager: bridge.EspPortManager) -> None:
    assert control(manager, SET_CONTROL_DTR_ON) == BOOTLOADER
    assert manager.is_download_mode


def test_the_rest_of_esptools_sequence_reaches_no_line(manager: bridge.EspPortManager) -> None:
    control(manager, SET_CONTROL_DTR_ON)
    assert control(manager, SET_CONTROL_RTS_OFF, SET_CONTROL_DTR_ON, SET_CONTROL_RTS_ON) == []
    assert len(manager.resets) == 1


def test_dtr_off_ends_download_mode(manager: bridge.EspPortManager) -> None:
    control(manager, SET_CONTROL_DTR_ON)
    assert control(manager, SET_CONTROL_DTR_OFF) == [("dtr", False)]
    assert not manager.is_download_mode
    assert control(manager, SET_CONTROL_RTS_OFF) == HARD


def test_rts_off_outside_download_mode_plays_a_hard_reset(manager: bridge.EspPortManager) -> None:
    assert control(manager, SET_CONTROL_RTS_ON) == []
    assert control(manager, SET_CONTROL_RTS_OFF) == HARD
    assert not manager.is_download_mode


def ports(*entries: tuple[int, int, str]) -> list[SimpleNamespace]:
    return [SimpleNamespace(vid=vid, pid=pid, device=device) for vid, pid, device in entries]


def test_find_port_matches_vid_and_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    found = ports((None, None, "COM1"), (0x1A86, 0x7522, "COM3"), (0x1A86, 0x7523, "COM7"))
    monkeypatch.setattr(bridge.list_ports, "comports", lambda: found)
    assert bridge.find_port("1a86:7523") == "COM7"
    assert bridge.find_port("1A86:7523") == "COM7"


def test_find_port_without_the_board_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge.list_ports, "comports", lambda: ports((0x1A86, 0x7522, "COM3")))
    assert bridge.find_port("1a86:7523") is None
