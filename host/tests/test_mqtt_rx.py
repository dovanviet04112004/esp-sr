"""mqtt_rx accepts what matches the contract and keeps everything else as a rejected line with its reason."""

from __future__ import annotations

import json
from pathlib import Path

import paho.mqtt.client as mqtt
import pytest

from srhost import mqtt_rx
from srhost.config import MqttConfig

DEVICE = "sr-b00001"
HEARTBEAT = {
    "deviceId": DEVICE,
    "fw": "0.1.0+8f3fa79",
    "uptimeS": 12,
    "heapInternalFree": 190000,
    "heapInternalMin": 180000,
    "dmaOverflows": 0,
    "framesDropped": 0,
    "cleanDropped": 0,
    "eventsDropped": 0,
}
TELEMETRY = {
    "deviceId": DEVICE,
    "seq": 3,
    "state": "LISTEN",
    "samples": [{"doaDeg": 90, "doaConf": 200, "vad": True, "levelDbfs": -30, "gainDb": 6}] * 10,
}


def body(payload: dict) -> bytes:
    return json.dumps(payload).encode()


def test_a_valid_heartbeat_is_accepted_with_its_device() -> None:
    message = mqtt_rx.parse(f"sr/{DEVICE}/up/heartbeat", body(HEARTBEAT))
    assert not message.rejected
    assert (message.kind, message.device, message.payload) == ("heartbeat", DEVICE, HEARTBEAT)


@pytest.mark.parametrize(
    ("topic", "raw", "reason"),
    [
        (f"sr/{DEVICE}/up/heartbeat", b"{not json", "not JSON"),
        (f"sr/{DEVICE}/up/heartbeat", b"\xff\xfe", "not JSON"),
        (f"sr/{DEVICE}/up/heartbeat", body({**HEARTBEAT, "uptimeS": -1}), "heartbeat schema: uptimeS"),
        (f"sr/{DEVICE}/up/heartbeat", body({**HEARTBEAT, "extra": 1}), "heartbeat schema"),
        (
            f"sr/{DEVICE}/up/telemetry",
            body({**TELEMETRY, "samples": [{**TELEMETRY["samples"][0], "levelDbfs": 5}] * 10}),
            "telemetry schema: samples/0/levelDbfs",
        ),
        ("sr/sr-other1/up/heartbeat", body(HEARTBEAT), "differs from the topic"),
        (f"sr/{DEVICE}/down/cmd", body({"op": "REBOOT"}), "not an up topic"),
        ("elsewhere/topic", body(HEARTBEAT), "not an up topic"),
    ],
)
def test_bad_messages_are_rejected_with_the_reason_and_raw_text(topic: str, raw: bytes, reason: str) -> None:
    message = mqtt_rx.parse(topic, raw)
    assert message.rejected
    assert reason in message.error
    assert message.payload is None
    assert message.raw == raw.decode("utf-8", errors="replace")


def test_the_log_keeps_one_line_per_message_and_counts_rejects(tmp_path: Path) -> None:
    path = tmp_path / "day" / "mqtt.jsonl"
    sink = mqtt_rx.JsonlLog(path)
    sink(mqtt_rx.parse(f"sr/{DEVICE}/up/heartbeat", body(HEARTBEAT), rx_utc="2026-09-26T11:00:00.000+00:00"))
    sink(mqtt_rx.parse(f"sr/{DEVICE}/up/telemetry", b"[]"))
    sink.close()
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert (sink.accepted, sink.rejected) == (1, 1)
    assert lines[0] == {
        "rx_utc": "2026-09-26T11:00:00.000+00:00",
        "topic": f"sr/{DEVICE}/up/heartbeat",
        "kind": "heartbeat",
        "device": DEVICE,
        "payload": HEARTBEAT,
    }
    assert lines[1]["raw"] == "[]" and "error" in lines[1] and "payload" not in lines[1]


def test_filters_cover_every_up_topic_and_no_down_topic() -> None:
    every = mqtt_rx.up_filters(None)
    assert [f for f, _ in every] == [
        "sr/+/up/status",
        "sr/+/up/heartbeat",
        "sr/+/up/telemetry",
        "sr/+/up/event",
        "sr/+/up/speakers",
        "sr/+/up/image",
        "sr/+/up/detections",
        "sr/+/up/sensors",
        "sr/+/up/note",
        "sr/+/up/journal",
    ]
    assert all(f.startswith(f"sr/{DEVICE}/up/") for f, _ in mqtt_rx.up_filters(DEVICE))


def test_a_binary_topic_must_name_a_device_that_can_be_one() -> None:
    message = mqtt_rx.parse("sr/../up/image", b"chunk")
    assert message.rejected and message.device is None
    assert not mqtt_rx.parse(f"sr/{DEVICE}/up/image", b"chunk").rejected


def test_a_handler_that_raises_leaves_the_receiver_running() -> None:
    def fail(_message: mqtt_rx.Message) -> None:
        raise OSError("disk full")

    cfg = MqttConfig(host="localhost", port=1883, user="u", password="p")
    receiver = mqtt_rx.Receiver(cfg, fail)
    msg = mqtt.MQTTMessage(topic=f"sr/{DEVICE}/up/heartbeat".encode())
    msg.payload = json.dumps(HEARTBEAT).encode()
    receiver._received(None, None, msg)
    assert receiver.failed == 1


def receiver_of(received: list[mqtt_rx.Message]) -> mqtt_rx.Receiver:
    cfg = MqttConfig(host="localhost", port=1883, user="u", password="p")
    return mqtt_rx.Receiver(cfg, received.append)


def arrive(receiver: mqtt_rx.Receiver, kind: str, payload: dict) -> None:
    msg = mqtt.MQTTMessage(topic=f"sr/{DEVICE}/up/{kind}".encode())
    msg.payload = json.dumps(payload).encode()
    receiver._received(None, None, msg)


EVENT = {"deviceId": DEVICE, "seq": 150, "kind": "WAKE", "scorePermille": 900}


def test_an_event_resent_within_the_window_is_taken_once(monkeypatch: pytest.MonkeyPatch) -> None:
    got: list[mqtt_rx.Message] = []
    receiver = receiver_of(got)
    now = 1000.0
    monkeypatch.setattr(mqtt_rx.time, "monotonic", lambda: now)
    arrive(receiver, "event", EVENT)
    now += 1.3
    arrive(receiver, "event", EVENT)
    now += 1.0
    arrive(receiver, "event", {**EVENT, "seq": 151})
    assert [m.payload["seq"] for m in got] == [150, 151]
    assert receiver.repeated == 1
    now += mqtt_rx.REPEAT_WINDOW_S
    arrive(receiver, "event", EVENT)
    assert len(got) == 3


def test_a_status_said_twice_is_two_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    got: list[mqtt_rx.Message] = []
    receiver = receiver_of(got)
    monkeypatch.setattr(mqtt_rx.time, "monotonic", lambda: 1000.0)
    status = {"deviceId": DEVICE, "state": "ONLINE", "fw": "0.1.0"}
    arrive(receiver, "status", status)
    arrive(receiver, "status", status)
    assert len(got) == 2


def test_stopping_a_receiver_that_never_connected_does_not_wait_for_paho() -> None:
    receiver = receiver_of([])
    receiver._client = None  # any call on it would raise
    receiver.stop()
