"""Put board B on a Wi-Fi network and point it at this PC: make wifi (KEHOACH 7.2, 7.4).

Asks the network, the one Windows is on by default, and its key without echo, the key Windows saved by default; sends
them through the console of main, restarts the board until it has an address, then points device/mqtt_uri and
device/stream_host at this PC's address on that network. The key never reaches the screen, a file or a log.
"""

from __future__ import annotations

import argparse
import getpass
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit

import serial

from srhost.config import ConfigError, record_config

PROMPT = b"sr> "
BAUD = 115200
PROMPT_TIMEOUT_S = 40.0
REPLY_TIMEOUT_S = 5.0
LINK_TIMEOUT_S = 40.0
SETTLE_S = 0.3
POLL_S = 0.1
KEY_BYTES_MIN, KEY_BYTES_MAX = 8, 63  # WPA2 passphrase, as wifi set of main checks it
SSID_LINE = re.compile(r"^\s*SSID\s*:\s*(.+?)\s*$", re.MULTILINE)
KEY_LINE = re.compile(r"^\s*Key Content\s*:\s*(.+?)\s*$", re.MULTILINE)
IPV4_LINE = re.compile(r"IPv4[^:\r\n]*:\s*(\d+\.\d+\.\d+\.\d+)")
STA_IP = re.compile(rb"sta ip: (\d+\.\d+\.\d+\.\d+)")
SESSION_UP = re.compile(rb"net_mqtt: session up")
WIFI_SET = re.compile(r"wifi/ssid .*, wifi/pass \d+ chars: (\S+)")
NVS_STR = re.compile(r'device/mqtt_uri = "([^"]*)" \(str\)')
NVS_SET = re.compile(r"device/\w+ = .* \(\w+\): (\S+)")


class BoardError(RuntimeError):
    """The board did not answer as main's console does."""


