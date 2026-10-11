"""RFC 2217 bridge to board B's CH340: Windows keeps the COM port, WSL reaches it over TCP (KEHOACH 2.1).

Runs on Windows' own Python with only pyserial beside it; make board-attach copies both over and starts it.
Run: python bridge.py --usb-id 1a86:7523 --host 172.27.224.1 --port 4011
"""

import argparse
import contextlib
import logging
import socket
import threading
import time
from collections.abc import Callable

import serial
import serial.rfc2217
from serial.rfc2217 import (
    COM_PORT_OPTION,
    SET_CONTROL,
    SET_CONTROL_DTR_OFF,
    SET_CONTROL_DTR_ON,
    SET_CONTROL_RTS_OFF,
    SET_CONTROL_RTS_ON,
)
from serial.tools import list_ports

ENTER_BOOT_S = 0.1  # esptool's classic reset: EN held low
RESET_DELAY_S = 0.05  # IO0 held low after EN rises
HOLD_S = 0.1  # hard reset: EN held low
FIND_S = 10.0  # usbipd hands a CH340 back within seconds
FIND_POLL_S = 0.5
MODEM_POLL_S = 1.0
JOIN_S = 2.0
BUFFER_BYTES = 1 << 16
RECV_BYTES = 1024


def bootloader_reset(port: serial.Serial) -> None:
    """esptool's classic reset: the chip comes up in its ROM bootloader."""
    port.dtr = False
    port.rts = True
    time.sleep(ENTER_BOOT_S)
    port.dtr = True
    port.rts = False
    time.sleep(RESET_DELAY_S)
    port.dtr = False


def hard_reset(port: serial.Serial) -> None:
    """esptool's hard reset: the chip comes up in its app."""
    port.rts = True
    time.sleep(HOLD_S)
    port.rts = False


def find_port(usb_id: str) -> str | None:
    """COM port of the first serial device with this USB "vid:pid", or None."""
    vid, pid = (int(part, 16) for part in usb_id.split(":"))
    for info in list_ports.comports():
        if (info.vid, info.pid) == (vid, pid):
            return info.device
    return None


def wait_for_port(usb_id: str, seconds: float) -> str | None:
    until = time.monotonic() + seconds
    while (name := find_port(usb_id)) is None and time.monotonic() < until:
        time.sleep(FIND_POLL_S)
    return name


def open_port(name: str) -> serial.Serial:
    """Open a COM port with DTR and RTS released, the lines that hold the chip in reset or in boot mode."""
    port = serial.serial_for_url(name, do_not_open=True, timeout=1)
    port.dtr = False
    port.rts = False
    port.open()
    port.set_buffer_size(rx_size=BUFFER_BYTES, tx_size=BUFFER_BYTES)
    return port


class EspPortManager(serial.rfc2217.PortManager):
    """RFC 2217 server side that plays esptool's reset sequences locally, as esp_rfc2217_server does.

    RFC 2217 carries DTR and RTS changes without their timing, so the first change of a sequence plays the whole
    sequence here and the rest of it is dropped.
    """

    def __init__(self, serial_port: serial.Serial, connection, logger=None) -> None:
        self.is_download_mode = False
        self.resets: list[threading.Thread] = []
        super().__init__(serial_port, connection, logger)

    def _play(self, sequence: Callable[[serial.Serial], None]) -> None:
        thread = threading.Thread(target=sequence, args=(self.serial,), daemon=True)
        self.resets.append(thread)
        thread.start()

    def _telnet_process_subnegotiation(self, suboption: bytes) -> None:
        if suboption[0:1] == COM_PORT_OPTION and suboption[1:2] == SET_CONTROL:
            control = suboption[2:3]
            if control == SET_CONTROL_DTR_OFF:
                self.is_download_mode = False
                self.serial.dtr = False
                return
            if control == SET_CONTROL_RTS_OFF and not self.is_download_mode:
                self._play(hard_reset)
                return
            if control == SET_CONTROL_DTR_ON and not self.is_download_mode:
                self.is_download_mode = True
                self._play(bootloader_reset)
                return
            if control in (SET_CONTROL_DTR_ON, SET_CONTROL_RTS_ON, SET_CONTROL_RTS_OFF):
                return
        super()._telnet_process_subnegotiation(suboption)


class Redirector:
    """One client's session: the board's bytes to the socket, the client's bytes through telnet to the board."""

    def __init__(self, port: serial.Serial, sock: socket.socket) -> None:
        self.serial = port
        self.socket = sock
        self._write_lock = threading.Lock()
        self.rfc2217 = EspPortManager(port, self)
        self.alive = True

    def write(self, data: bytes) -> None:
        with self._write_lock:
            self.socket.sendall(data)

    def reader(self) -> None:
        while self.alive:
            try:
                data = self.serial.read(self.serial.in_waiting or 1)
                if data:
                    self.write(b"".join(self.rfc2217.escape(data)))
            except OSError as err:
                if self.alive:
                    logging.error("board -> client: %s", err)
                break
        self.alive = False
        # A board gone from USB ends the session for the client too, which otherwise waits on its next read.
        with contextlib.suppress(OSError):
            self.socket.shutdown(socket.SHUT_RDWR)

    def poller(self) -> None:
        while self.alive:
            time.sleep(MODEM_POLL_S)
            try:
                self.rfc2217.check_modem_lines()
            except OSError:
                break

    def run(self) -> None:
        threads = [threading.Thread(target=self.reader, daemon=True), threading.Thread(target=self.poller, daemon=True)]
        for thread in threads:
            thread.start()
        while self.alive:
            try:
                data = self.socket.recv(RECV_BYTES)
                if not data:
                    break
                self.serial.write(b"".join(self.rfc2217.filter(data)))
            except OSError as err:
                if self.alive:
                    logging.error("client -> board: %s", err)
                break
        self.alive = False
        for thread in threads + self.rfc2217.resets:
            thread.join(JOIN_S)


def serve(usb_id: str, client: socket.socket) -> None:
    """Bridge one client to the board, whose COM port is found and opened afresh for every client."""
    name = find_port(usb_id)
    if name is None:
        logging.error("no COM port with USB id %s: plug board B in", usb_id)
        return
    try:
        port = open_port(name)
    except serial.SerialException as err:
        logging.error("%s", err)
        return
    try:
        Redirector(port, client).run()
    finally:
        with contextlib.suppress(OSError):
            port.dtr = False
            port.rts = False
        with contextlib.suppress(OSError):
            port.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="RFC 2217 bridge to board B's COM port")
    parser.add_argument("--usb-id", required=True, help='USB "vid:pid" of the board\'s serial chip, e.g. 1a86:7523')
    parser.add_argument("--host", required=True, help="address to listen on: the Windows side of WSL's NAT")
    parser.add_argument("--port", type=int, required=True, help="TCP port to listen on")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    name = wait_for_port(args.usb_id, FIND_S)
    if name is None:
        logging.error("no COM port with USB id %s: plug board B in", args.usb_id)
        return 1
    try:
        open_port(name).close()
    except serial.SerialException as err:
        logging.error("%s: does another program hold it?", err)
        return 1
    try:
        server = socket.create_server((args.host, args.port))
    except OSError as err:
        logging.error("cannot listen on %s:%d: %s", args.host, args.port, err)
        return 1
    logging.info("serving %s on %s:%d", name, args.host, args.port)
    with server, contextlib.suppress(KeyboardInterrupt):
        while True:
            client, address = server.accept()
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            logging.info("client %s:%d", *address)
            with client:
                serve(args.usb_id, client)
            logging.info("client gone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
