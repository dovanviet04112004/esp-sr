"""Live bss on board B, one WAV per seat (E8-T5, KEHOACH 3.8, 7.4): make bss builds and flashes the app, then runs this.

Puts the board on this PC's network if it is not, as make wifi asks; then asks mode, seats, ns, seconds and delay
(BSS_LIVE_MODE, _SEATS, _NS, _SECONDS, _DELAY answer ahead), starts srhost.stream_rx in Docker, sends the job and shows
the status each second, seconds left first; Enter stops early. WAVs land in $SRPIPE_DATA_ROOT/cache/bss_live/<run>/.
"""

from __future__ import annotations

import getpass
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "host" / "src"))
from srhost import wifi as host_wifi
from test_report import collect

REPO = Path(__file__).resolve().parents[3]
TAG = "BSSLIVE"
CONTAINER = "sr-bss-live"
IMAGE = "python:3.12-slim"
DEFAULTS = {"mode": "learn", "seats": "2", "ns": "mean", "seconds": "30", "delay": "4"}
NS_HELP = {"off": "no noise suppression", "mean": "once on the mean, before the gates", "each": "on each seat"}
BOOT_TIMEOUT_S = 90
WIFI_TRIES = 3
LISTEN_TIMEOUT_S = 180
RECEIVER_GRACE_S = 10  # after the board's last line, for the last frames
DELAY_STEPS_PER_S = 2  # the menu offers the delay in 0.5 s steps
SUMMARY_FILE = "stream.json"  # what srhost.stream_rx writes beside the WAVs
LINE = rf"{TAG} \d+ ([^\r\n]*?) crc=[0-9a-f]{{8}}"
IPV4 = re.compile(r"IPv4[^:\r\n]*:\s*(\d+\.\d+\.\d+\.\d+)")
ADDRESS = re.compile(r"\d+\.\d+\.\d+\.\d+")
SUMMARY = re.compile(r"summary hops (\d+) .* dropped (\d+) dma_overflows (\d+)")


def board_line(dut, prefix: str) -> str:
    """The text of the next report line, which must start with prefix; a 'wifi down' or 'refused' line fails."""
    text = dut.expect(re.compile(LINE.encode()), timeout=BOOT_TIMEOUT_S).group(1).decode(errors="replace")
    if not text.startswith(prefix):
        pytest.fail(f"board B: {text}")
    return text.removeprefix(prefix).strip()


def limits_of(ready: str) -> dict[str, list[str]]:
    """'modes a,b ns x,y,z seats 2-4 seconds 5-600 delay 0-8' as lists of allowed answers, the delay in 0.5 s steps."""
    words = ready.split()
    pairs = dict(zip(words[::2], words[1::2], strict=True))
    spans = {}
    for key in ("seats", "seconds"):
        low, high = (int(n) for n in pairs[key].split("-"))
        spans[key] = [str(n) for n in range(low, high + 1)]
    low, high = (int(n) for n in pairs["delay"].split("-"))
    spans["delay"] = [
        f"{k / DELAY_STEPS_PER_S:g}" for k in range(low * DELAY_STEPS_PER_S, high * DELAY_STEPS_PER_S + 1)
    ]
    return {"mode": pairs["modes"].split(","), "ns": pairs["ns"].split(","), **spans}


def plain(answer: str, allowed: list[str]) -> str:
    """answer as allowed spells it: a number picks a named choice, and 4.0 is 4."""
    if answer.isdigit() and not allowed[0].isdigit() and 1 <= int(answer) <= len(allowed):
        return allowed[int(answer) - 1]
    return f"{float(answer):g}" if re.fullmatch(r"\d+(\.\d*)?", answer) else answer


def ask(name: str, allowed: list[str], default: str, preset: str, show: str) -> str:
    """preset when allowed, else a question until the answer is allowed."""
    if preset:
        if plain(preset, allowed) not in allowed:
            pytest.fail(f"{name}={preset} is not one of {', '.join(allowed)}")
        return plain(preset, allowed)
    while True:
        answer = plain(input(f"{name} {show} [{default}]: ").strip() or default, allowed)
        if answer in allowed:
            return answer
        print(f"  one of {', '.join(allowed)}")


