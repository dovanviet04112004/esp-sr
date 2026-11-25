"""settings sends SET_CONFIG inside the device_cmd schema and reads the CONFIG_ answer from status and events."""

from __future__ import annotations

import json

import pytest

from srhost import mqtt_rx, settings
from test_commands import event, status
from test_mqtt_rx import DEVICE


def test_the_body_is_compact_inside_the_schema() -> None:
    raw = settings.body("afe/ns_engine", 1)
    assert raw == b'{"op":"SET_CONFIG","key":"afe/ns_engine","value":1}'
    assert mqtt_rx.schema_error("device_cmd", json.loads(raw)) is None


def test_the_answer_keeps_status_and_config_refusals_only() -> None:
    answer = settings.Answer()
    answer.take(status("ONLINE"))
    answer.take(event({"kind": "ERROR", "code": "CONFIG_REFUSED"}))
    answer.take(event({"kind": "ERROR", "code": "COMMANDS_INVALID", "commandId": "mo_den"}))
    answer.take(event({"kind": "REJECT", "code": "LOW_SCORE"}))
    assert answer.online is True
    assert [r["code"] for r in answer.refusals] == ["CONFIG_REFUSED"]


def test_a_refusal_names_its_code_and_meaning() -> None:
    answer = settings.Answer(online=True, refusals=[{"code": "CONFIG_REFUSED"}])
    [line] = settings.report(DEVICE, "afe/ns_engine", 1, answer, 5.0)
    assert line == f"{DEVICE} refused afe/ns_engine=1: CONFIG_REFUSED, {settings.MEANINGS['CONFIG_REFUSED']}"


@pytest.mark.parametrize(
    ("online", "expected"),
    [
        (None, f"no status from {DEVICE}"),
        (False, f"{DEVICE} is offline"),
        (True, f"sent afe/ns_engine=0 to {DEVICE}; no refusal within 5 s"),
    ],
)
def test_without_a_refusal_the_report_says_whether_the_board_is_there(online: bool | None, expected: str) -> None:
    [line] = settings.report(DEVICE, "afe/ns_engine", 0, settings.Answer(online=online), 5.0)
    assert line.startswith(expected)


@pytest.mark.parametrize(
    "argv",
    [
        ["--device", DEVICE, "--key", "afe/ns_engine"],
        ["--device", "+", "--key", "afe/ns_engine", "--value", "1"],
        ["--device", DEVICE, "--key", "ns_engine", "--value", "1"],
    ],
)
def test_a_device_a_key_of_two_parts_and_a_value_are_required(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        settings.main(argv)
    assert stop.value.code == 2
