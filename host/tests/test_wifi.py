"""srhost.wifi on text Windows prints, with no board and no network."""

from __future__ import annotations

import pytest

from srhost import wifi

INTERFACES = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    State                  : connected
    SSID                   : esp
    BSSID                  : 36:62:99:3e:8b:c4
    Network type           : Infrastructure
"""

PROFILE = """
Security settings
-----------------
    Authentication         : WPA2-Personal
    Security key           : Present
    Key Content            : not-a-real-key
"""

IPCONFIG = """
Ethernet adapter vEthernet (WSL):
   IPv4 Address. . . . . . . . . . . : 172.27.224.1
Wireless LAN adapter Wi-Fi:
   IPv4 Address. . . . . . . . . . . : 192.168.116.251
"""

# netsh wlan show networks mode=bssid as Windows 11 prints it, one block cut short; "old" without Band lines.
NETWORKS = """
Interface name : Wi-Fi
There are 5 networks currently visible.

SSID 1 : TPLINK5G
    Network type            : Infrastructure
    BSSID 1                 : 90:a7:c1:02:f6:a2
         Signal             : 80%
         Radio type         : 802.11ac
         Band               : 5 GHz
         Channel            : 132
         Bss Load:
             Connected Stations:         0
             Channel Utilization:        3 (1 %)

SSID 2 : Aladdin T2
    Network type            : Infrastructure
    BSSID 1                 : 24:43:e2:e1:3e:10
         Band               : 2.4 GHz
         Channel            : 2
    BSSID 2                 : 24:43:e2:e1:3e:11
         Band               : 5 GHz
         Channel            : 153

SSID 3 : esp
    Network type            : Infrastructure
    BSSID 1                 : 36:62:99:3e:8b:c4
         Band               : 2.4 GHz
         Channel            : 11

SSID 4 : old 2g
    BSSID 1                 : 00:11:22:33:44:55
         Channel            : 6

SSID 5 : old 5g
    BSSID 1                 : 00:11:22:33:44:66
         Channel            : 36
"""


def test_the_network_is_the_ssid_line_not_the_bssid_one() -> None:
    assert wifi.current_ssid(INTERFACES) == "esp"
    assert wifi.current_ssid("There is no wireless interface on the system.") == ""


def test_the_scan_gives_each_network_the_bands_it_is_heard_on() -> None:
    assert wifi.scan_bands(NETWORKS) == {
        "TPLINK5G": {"5 GHz"},
        "Aladdin T2": {"2.4 GHz", "5 GHz"},
        "esp": {"2.4 GHz"},
        "old 2g": {"2.4 GHz"},
        "old 5g": {"5 GHz"},
    }
    assert wifi.scan_bands("") == {}


@pytest.mark.parametrize("ssid", ["TPLINK5G", "old 5g"])
def test_a_network_heard_only_off_2_4_ghz_is_refused(ssid: str) -> None:
    with pytest.raises(wifi.BoardError, match=r"2\.4 GHz networks only"):
        wifi.check_band(ssid, wifi.scan_bands(NETWORKS))


@pytest.mark.parametrize("ssid", ["esp", "Aladdin T2", "old 2g", "hidden, out of the scan"])
def test_a_2_4_ghz_dual_band_or_unscanned_network_passes(ssid: str) -> None:
    wifi.check_band(ssid, wifi.scan_bands(NETWORKS))


def test_the_saved_key_is_the_key_content_line() -> None:
    assert wifi.saved_key(PROFILE) == "not-a-real-key"
    assert wifi.saved_key("    Security key           : Absent") == ""


def test_the_pc_address_is_the_one_on_the_boards_network() -> None:
    assert wifi.near("192.168.116.74", IPCONFIG) == "192.168.116.251"
    assert wifi.near("192.168.40.20", IPCONFIG) == ""


def test_a_key_stays_one_console_argument() -> None:
    assert wifi.quoted('a b"c\\d') == '"a b\\"c\\\\d"'


def test_the_broker_moves_host_and_keeps_scheme_and_port() -> None:
    assert wifi.with_host("mqtt://192.168.116.251:1883", "192.168.40.131") == "mqtt://192.168.40.131:1883"
    assert wifi.with_host("mqtts://broker.local", "10.0.0.2") == "mqtts://10.0.0.2"


@pytest.mark.parametrize("key", ["short", "x" * 64, "khóa-có-dấu-1"])
def test_keys_wifi_set_refuses_are_refused_here(key: str) -> None:
    with pytest.raises(wifi.BoardError):
        wifi.check_key(key)


def test_an_open_network_and_a_wpa_key_pass() -> None:
    wifi.check_key("")
    wifi.check_key("eight ch")


class ScriptedPort:
    """A serial port that hands out what the board printed, a byte at a time, and keeps what was written."""

    def __init__(self, printed: bytes) -> None:
        self.printed = printed
        self.written = b""

    @property
    def in_waiting(self) -> int:
        return 1 if self.printed else 0

    def read(self, size: int) -> bytes:
        taken, self.printed = self.printed[:size], self.printed[size:]
        return taken

    def write(self, data: bytes) -> None:
        self.written += data


def console_on(printed: bytes) -> wifi.Console:
    console = wifi.Console.__new__(wifi.Console)
    console.port = ScriptedPort(printed)
    console.seen = b""
    return console


def test_an_answer_behind_a_prompt_in_the_echo_is_still_read() -> None:
    line = "nvs set device stream_host 10.0.0.2"
    console = console_on(f"sr> {line}\r\nsr> \r\ndevice/stream_host = 10.0.0.2 (str): ESP_OK\r\nsr> ".encode())
    assert console.run(line, wifi.NVS_SET) == "ESP_OK"


def test_a_line_the_board_never_answers_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wifi, "REPLY_TIMEOUT_S", 0.01)
    console = console_on(b"sr> ")
    with pytest.raises(wifi.BoardError, match="no answer"):
        console.run("nvs set device stream_host 10.0.0.2", wifi.NVS_SET)


def test_opening_the_console_asks_for_a_restart_a_board_without_a_reset_pin_needs() -> None:
    console = wifi.Console("loop://")
    try:
        assert console.port.read(len(wifi.RESTART_LINE)) == wifi.RESTART_LINE
    finally:
        console.close()