def choose(limits: dict[str, list[str]]) -> dict[str, str]:
    """The run's answers, broadside always on two seats."""
    preset = {k: os.environ.get(f"BSS_LIVE_{k.upper()}", "") for k in DEFAULTS}
    shows = {
        "mode": " / ".join(f"{i + 1} {m}" for i, m in enumerate(limits["mode"])),
        "seats": f"{limits['seats'][0]}-{limits['seats'][-1]}",
        "ns": " / ".join(f"{i + 1} {n} ({NS_HELP.get(n, n)})" for i, n in enumerate(limits["ns"])),
        "seconds": f"{limits['seconds'][0]}-{limits['seconds'][-1]}",
        "delay": f"{limits['delay'][0]}-{limits['delay'][-1]} s by 0.5, 0 live",
    }
    job = {}
    for key in DEFAULTS:
        if key == "seats" and job["mode"] != "learn":
            job[key] = limits[key][0]
            continue
        job[key] = ask(key, limits[key], DEFAULTS[key], preset[key], shows[key])
    return job


def pc_address(board_ip: str) -> str:
    """This PC's address on the board's /24, as Windows reports it, or ""; the receiver listens there through Docker."""
    if os.environ.get("BSS_LIVE_HOST"):
        return os.environ["BSS_LIVE_HOST"]
    ipconfig = subprocess.run(["ipconfig.exe"], capture_output=True, check=False).stdout.decode(errors="replace")
    prefix = board_ip.rsplit(".", 1)[0] + "."
    return next((ip for ip in IPV4.findall(ipconfig) if ip.startswith(prefix)), "")


def wifi_line() -> str:
    """The console line that puts board B on a network, asked as make wifi asks; the key is never shown."""
    if sys.stdin is None or not sys.stdin.isatty():
        pytest.fail("board B is not on this PC's network: run make bss in a terminal to give it one")
    heard = host_wifi.scan_bands(host_wifi.windows("netsh.exe", "wlan", "show", "networks", "mode=bssid"))
    default = host_wifi.current_ssid(host_wifi.windows("netsh.exe", "wlan", "show", "interfaces"))
    if host_wifi.BAND_24 not in heard.get(default, {host_wifi.BAND_24}):
        default = ""
    while True:
        ssid = input(f"Wi-Fi network for board B [{default}]: ").strip() or default
        try:
            if not ssid:
                raise host_wifi.BoardError("no network: type a 2.4 GHz one")
            host_wifi.check_band(ssid, heard)
            key = getpass.getpass(f"key for {ssid} (Enter: the key Windows saved for it): ")
            if not key:
                profile = host_wifi.windows("netsh.exe", "wlan", "show", "profile", f"name={ssid}", "key=clear")
                key = host_wifi.saved_key(profile)
                print(f"using the key Windows saved for {ssid}" if key else f"no key saved for {ssid}: an open network")
            host_wifi.check_key(key)
        except host_wifi.BoardError as err:
            print(f"  {err}")
            continue
        return " ".join(["wifi", ssid.encode().hex()] + ([key.encode().hex()] if key else []))


def on_network(dut) -> tuple[str, str]:
    """Board B's address on this PC's network and its ready line, a network given to it first if need be."""
    text = board_line(dut, "wifi ")
    for _ in range(WIFI_TRIES):
        board_ip = text if ADDRESS.fullmatch(text) else ""
        ready = board_line(dut, "ready ") if board_ip else ""
        if board_ip and pc_address(board_ip):
            return board_ip, ready
        print(f"\nboard B: wifi {text}; it has to join this PC's network")
        dut.write(wifi_line() + "\n")
        stored = board_line(dut, "wifi set ")
        if not stored.endswith("ESP_OK"):
            pytest.fail(f"board B: wifi set {stored}")
        text = board_line(dut, "wifi ")
    pytest.fail(f"board B did not reach this PC's network in {WIFI_TRIES} tries")


