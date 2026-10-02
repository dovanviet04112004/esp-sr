"""Send a command set to one board on down/commands, retained, then show what the board answers (KEHOACH 4.6, 7.3).

Sets repeating an id or a normalised line are refused, as the board rejects tied lines forever; version is the Unix
time of the send, so the retained copy a board gets again on reconnecting is not new to it. Success is silent.
Run: uv run python -m srhost.commands --device <deviceId> (<set.json> | --clear)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import paho.mqtt.client as mqtt

from srhost.config import ConfigError, MqttConfig, mqtt_config
from srhost.generated import topics
from srhost.generated.payload import SCHEMAS
from srhost.mqtt_rx import KEEPALIVE_S, Message, parse, schema_error

ANSWER_WAIT_S = 5.0
BROKER_WAIT_S = 5.0
VERSION_MAX = SCHEMAS["command_set"]["properties"]["version"]["maximum"]
DEVICE_ID = re.compile(SCHEMAS["status"]["properties"]["deviceId"]["pattern"])
REFUSAL_PREFIX = "COMMANDS_"
MEANINGS = {
    "COMMANDS_INVALID": "not a command set, or lang_vi cannot read a line",
    "COMMANDS_REFUSED": "this image cannot take a new command set",
    "COMMANDS_NOT_SAVED": "in use until the board restarts: set.json was not written",
}


class SetError(ValueError):
    """The file is not a command set this tool sends; the message says what to fix."""


def normalised(line: str) -> str:
    return " ".join(unicodedata.normalize("NFC", line).casefold().split())


def repeats(commands: list[dict]) -> list[str]:
    """Each id, and each line once normalised, that more than one command holds."""
    found = []
    for name, key in (("id", lambda c: c["id"]), ("line", lambda c: normalised(c["text"]))):
        owners: dict[str, list[str]] = {}
        for command in commands:
            owners.setdefault(key(command), []).append(command["id"])
        found += [f"{name} {value!r} in {', '.join(ids)}" for value, ids in owners.items() if len(ids) > 1]
    return found


def load_set(path: Path) -> dict:
    """The command set in path, checked against its schema and for repeated ids and lines."""
    try:
        command_set = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
        raise SetError(f"{path}: {err}") from err
    error = schema_error("command_set", command_set)
    if error is not None:
        raise SetError(f"{path}: {error}")
    found = repeats(command_set["commands"])
    if found:
        raise SetError(f"{path}: {'; '.join(found)}")
    return command_set


def stamped(command_set: dict, now_s: float) -> dict:
    """The set with version set to the Unix time of the send, since the board tells sets apart by version."""
    return {**command_set, "version": min(max(int(now_s), 1), VERSION_MAX)}


def body(command_set: dict) -> bytes:
    return json.dumps(command_set, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@dataclass
class Answer:
    """What the board said while the tool listened: its retained status and every COMMANDS_ refusal."""

    online: bool | None = None
    refusals: list[dict] = field(default_factory=list)

    def take(self, message: Message) -> None:
        if message.rejected:
            return
        payload = message.payload
        if message.kind == "status":
            self.online = payload["state"] == "ONLINE"
        elif message.kind == "event" and payload["kind"] == "ERROR" and payload["code"].startswith(REFUSAL_PREFIX):
            self.refusals.append(payload)


def send(cfg: MqttConfig, device: str, payload: bytes, wait_s: float) -> Answer:
    """Publish payload retained on the board's down/commands, then gather its answer for wait_s seconds.

    The status and event topics are subscribed first, so a refusal the board sends at once is not missed.
    """
    answer = Answer()
    subscribed = threading.Event()
    listened = [(topics.BY_ID[t].build(device), topics.BY_ID[t].qos) for t in ("status", "event")]
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(cfg.user, cfg.password)

    def connected(c: mqtt.Client, _userdata, _flags, reason, _properties) -> None:
        if not reason.is_failure:
            c.subscribe(listened)

    client.on_connect = connected
    client.on_subscribe = lambda *_: subscribed.set()
    client.on_message = lambda _c, _u, msg: answer.take(parse(msg.topic, msg.payload))
    client.connect(cfg.host, cfg.port, KEEPALIVE_S)
    client.loop_start()
    try:
        if not subscribed.wait(BROKER_WAIT_S):
            raise ConnectionError(f"broker {cfg.host}:{cfg.port} did not take the subscription of {cfg.user}")
        spec = topics.BY_ID["commands"]
        sent = client.publish(spec.build(device), payload, qos=spec.qos, retain=spec.retain)
        if sent.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError(f"publish to {cfg.host}:{cfg.port} failed: {mqtt.error_string(sent.rc)}")
        sent.wait_for_publish(BROKER_WAIT_S)
        if not sent.is_published():
            raise ConnectionError(f"broker {cfg.host}:{cfg.port} did not acknowledge the set")
        time.sleep(wait_s)
    finally:
        client.disconnect()
        client.loop_stop()
    return answer


def report(device: str, command_set: dict, answer: Answer, wait_s: float) -> list[str]:
    """One line per refusal; else whether the board is there to take the set."""
    version = command_set["version"]
    if answer.refusals:
        lines = []
        for refusal in answer.refusals:
            code = refusal["code"]
            line = f"{device} refused v{version}: {code}, {MEANINGS.get(code, 'a code this tool does not know')}"
            lines.append(line + (f"; line of {refusal['commandId']}" if "commandId" in refusal else ""))
        return lines
    if answer.online is None:
        return [f"no status from {device}: is the deviceId right? The set stays retained for it"]
    if not answer.online:
        return [f"{device} is offline: it takes v{version} when it connects again"]
    return [
        f"sent v{version}, {len(command_set['commands'])} commands, to {device}; no refusal within {wait_s:.0f} s",
        "the board takes it once no utterance waits to be scored: say a new command to see its id",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", required=True, help="deviceId of the board, as its boot log prints it")
    parser.add_argument("set", nargs="?", default="", help="command set file, laid out as contracts/commands/*.json")
    parser.add_argument("--clear", action="store_true", help="drop the retained set; the board keeps the one it uses")
    args = parser.parse_args(argv)
    if not DEVICE_ID.fullmatch(args.device):
        parser.error(f"--device {args.device!r} is not a deviceId")
    if args.clear == bool(args.set):
        parser.error("give a command set file or --clear, not both")
    try:
        cfg = mqtt_config()
        if args.clear:
            send(cfg, args.device, b"", 0.0)
            print(f"retained set of {args.device} dropped; the board keeps the set it uses")
            return 0
        command_set = stamped(load_set(Path(args.set)), time.time())
        answer = send(cfg, args.device, body(command_set), ANSWER_WAIT_S)
    except (ConfigError, SetError, OSError) as err:
        print(f"commands: {err}", file=sys.stderr)
        return 2
    print("\n".join(report(args.device, command_set, answer, ANSWER_WAIT_S)))
    return 1 if answer.refusals else 0


if __name__ == "__main__":
    raise SystemExit(main())
