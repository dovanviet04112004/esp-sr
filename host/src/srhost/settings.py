"""Send SET_CONFIG to one board on down/cmd, then show what the board answers (KEHOACH 6.2, 7.3).

The board takes afe/ns_engine for now: 0 runs the OM-LSA floor in the ns slot, 1 the net its build carries. It writes
NVS and swaps on its next hop; success is silent, a refusal comes back as an ERROR event with a CONFIG_ code.
Run: uv run python -m srhost.settings --device <deviceId> --key afe/ns_engine --value <0|1>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from dataclasses import dataclass, field

import paho.mqtt.client as mqtt

from srhost.config import ConfigError, MqttConfig, mqtt_config
from srhost.generated import topics
from srhost.generated.payload import SCHEMAS
from srhost.mqtt_rx import KEEPALIVE_S, Message, parse

ANSWER_WAIT_S = 5.0
BROKER_WAIT_S = 5.0
DEVICE_ID = re.compile(SCHEMAS["status"]["properties"]["deviceId"]["pattern"])
KEY = re.compile(SCHEMAS["device_cmd"]["properties"]["key"]["pattern"])
REFUSAL_PREFIX = "CONFIG_"
MEANINGS = {
    "CONFIG_REFUSED": "the board does not take this key or value, or its build has no ns net",
    "CONFIG_NOT_SAVED": "in use until the board restarts: NVS was not written",
}


@dataclass
class Answer:
    """What the board said while the tool listened: its retained status and every CONFIG_ refusal."""

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


def body(key: str, value: int) -> bytes:
    """The device_cmd payload of SET_CONFIG."""
    return json.dumps({"op": "SET_CONFIG", "key": key, "value": value}, separators=(",", ":")).encode("utf-8")


def send(cfg: MqttConfig, device: str, payload: bytes, wait_s: float) -> Answer:
    """Publish payload on the board's down/cmd, then gather its answer for wait_s seconds; the status and event topics
    are subscribed first, so a refusal the board sends at once is not missed."""
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
        spec = topics.BY_ID["cmd"]
        sent = client.publish(spec.build(device), payload, qos=spec.qos, retain=spec.retain)
        if sent.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError(f"publish to {cfg.host}:{cfg.port} failed: {mqtt.error_string(sent.rc)}")
        sent.wait_for_publish(BROKER_WAIT_S)
        if not sent.is_published():
            raise ConnectionError(f"broker {cfg.host}:{cfg.port} did not acknowledge SET_CONFIG")
        time.sleep(wait_s)
    finally:
        client.disconnect()
        client.loop_stop()
    return answer


def report(device: str, key: str, value: int, answer: Answer, wait_s: float) -> list[str]:
    """One line per refusal; else whether the board was there to take it."""
    if answer.refusals:
        return [
            f"{device} refused {key}={value}: {r['code']}, {MEANINGS.get(r['code'], 'a code this tool does not know')}"
            for r in answer.refusals
        ]
    if answer.online is None:
        return [f"no status from {device}: is the deviceId right? SET_CONFIG is not retained, send it again"]
    if not answer.online:
        return [f"{device} is offline: SET_CONFIG is not retained, send it again once it is online"]
    return [f"sent {key}={value} to {device}; no refusal within {wait_s:.0f} s, its boot log shows the value kept"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", required=True, help="deviceId of the board, as its boot log prints it")
    parser.add_argument("--key", required=True, help="<namespace>/<key> of KEHOACH 6.2, afe/ns_engine for now")
    parser.add_argument("--value", required=True, type=int, help="the integer to set")
    args = parser.parse_args(argv)
    if not DEVICE_ID.fullmatch(args.device):
        parser.error(f"--device {args.device!r} is not a deviceId")
    if not KEY.fullmatch(args.key):
        parser.error(f"--key {args.key!r} is not <namespace>/<key>")
    try:
        answer = send(mqtt_config(), args.device, body(args.key, args.value), ANSWER_WAIT_S)
    except (ConfigError, OSError) as err:
        print(f"settings: {err}", file=sys.stderr)
        return 2
    print("\n".join(report(args.device, args.key, args.value, answer, ANSWER_WAIT_S)))
    return 1 if answer.refusals else 0


if __name__ == "__main__":
    raise SystemExit(main())