def windows(*command: str) -> str:
    """Text a Windows tool prints, or "" when it is missing or fails."""
    try:
        done = subprocess.run(command, capture_output=True, check=False, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.decode(errors="replace") if done.returncode == 0 else ""


def current_ssid(netsh_interfaces: str) -> str:
    """The network of the first connected Wi-Fi adapter in netsh wlan show interfaces, or ""."""
    found = SSID_LINE.search(netsh_interfaces)
    return found.group(1) if found else ""


def saved_key(netsh_profile: str) -> str:
    """The key of netsh wlan show profile ... key=clear, or "" for an open network or none saved."""
    found = KEY_LINE.search(netsh_profile)
    return found.group(1) if found else ""


def near(board_ip: str, ipconfig: str) -> str:
    """This PC's IPv4 address on the board's /24, or "" when the PC is not on that network."""
    prefix = board_ip.rsplit(".", 1)[0] + "."
    return next((ip for ip in IPV4_LINE.findall(ipconfig) if ip.startswith(prefix) and ip != board_ip), "")


def quoted(key: str) -> str:
    """key as one argument of the console, which splits on spaces and reads backslash escapes in quotes."""
    return '"' + key.replace("\\", "\\\\").replace('"', '\\"') + '"'


def with_host(uri: str, host: str) -> str:
    """uri with its host replaced, scheme and port kept."""
    parts = urlsplit(uri)
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def check_key(key: str) -> None:
    if key and not (KEY_BYTES_MIN <= len(key) <= KEY_BYTES_MAX and key.isascii() and key.isprintable()):
        raise BoardError(f"the key must be {KEY_BYTES_MIN}..{KEY_BYTES_MAX} printable ASCII characters, or empty")


class Console:
    """main's console over PORT; opening it restarts the board, as the bridge resets the chip on every open."""

    def __init__(self, port_name: str) -> None:
        self.port = serial.serial_for_url(port_name, BAUD, timeout=POLL_S, do_not_open=True)
        # Lines released: over the RFC 2217 bridge a raised DTR is esptool's cue for the bootloader (KEHOACH 2.1).
        self.port.dtr = False
        self.port.rts = False
        self.port.open()
        self.seen = b""

    def close(self) -> None:
        self.port.close()

    def wait(self, pattern: re.Pattern[bytes] | bytes, timeout_s: float) -> re.Match[bytes] | None:
        """Read until pattern shows up in what the board printed since the last wait, or timeout_s passes."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.seen += self.port.read(self.port.in_waiting or 1)
            found = re.search(pattern if isinstance(pattern, re.Pattern) else re.escape(pattern), self.seen)
            if found:
                self.seen = self.seen[found.end() :]
                return found
        return None

    def prompt(self) -> None:
        self.port.write(b"\r\n")
        if self.wait(PROMPT, PROMPT_TIMEOUT_S) is None:
            raise BoardError("no 'sr> ' prompt: flash the product with its console first (make fw-bench-flash)")
        time.sleep(SETTLE_S)
        self.port.reset_input_buffer()
        self.seen = b""

    def run(self, line: str, answer: re.Pattern[str], missing: str = "no answer from the board") -> str:
        """Send one console line and return the first group of its answer; the echo of the line is never kept."""
        self.port.write(line.encode() + b"\r\n")
        reply = self.wait(PROMPT, REPLY_TIMEOUT_S)
        text = (
            self.seen.decode(errors="replace")
            if reply is None
            else reply.string[: reply.start()].decode(errors="replace")
        )
        found = answer.search(text)
        if found is None:
            raise BoardError(f"{line.split(' ')[0]}: {missing}")
        return found.group(1)


def join(port_name: str, ssid: str, key: str) -> str:
    """Store ssid and key, restart, and return the board's address once it has one."""
    console = Console(port_name)
    try:
        console.prompt()
        line = f"wifi set -x {ssid.encode().hex()}" + (f" {quoted(key)}" if key else "")
        status = console.run(line, WIFI_SET)
        if status != "ESP_OK":
            raise BoardError(f"wifi set: {status}")
    finally:
        console.close()
    console = Console(port_name)
    try:
        address = console.wait(STA_IP, LINK_TIMEOUT_S)
    finally:
        console.close()
    if address is None:
        raise BoardError(f"board B did not join {ssid} in {LINK_TIMEOUT_S:.0f} s: is the key right?")
    return address.group(1).decode()


def point_at(port_name: str, pc_ip: str, stream_port: int) -> str:
    """Point the broker and the stream at pc_ip, keeping the broker's scheme and port; return the new mqtt_uri."""
    console = Console(port_name)
    try:
        console.prompt()
        missing = "board B has no device/mqtt_uri: set it once from the console"
        uri = with_host(console.run("nvs get device mqtt_uri", NVS_STR, missing), pc_ip)
        for line in (
            f"nvs set device mqtt_uri {uri}",
            f"nvs set device stream_host {pc_ip}",
            f"nvs set device stream_port {stream_port} -t u16",
        ):
            status = console.run(line, NVS_SET)
            if status != "ESP_OK":
                raise BoardError(f"{line.rsplit(' ', 1)[0]}: {status}")
    finally:
        console.close()
    return uri


def broker_reached(port_name: str) -> bool:
    """Restart the board and tell whether its MQTT session comes up."""
    console = Console(port_name)
    try:
        return console.wait(SESSION_UP, LINK_TIMEOUT_S) is not None
    finally:
        console.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", required=True, help="board B's serial port, the bridge's URL by default")
    args = parser.parse_args(argv)
    try:
        stream_port = record_config().stream_port
        default = current_ssid(windows("netsh.exe", "wlan", "show", "interfaces"))
        ssid = input(f"Wi-Fi network [{default}]: ").strip() or default
        if not ssid:
            raise BoardError("no network: Windows is on none, type one")
        key = getpass.getpass(f"key for {ssid} (Enter: the key Windows saved for it): ")
        if not key:
            key = saved_key(windows("netsh.exe", "wlan", "show", "profile", f"name={ssid}", "key=clear"))
            print(f"using the key Windows saved for {ssid}" if key else f"no key saved for {ssid}: an open network")
        check_key(key)
        board_ip = join(args.port, ssid, key)
        print(f"board B joined {ssid} as {board_ip}")
        pc_ip = near(board_ip, windows("ipconfig.exe"))
        if not pc_ip:
            print(f"this PC has no address on {board_ip.rsplit('.', 1)[0]}.0/24: join {ssid} too, then run make wifi")
            return 1
        uri = point_at(args.port, pc_ip, stream_port)
        print(f"broker {uri}, stream {pc_ip}:{stream_port}")
        if not broker_reached(args.port):
            print(f"no MQTT session in {LINK_TIMEOUT_S:.0f} s: is the broker running in Docker Desktop on {pc_ip}?")
            return 1
        print("board B is on the broker")
    except (BoardError, ConfigError, serial.SerialException) as err:
        print(f"wifi: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
