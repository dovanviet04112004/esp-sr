"""commands sends only sets the board can tell apart and reads the board's answer from status and events."""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from srhost import commands, mqtt_rx
from srhost.config import REPO_ROOT
from test_mqtt_rx import DEVICE

DEFAULT_SET = REPO_ROOT / "contracts" / "commands" / "default_vi.json"
NOW_S = 1_790_000_000.7


def written(tmp_path: Path, command_set: dict) -> Path:
    path = tmp_path / "set.json"
    path.write_text(json.dumps(command_set, ensure_ascii=False), encoding="utf-8")
    return path


def two_commands(first: dict, second: dict) -> dict:
    return {"version": 1, "commands": [first, second]}


def event(payload: dict) -> mqtt_rx.Message:
    return mqtt_rx.parse(f"sr/{DEVICE}/up/event", json.dumps({"deviceId": DEVICE, "seq": 7, **payload}).encode())


def status(state: str) -> mqtt_rx.Message:
    return mqtt_rx.parse(f"sr/{DEVICE}/up/status", json.dumps({"deviceId": DEVICE, "state": state}).encode())


def test_the_default_set_loads_as_it_is() -> None:
    assert commands.load_set(DEFAULT_SET) == json.loads(DEFAULT_SET.read_text(encoding="utf-8"))


def test_a_set_outside_the_schema_is_refused_with_where(tmp_path: Path) -> None:
    path = written(tmp_path, {"version": 1, "commands": [{"id": "Bat_Den", "text": "bật đèn"}]})
    with pytest.raises(commands.SetError, match="commands/0/id"):
        commands.load_set(path)


def test_a_file_that_is_not_json_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "set.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(commands.SetError, match=r"set\.json"):
        commands.load_set(path)


def test_two_commands_with_one_id_are_refused(tmp_path: Path) -> None:
    path = written(tmp_path, two_commands({"id": "den", "text": "bật đèn"}, {"id": "den", "text": "tắt đèn"}))
    with pytest.raises(commands.SetError, match="id 'den' in den, den"):
        commands.load_set(path)


def test_one_line_written_two_ways_is_refused(tmp_path: Path) -> None:
    decomposed = unicodedata.normalize("NFD", "Bật  Đèn ")
    path = written(tmp_path, two_commands({"id": "bat_den", "text": "bật đèn"}, {"id": "mo_den", "text": decomposed}))
    with pytest.raises(commands.SetError, match="line 'bật đèn' in bat_den, mo_den"):
        commands.load_set(path)


def test_lines_that_differ_only_in_tone_are_two_commands(tmp_path: Path) -> None:
    command_set = two_commands({"id": "bat_den", "text": "bật đèn"}, {"id": "bat_den_2", "text": "bất đèn"})
    assert commands.load_set(written(tmp_path, command_set)) == command_set


def test_the_version_is_the_time_of_the_send_and_nothing_else_moves() -> None:
    command_set = commands.load_set(DEFAULT_SET)
    sent = commands.stamped(command_set, NOW_S)
    assert sent["version"] == int(NOW_S)
    assert sent["commands"] == command_set["commands"]
    assert command_set["version"] == 1


def test_the_body_is_compact_utf8_inside_the_schema() -> None:
    sent = commands.stamped(commands.load_set(DEFAULT_SET), NOW_S)
    raw = commands.body(sent)
    assert "bật đèn".encode() in raw
    assert b": " not in raw
    assert json.loads(raw) == sent
    assert mqtt_rx.schema_error("command_set", json.loads(raw)) is None


def test_the_answer_keeps_status_and_commands_refusals_only() -> None:
    answer = commands.Answer()
    answer.take(status("ONLINE"))
    answer.take(event({"kind": "ERROR", "code": "COMMANDS_INVALID", "commandId": "mo_den"}))
    answer.take(event({"kind": "ERROR", "code": "FRAME_GAP"}))
    answer.take(event({"kind": "REJECT", "code": "LOW_SCORE"}))
    answer.take(mqtt_rx.parse(f"sr/{DEVICE}/up/event", b"{"))
    assert answer.online is True
    assert [(r["code"], r["commandId"]) for r in answer.refusals] == [("COMMANDS_INVALID", "mo_den")]


def test_a_refusal_names_its_code_meaning_and_line() -> None:
    answer = commands.Answer(online=True, refusals=[{"code": "COMMANDS_INVALID", "commandId": "mo_den"}])
    [line] = commands.report(DEVICE, {"version": 9, "commands": []}, answer, 5.0)
    assert line == f"{DEVICE} refused v9: COMMANDS_INVALID, {commands.MEANINGS['COMMANDS_INVALID']}; line of mo_den"


@pytest.mark.parametrize(
    ("online", "expected"),
    [
        (None, f"no status from {DEVICE}"),
        (False, f"{DEVICE} is offline: it takes v9 when it connects again"),
        (True, f"sent v9, 1 commands, to {DEVICE}; no refusal within 5 s"),
    ],
)
def test_without_a_refusal_the_report_says_whether_the_board_is_there(online: bool | None, expected: str) -> None:
    command_set = {"version": 9, "commands": [{"id": "bat_den", "text": "bật đèn"}]}
    lines = commands.report(DEVICE, command_set, commands.Answer(online=online), 5.0)
    assert lines[0].startswith(expected)


@pytest.mark.parametrize(
    "argv",
    [["--device", DEVICE], ["--device", DEVICE, "--clear", "set.json"], ["--device", "+", "set.json"]],
)
def test_one_of_a_set_file_or_clear_and_a_real_device_are_required(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        commands.main(argv)
    assert stop.value.code == 2
