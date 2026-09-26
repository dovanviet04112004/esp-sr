"""Watch the boards live: state, health, direction, voice flag, level and events as they arrive (KEHOACH 4.6, 7.7).

On a terminal the screen is redrawn several times a second; piped, one line is printed per message.
Run: uv run python -m srhost.live [--device <deviceId>] [--jsonl <file>]
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from srhost.config import ConfigError, mqtt_config
from srhost.mqtt_rx import JsonlLog, Message, Receiver

REFRESH_S = 0.2
EVENTS_SHOWN = 8
BAR_WIDTH = 20
LEVEL_FLOOR_DBFS = -90
DOA_MAX_DEG = 180
PERMILLE = 1000
KIB = 1024
CLEAR = "\x1b[H\x1b[2J"


@dataclass
class Board:
    """The latest of each kind of message from one device."""

    status: dict | None = None
    heartbeat: dict | None = None
    telemetry: dict | None = None
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


def describe_event(message: Message) -> str:
    event = message.payload or {}
    kind = event.get("kind")
    clock = message.rx_utc[11:19]
    if kind == "WAKE":
        text = f"WAKE score {event['scorePermille'] / PERMILLE:.2f}"
    elif kind == "COMMAND":
        text = (
            f"COMMAND {event['commandId']} score {event['scorePermille'] / PERMILLE:.2f}"
            f" margin {event['marginPermille'] / PERMILLE:.2f}"
        )
    else:
        text = f"{kind} {event.get('code', '')}"
    if event.get("doaDeg", -1) >= 0:
        text += f" at {event['doaDeg']}°"
    return f"{clock} {text}"


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
            elif message.kind == "event":
                board.events.append(message)
            elif message.kind in ("status", "heartbeat", "telemetry"):
                setattr(board, message.kind, message.payload)

    def render(self, now_monotonic: float | None = None) -> list[str]:
        now_monotonic = now_monotonic if now_monotonic is not None else time.monotonic()
        with self._lock:
            if not self._boards:
                return ["waiting for a board…"]
            lines: list[str] = []
            for device, board in sorted(self._boards.items()):
                status = board.status or {}
                lines.append(
                    f"{device}  {status.get('state', '?')}  fw {status.get('fw', '?')}"
                    f"  seen {now_monotonic - board.seen_monotonic:.0f} s ago"
                )
                if board.heartbeat:
                    lines.append(f"  health  {describe_heartbeat(board.heartbeat)}")
                if board.telemetry:
                    lines.append(f"  front   {describe_sample(board.telemetry)}")
                lines.extend(f"  event   {describe_event(e)}" for e in reversed(board.events))
                if board.rejected:
                    lines.append(f"  REJECTED {board.rejected} payloads, last: {board.last_error}")
            if self.stray:
                lines.append(f"{self.stray} messages on topics outside the contract")
            return lines


def one_line(message: Message) -> str:
    if message.rejected:
        return f"{message.rx_utc} {message.topic} REJECTED {message.error}"
    if message.kind == "event":
        return f"{message.device} event {describe_event(message)}"
    if message.kind == "heartbeat":
        return f"{message.device} health {describe_heartbeat(message.payload)}"
    if message.kind == "telemetry":
        return f"{message.device} front {describe_sample(message.payload)}"
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
    except ConfigError as err:
        print(f"live: {err}", file=sys.stderr)
        return 2
    view = View()
    sink = JsonlLog(args.jsonl) if args.jsonl else None

    def on_message(message: Message) -> None:
        view.update(message)
        if sink:
            sink(message)
        if not redraw:
            print(one_line(message), flush=True)

    receiver = Receiver(cfg, on_message, args.device)
    receiver.start()
    try:
        while True:
            if redraw:
                link = "connected" if receiver.connected.is_set() else "connecting"
                header = f"broker {cfg.host}:{cfg.port} {link}  (Ctrl-C quits)"
                print(CLEAR + "\n".join([header, "", *view.render()]), flush=True)
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
