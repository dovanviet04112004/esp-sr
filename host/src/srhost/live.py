"""Watch the boards live: state, health, direction, voice flag, level, the speakers a board keeps, and events as they
arrive with the speaker each command was checked against (KEHOACH 3.17, 4.6, 7.7).

On a terminal each frame is written over the last, cropped to the window; piped, one line is printed per message.
Board C's pictures and detect's boxes land under the data root as they arrive (srhost.vision).
Run: uv run python -m srhost.live [--device <deviceId>] [--jsonl <file>]
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from pathlib import Path

from srhost.config import ConfigError, mqtt_config, record_config
from srhost.mqtt_rx import JsonlLog, Message, Receiver
from srhost.speakers import ENROL_COMMANDS
from srhost.vision import IMAGES_DIR, Assembler, describe_boxes, keep, stem_of

REFRESH_S = 0.2
EVENTS_SHOWN = 8
KEPT_SHOWN = 4
BAR_WIDTH = 20
LEVEL_FLOOR_DBFS = -90
DOA_MAX_DEG = 180
PERMILLE = 1000
KIB = 1024
E7 = 1e7
E1 = 10
MG_PER_G = 1000
CLEAR = "\x1b[H\x1b[2J"
HOME = "\x1b[H"
ERASE_LINE = "\x1b[K"
ERASE_BELOW = "\x1b[J"
OFFLINE = "OFFLINE"


@dataclass
class Board:
    """The latest of each kind of message from one device."""

    status: dict | None = None
    heartbeat: dict | None = None
    telemetry: dict | None = None
    sensors: dict | None = None
    roster: dict | None = None
    events: deque[Message] = field(default_factory=lambda: deque(maxlen=EVENTS_SHOWN))
    rejected: int = 0
    last_error: str | None = None
    seen_monotonic: float = 0.0


def bar(value: float, low: float, high: float, width: int = BAR_WIDTH) -> str:
    filled = round(width * min(max((value - low) / (high - low), 0.0), 1.0))
    return "█" * filled + "░" * (width - filled)


def doa_track(doa_deg: int, width: int = BAR_WIDTH) -> str:
    if doa_deg < 0:
        return "·" * width
    at = round((width - 1) * doa_deg / DOA_MAX_DEG)
    return "·" * at + "|" + "·" * (width - 1 - at)


def roster_names(roster: dict | None) -> dict[str, str]:
    return {p["id"]: p["name"] for p in roster["speakers"]} if roster else {}


def describe_speaker(event: dict, names: dict[str, str] | None = None) -> str:
    """The speaker part of an event, named as the board's roster names them: who an enrolment counts for and how far
    it is, or the closest voiceprint's person with its cosine; empty without one."""
    if "speakerId" not in event:
        return ""
    who = event["speakerId"]
    if names and who in names:
        who += f" {names[who]}"
    if "enrolCount" in event:
        return f" · enrolling {who} {event['enrolCount']}/{ENROL_COMMANDS}"
    cosine = f" {event['speakerPermille'] / PERMILLE:.2f}" if "speakerPermille" in event else ""
    return f" · speaker {who}{cosine}"


def local_clock(rx_utc: str, tz: tzinfo | None = None) -> str:
    """HH:MM:SS of an ISO receive time on this machine's clock, or on tz."""
    return datetime.fromisoformat(rx_utc).astimezone(tz).strftime("%H:%M:%S")


def describe_event(message: Message, names: dict[str, str] | None = None) -> str:
    event = message.payload or {}
    kind = event.get("kind")
    clock = local_clock(message.rx_utc)
    if message.kind == "detections":
        return f"{clock} DETECT {describe_boxes(event)}"
    if kind == "CAPTURE":
        kept = "saved" if event["saved"] else "not saved"
        return f"{clock} CAPTURE {stem_of(event['boot'], event['captureSeq'])} {event['imageBytes']} bytes, {kept}"
    if kind == "WAKE":
        text = f"WAKE score {event['scorePermille'] / PERMILLE:.2f}"
    elif kind == "COMMAND":
        text = (
            f"COMMAND {event['commandId']} score {event['scorePermille'] / PERMILLE:.2f}"
            f" margin {event['marginPermille'] / PERMILLE:.2f}"
        )
    else:
        text = f"{kind} {event.get('code', '')}"
        if event.get("commandId"):
            text += f" {event['commandId']}"
    text += describe_speaker(event, names)
    if event.get("doaDeg", -1) >= 0:
        text += f" at {event['doaDeg']}°"
    return f"{clock} {text}"