@contextmanager
def receiver(out: Path, port: int, seconds: int) -> Iterator[subprocess.Popen]:
    """srhost.stream_rx in Docker, published on Windows so the board reaches it, writing into out."""
    data_root = os.environ["BSS_LIVE_DATA_ROOT"]
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, check=False)
    command = [
        "docker", "run", "--rm", "--name", CONTAINER, "--user", f"{os.getuid()}:{os.getgid()}",
        "--env-file", str(REPO / "host" / ".env"), "-e", "PYTHONPATH=/repo/host/src", "-p", f"{port}:{port}",
        "-v", f"{REPO}:/repo", "-v", f"{data_root}:{data_root}", IMAGE,
        "python", "-m", "srhost.stream_rx", "--out", str(out), "--duration-s", str(seconds),
    ]  # fmt: skip
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: [lines.put(line) for line in proc.stdout], daemon=True).start()
    seen: list[str] = []
    deadline = time.monotonic() + LISTEN_TIMEOUT_S
    while not any("listening on" in line for line in seen):
        try:
            seen.append(lines.get(timeout=max(0.1, deadline - time.monotonic())))
        except queue.Empty:
            subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, check=False)
            pytest.fail("srhost.stream_rx never listened: " + "".join(seen)[-500:])
    try:
        yield proc
    finally:
        try:
            proc.wait(timeout=RECEIVER_GRACE_S)
        except subprocess.TimeoutExpired:
            # Stopped early, it waits for audio that never comes; SIGTERM ends it between frames, WAVs closed.
            subprocess.run(["docker", "stop", "-t", str(RECEIVER_GRACE_S), CONTAINER], capture_output=True, check=False)
            try:
                proc.wait(timeout=RECEIVER_GRACE_S)
            except subprocess.TimeoutExpired:
                subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, check=False)
        while not lines.empty():
            seen.append(lines.get())
        print("".join(line for line in seen if "frames" in line or "gap" in line), end="")


def stop_on_enter(dut) -> None:
    """Send stop to the board once Enter is pressed; a piped or closed stdin sends nothing."""
    if sys.stdin is None or not sys.stdin.isatty():
        return
    if sys.stdin.readline():
        dut.write("stop\n")
        print("stopping: board B lets out what it heard, then closes the stream")


@pytest.fixture
def app(app):
    """pytest-embedded's app; esptool writes only images whose MD5 on board B differs, so a rerun skips the flash."""
    app.flash_args["write_flash_args"].append("--skip-flashed")
    return app


@pytest.mark.esp32s3
def test_bss_live(dut) -> None:
    board_line(dut, "app ")
    board_ip, ready = on_network(dut)
    limits = limits_of(ready)
    host = pc_address(board_ip)
    port = int(os.environ["BSS_LIVE_STREAM_PORT"])
    print(f"\nboard B on {board_ip}, receiver on {host}:{port}; Enter keeps [the default]")
    job = choose(limits)
    name = "_".join(
        [datetime.now().strftime("%Y%m%d_%H%M%S"), job["mode"], job["seats"], job["ns"], f"d{job['delay']}"]
    )
    out = Path(os.environ["BSS_LIVE_DATA_ROOT"]) / "cache" / "bss_live" / name
    out.mkdir(parents=True)
    (out / "job.json").write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
    with receiver(out, port, int(job["seconds"])):
        print(f"talk for {job['seconds']} s, one at a time, from different directions around board B; Enter stops")
        if job["delay"] != "0":
            print(f"the WAVs trail the room by {job['delay']} s: board B listens that much longer")
        delay = {"delay_ms": round(float(job["delay"]) * 1000)}
        fields = {**{k: v for k, v in job.items() if k != "delay"}, **delay, "host": host, "port": port}
        dut.write("job " + " ".join(f"{k}={v}" for k, v in fields.items()) + "\n")
        threading.Thread(target=stop_on_enter, args=(dut,), daemon=True).start()
        texts = collect(dut, TAG)
    (out / "status.txt").write_text("\n".join(texts) + "\n", encoding="utf-8")
    stream = json.loads((out / SUMMARY_FILE).read_text(encoding="utf-8")) if (out / SUMMARY_FILE).exists() else {}
    wavs = sorted(p.name for p in out.glob("*.wav"))
    print(f"{out}\n  {' '.join(wavs)}\nboard B runs bss_live until make product puts the product back")
    summary = next((SUMMARY.search(t) for t in texts if SUMMARY.search(t)), None)
    assert summary is not None, "board B sent no summary line"
    assert int(summary.group(2)) == 0 and int(summary.group(3)) == 0, f"board B lost hops: {summary.group(0)}"
    assert not stream.get("gaps"), f"the stream has gaps: {stream.get('gaps')}"
    assert {f"seat{j}.wav" for j in range(int(job["seats"]))} <= set(wavs), f"missing seat WAVs in {out}"
