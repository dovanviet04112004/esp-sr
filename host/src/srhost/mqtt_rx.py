"""Subscribe to what boards send, check each payload against its schema, log every message as one JSON line.

A payload that is not JSON, breaks its schema or names another device is kept as a rejected line with
the reason, never dropped silently (KEHOACH 4.6, 7.3, CLAUDE.md 4.4).
Run: uv run python -m srhost.mqtt_rx --jsonl <file> [--device <deviceId>]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

import paho.mqtt.client as mqtt
from jsonschema import Draft202012Validator

from srhost.config import ConfigError, MqttConfig, mqtt_config
from srhost.generated import topics
from srhost.generated.payload import SCHEMAS

KEEPALIVE_S = 30
WILDCARD = "+"

log = logging.getLogger(__name__)
VALIDATORS = {name: Draft202012Validator(schema) for name, schema in SCHEMAS.items()}


@dataclass(frozen=True)
class Message:
    """One MQTT message from a board; payload is None and error says why when it was rejected."""

    rx_utc: str
    topic: str
    kind: str | None
    device: str | None
    payload: dict | None
    error: str | None = None
    raw: str | None = None

    @property
    def rejected(self) -> bool:
        return self.error is not None

    def to_json(self) -> str:
        return json.dumps({k: v for k, v in asdict(self).items() if v is not None}, ensure_ascii=False)


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def schema_error(schema: str, payload: object) -> str | None:
    errors = sorted(VALIDATORS[schema].iter_errors(payload), key=lambda e: list(e.absolute_path))
    if not errors:
        return None
    where = "/".join(str(p) for p in errors[0].absolute_path) or "payload"
    return f"{schema} schema: {where}: {errors[0].message}"


def parse(topic: str, body: bytes, rx_utc: str | None = None) -> Message:
    """Match the topic, decode the JSON and validate it; never raises on what a board sends."""
    rx_utc = rx_utc or now_utc()
    raw = body.decode("utf-8", errors="replace")
    matched = topics.match(topic)
    if matched is None or matched[0].direction != "up":
        return Message(rx_utc, topic, None, None, None, "not an up topic of contracts/mqtt_topics.yaml", raw)
    spec, device = matched
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        return Message(rx_utc, topic, spec.id, device, None, f"not JSON: {err}", raw)
    error = schema_error(spec.schema, payload)
    if error is None and payload.get("deviceId", device) != device:
        error = f"deviceId {payload['deviceId']!r} differs from the topic"
    if error is not None:
        return Message(rx_utc, topic, spec.id, device, None, error, raw)
    return Message(rx_utc, topic, spec.id, device, payload)


class JsonlLog:
    """Appends each message to a file as one line, flushed so a crash loses at most the line being written."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._out: TextIO = path.open("a", encoding="utf-8")
        self._lock = threading.Lock()
        self.accepted = 0
        self.rejected = 0

    def __call__(self, message: Message) -> None:
        with self._lock:
            self._out.write(message.to_json() + "\n")
            self._out.flush()
            if message.rejected:
                self.rejected += 1
            else:
                self.accepted += 1

    def close(self) -> None:
        with self._lock:
            self._out.close()


def up_filters(device: str | None) -> list[tuple[str, int]]:
    return [(t.build(device or WILDCARD), t.qos) for t in topics.TOPICS if t.direction == "up"]


class Receiver:
    """One broker connection that subscribes to every up topic and hands each parsed message to on_message.

    on_message runs on the paho network thread; it must be quick and thread safe.
    """

    def __init__(self, cfg: MqttConfig, on_message: Callable[[Message], None], device: str | None = None) -> None:
        self._cfg = cfg
        self._on_message = on_message
        self._filters = up_filters(device)
        self.connected = threading.Event()
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._client.username_pw_set(cfg.user, cfg.password)
        self._client.on_connect = self._connected
        self._client.on_disconnect = self._disconnected
        self._client.on_message = self._received

    def _connected(self, client: mqtt.Client, _userdata, _flags, reason, _properties) -> None:
        if reason.is_failure:
            log.error("broker %s:%d refused srhost: %s", self._cfg.host, self._cfg.port, reason)
            return
        client.subscribe(self._filters)
        self.connected.set()
        log.info("subscribed to %s", ", ".join(f for f, _ in self._filters))

    def _disconnected(self, _client, _userdata, _flags, reason, _properties) -> None:
        self.connected.clear()
        log.warning("broker connection lost: %s; paho reconnects", reason)

    def _received(self, _client, _userdata, msg: mqtt.MQTTMessage) -> None:
        message = parse(msg.topic, msg.payload)
        if message.rejected:
            log.warning("rejected %s: %s", message.topic, message.error)
        self._on_message(message)

    def start(self) -> None:
        self._client.connect_async(self._cfg.host, self._cfg.port, KEEPALIVE_S)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.disconnect()
        self._client.loop_stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--jsonl", type=Path, required=True, help="file to append one line per message to")
    parser.add_argument("--device", help="one deviceId; every board otherwise")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        cfg = mqtt_config()
    except ConfigError as err:
        print(f"mqtt_rx: {err}", file=sys.stderr)
        return 2
    sink = JsonlLog(args.jsonl)
    receiver = Receiver(cfg, sink, args.device)
    receiver.start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        receiver.stop()
        sink.close()
    print(f"{sink.accepted} messages logged, {sink.rejected} rejected, in {args.jsonl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
