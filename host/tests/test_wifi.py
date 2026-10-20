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


def test_the_network_is_the_ssid_line_not_the_bssid_one() -> None:
    assert wifi.current_ssid(INTERFACES) == "esp"
    assert wifi.current_ssid("There is no wireless interface on the system.") == ""


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