def describe_roster(roster: dict) -> str:
    """The speakers a board keeps, whether each has a voiceprint, and the enrolment under way."""
    people = ", ".join(
        f"{p['id']} {p['name']} ({'enrolled' if p['enrolled'] else 'no voiceprint'})" for p in roster["speakers"]
    )
    text = f"v{roster['version']} check {'on' if roster['check'] else 'off'}: {people or 'nobody'}"
    if "enrollingId" in roster:
        text += f"; enrolling {roster['enrollingId']} {roster.get('enrolCount', 0)}/{ENROL_COMMANDS}"
    return text


def describe_heartbeat(hb: dict) -> str:
    drops = " ".join(
        f"{name} {hb[key]}"
        for name, key in (
            ("dma", "dmaOverflows"),
            ("frames", "framesDropped"),
            ("clean", "cleanDropped"),
            ("events", "eventsDropped"),
            ("stream", "streamDropped"),
        )
        if key in hb
    )
    text = (
        f"up {hb['uptimeS']} s  internal {hb['heapInternalFree'] / KIB:.1f} KiB"
        f" (min {hb['heapInternalMin'] / KIB:.1f})  dropped: {drops}"
    )
    if "rssiDbm" in hb:
        text += f"  rssi {hb['rssiDbm']} dBm"
    return text


def describe_sensors(sensors: dict) -> str:
    gps, ir = sensors["gps"], sensors["ir"]
    if gps["hasFix"]:
        place = f"{gps['latE7'] / E7:.5f} {gps['lonE7'] / E7:.5f}, {gps['satellites']} satellites"
    else:
        place = "no antenna" if gps["antennaOpen"] else f"no fix, {gps['inView']} in view"
    text = f"gps {place}  ir {'ON' if ir['active'] else 'off'}, {ir['hits']} hits"
    if "motion" in sensors:
        motion = sensors["motion"]
        accel = " ".join(f"{mg / MG_PER_G:+.2f}" for mg in motion["accelMg"])
        text += f"  accel {accel} g  {motion['tempCE1'] / E1:.1f} C"
    if "adc" in sensors:
        text += "  adc " + " ".join(str(mv) for mv in sensors["adc"]["channelsMv"]) + " mV"
    return text


def describe_sample(telemetry: dict) -> str:
    sample = telemetry["samples"][-1]
    return (
        f"{telemetry['state']:<7} doa {doa_track(sample['doaDeg'])} {sample['doaDeg']:>4}° conf {sample['doaConf']:>3}"
        f"  vad {'●' if sample['vad'] else '○'}  level {bar(sample['levelDbfs'], LEVEL_FLOOR_DBFS, 0)}"
        f" {sample['levelDbfs']:>4} dBFS  gain {sample['gainDb']:+d} dB"
    )


