"""live folds each kind of message into the right line and shows what was rejected."""

from __future__ import annotations

import json

from srhost import live, mqtt_rx
from test_mqtt_rx import DEVICE, HEARTBEAT, TELEMETRY


def message(kind: str, payload: dict | bytes, device: str = DEVICE) -> mqtt_rx.Message:
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return mqtt_rx.parse(f"sr/{device}/up/{kind}", raw, rx_utc="2026-09-26T11:02:03.000+00:00")


def test_an_empty_view_says_it_waits() -> None:
    assert live.View().render() == ["waiting for a board…"]


def test_each_kind_lands_on_its_line() -> None:
    view = live.View()
    view.update(message("status", {"deviceId": DEVICE, "state": "ONLINE", "fw": "0.1.0+8f3fa79"}))
    view.update(message("heartbeat", HEARTBEAT))
    view.update(message("telemetry", TELEMETRY))
    view.update(message("event", {"deviceId": DEVICE, "seq": 9, "kind": "WAKE", "scorePermille": 930, "doaDeg": 45}))
    view.update(
        message(
            "event",
            {
                "deviceId": DEVICE,
                "seq": 20,
                "kind": "COMMAND",
                "commandId": "bat_den",
                "scorePermille": 880,
                "marginPermille": 300,
            },
        )
    )
    text = "\n".join(view.render(now_monotonic=0.0))
    assert f"{DEVICE}  ONLINE  fw 0.1.0+8f3fa79" in text
    assert "up 12 s" in text and "dma 0" in text
    assert "LISTEN" in text and "vad ●" in text and "-30 dBFS" in text
    assert "11:02:03 WAKE score 0.93 at 45°" in text
    assert text.index("COMMAND bat_den score 0.88 margin 0.30") < text.index("WAKE")


def test_rejected_payloads_are_counted_with_the_last_reason() -> None:
    view = live.View()
    view.update(message("heartbeat", b"{broken"))
    view.update(message("heartbeat", {**HEARTBEAT, "uptimeS": -1}))
    assert "REJECTED 2 payloads, last: heartbeat schema: uptimeS" in "\n".join(view.render())


def test_the_doa_marker_moves_across_the_track_and_hides_without_a_direction() -> None:
    assert live.doa_track(0).startswith("|")
    assert live.doa_track(180).endswith("|")
    assert "|" not in live.doa_track(-1)


def test_the_level_bar_is_clamped() -> None:
    assert live.bar(-200, -90, 0) == "░" * live.BAR_WIDTH
    assert live.bar(10, -90, 0) == "█" * live.BAR_WIDTH
