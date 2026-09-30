"""Run every board variant of KEHOACH 3.16 on board B over Wi-Fi TCP (make espsr-compare).

ESP-SR's nets go to the model partition by esptool; the board joins Wi-Fi from NVS and prints the address it listens
on; the PC sends each item's job and input and takes back every variant's result and output, checked against the
board's CRC32, as <item>/<variant>.wav and <item>/board.json; the report and costs come last through test_report.
ESPSR_COMPARE_ITEMS names interim/scenes/afe_compare; ESPSR_COMPARE_ONLY, when set, the items to run."""

from __future__ import annotations

import os
import re
import socket
import sys
from pathlib import Path

import esptool
import pytest
import yaml
from pytest_embedded_serial_esp.serial import EspSerial

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import items
from test_report import collect

TAG = "ESPSR"
BAUD = os.getenv("ESPBAUD", "921600")
LISTEN = re.compile(rb"ESPSR listen (\d+\.\d+\.\d+\.\d+):(\d+)")
LISTEN_TIMEOUT_S = 120  # boot, the model image mapped, Wi-Fi joined
LINK_TIMEOUT_S = 600  # between two results: the slowest variant


@EspSerial.use_esptool()
def esptool_run(serial: EspSerial, *args: str) -> None:
    esptool.main(["--baud", BAUD, *args], esp=serial.esp)


def write_flash(dut, offset: int, data: bytes) -> None:
    """Write data at offset and reset, so the app starts on what was written."""
    path = Path(dut.app.binary_path) / "espsr_write.bin"
    path.write_bytes(data)
    esptool_run(dut.serial, "--after", "hard-reset", "write-flash", hex(offset), str(path))


def recv_exact(sock: socket.socket, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise ConnectionError(f"the board closed the link {len(out)} bytes into {n}")
        out += chunk
    return bytes(out)


def run_item(sock: socket.socket, job: int, folder: Path, variants: list, gains: list[float]) -> list[str]:
    """One item through every variant, each output written as wav; the variants that failed. An ESP-SR variant its
    library refuses is a cell left empty (KEHOACH 3.16), not a failure: board.json keeps its status."""
    pcm, samples, channels = items.read_input(folder / items.INPUT)
    sock.sendall(items.job_bytes(job, folder.name, samples, channels, gains, variants) + pcm)
    failed = []
    for index, v in enumerate(variants):
        r = items.parse_result(recv_exact(sock, items.RESULT.size), job, index)
        items.record_costs(folder, {v.name: r})
        if r["status"] == 0:
            items.write_output(folder, v.name, recv_exact(sock, r["samples"] * 2), r["crc32"])
        sock.sendall(items.ACK)
        if r["status"] != 0 and (v.kind == "dsp_afe" or r["status"] != items.REFUSED):
            failed.append(f"{folder.name}/{v.name}: status {r['status']}")
        print(f"{folder.name}/{v.name}: status {r['status']}, {r['us_mean']} us a hop", flush=True)
    return failed


@pytest.mark.esp32s3
def test_espsr_compare(dut) -> None:
    root = Path(os.environ["ESPSR_COMPARE_ITEMS"])
    only = set(os.getenv("ESPSR_COMPARE_ONLY", "").split())
    cfg = yaml.safe_load(items.COMPARE.read_text())
    variants = items.board_variants(cfg)
    gains = items.balance()
    model_offset, _ = items.partition(items.MODEL_PARTITION)
    write_flash(dut, model_offset, items.model_image(Path(dut.app.binary_path) / "espsr_models").read_bytes())
    found = dut.expect(LISTEN, timeout=LISTEN_TIMEOUT_S)
    address = (found.group(1).decode(), int(found.group(2)))
    failed = []
    with socket.create_connection(address, timeout=LINK_TIMEOUT_S) as sock:
        for job, item in enumerate((i for i in cfg["items"] if not only or i["name"] in only), start=1):
            failed += run_item(sock, job, root / item["name"], variants, gains)
        sock.sendall(items.end_bytes())
    lines = collect(dut, TAG)
    print("\n".join(lines))
    assert not failed, "variants that failed, their reason in the report above:\n" + "\n".join(failed)