class View:
    """Folds messages into per-device state and renders it; update runs on the MQTT thread, render on main."""

    def __init__(self) -> None:
        self._boards: dict[str, Board] = {}
        self._lock = threading.Lock()
        self.stray = 0
        self.kept: deque[str] = deque(maxlen=KEPT_SHOWN)

    def update(self, message: Message) -> None:
        with self._lock:
            if message.device is None:
                self.stray += 1
                return
            board = self._boards.setdefault(message.device, Board())
            board.seen_monotonic = time.monotonic()
            if message.rejected:
                board.rejected += 1
                board.last_error = message.error
            elif message.kind in ("event", "detections"):
                board.events.append(message)
            elif message.kind == "roster":
                board.roster = message.payload
            elif message.kind in ("status", "heartbeat", "telemetry", "sensors"):
                setattr(board, message.kind, message.payload)

    def render(self, now_monotonic: float | None = None) -> list[str]:
        now_monotonic = now_monotonic if now_monotonic is not None else time.monotonic()
        with self._lock:
            if not self._boards:
                return ["waiting for a board…"]
            lines: list[str] = []
            for device, board in sorted(self._boards.items()):
                status = board.status or {}
                if status.get("state") == OFFLINE:
                    lines.append(f"{device}  {OFFLINE}  fw {status.get('fw', '?')}")
                    continue
                lines.append(
                    f"{device}  {status.get('state', '?')}  fw {status.get('fw', '?')}"
                    f"  seen {now_monotonic - board.seen_monotonic:.0f} s ago"
                )
                if board.heartbeat:
                    lines.append(f"  health  {describe_heartbeat(board.heartbeat)}")
                if board.telemetry:
                    lines.append(f"  front   {describe_sample(board.telemetry)}")
                if board.sensors:
                    lines.append(f"  sensors {describe_sensors(board.sensors)}")
                if board.roster:
                    lines.append(f"  speakers {describe_roster(board.roster)}")
                names = roster_names(board.roster)
                lines.extend(f"  event   {describe_event(e, names)}" for e in reversed(board.events))
                if board.rejected:
                    lines.append(f"  REJECTED {board.rejected} payloads, last: {board.last_error}")
            if self.stray:
                lines.append(f"{self.stray} messages on topics outside the contract")
            lines.extend(f"kept  {line}" for line in self.kept)
            return lines

    def names(self, device: str | None) -> dict[str, str]:
        with self._lock:
            board = self._boards.get(device) if device else None
            return roster_names(board.roster if board else None)


def frame_of(lines: list[str], columns: int, rows: int) -> str:
    """One frame to write over the last, cropped so it neither wraps nor scrolls; clearing first would flicker."""
    body = "".join(f"{line[: columns - 1]}{ERASE_LINE}\n" for line in lines[: rows - 1])
    return f"{HOME}{body}{ERASE_BELOW}"


def one_line(message: Message, names: dict[str, str] | None = None) -> str:
    if message.rejected:
        return f"{message.rx_utc} {message.topic} REJECTED {message.error}"
    if message.kind in ("event", "detections"):
        return f"{message.device} event {describe_event(message, names)}"
    if message.kind == "image":
        return f"{message.device} image chunk {len(message.body or b'')} bytes"
    if message.kind == "heartbeat":
        return f"{message.device} health {describe_heartbeat(message.payload)}"
    if message.kind == "telemetry":
        return f"{message.device} front {describe_sample(message.payload)}"
    if message.kind == "roster":
        return f"{message.device} speakers {describe_roster(message.payload)}"
    if message.kind == "sensors":
        return f"{message.device} sensors {describe_sensors(message.payload)}"
    return f"{message.device} {message.kind} {message.payload}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", help="one deviceId; every board otherwise")
    parser.add_argument("--jsonl", type=Path, help="also append every message to this file")
    args = parser.parse_args(argv)
    redraw = sys.stdout.isatty()
    logging.basicConfig(level=logging.ERROR if redraw else logging.INFO, format="%(asctime)s %(message)s")
    try:
        cfg = mqtt_config()
        assembler = Assembler(record_config().data_root / IMAGES_DIR)
    except ConfigError as err:
        print(f"live: {err}", file=sys.stderr)
        return 2
    view = View()
    sink = JsonlLog(args.jsonl) if args.jsonl else None

    def on_message(message: Message) -> None:
        view.update(message)
        if sink:
            sink(message)
        kept = keep(assembler, message)
        view.kept.extend(kept)
        if not redraw:
            print(one_line(message, view.names(message.device)), flush=True)
            for line in kept:
                print(line, flush=True)

    receiver = Receiver(cfg, on_message, args.device)
    receiver.start()
    shown = ""
    if redraw:
        print(CLEAR, end="", flush=True)
    try:
        while True:
            if redraw:
                link = "connected" if receiver.connected.is_set() else "connecting"
                header = f"broker {cfg.host}:{cfg.port} {link}  (Ctrl-C quits)"
                size = shutil.get_terminal_size()
                frame = frame_of([header, "", *view.render()], size.columns, size.lines)
                if frame != shown:
                    print(frame, end="", flush=True)
                    shown = frame
            time.sleep(REFRESH_S)
    except KeyboardInterrupt:
        pass
    finally:
        receiver.stop()
        if sink:
            sink.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
