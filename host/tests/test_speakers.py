"""speakers sends only rosters with a consent code for everyone, and reads the roster, refusals and enrolment back."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from srhost import mqtt_rx, speakers
from srhost.config import REPO_ROOT
from test_mqtt_rx import DEVICE

EXAMPLE = REPO_ROOT / "host" / "speakers" / "example_vi.json"
NOW_S = 1_790_000_000.7


def written(tmp_path: Path, roster: dict) -> Path:
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(roster, ensure_ascii=False), encoding="utf-8")
    return path


def person(spk: str, name: str, consent: str | None = "C001") -> dict:
    return {"id": spk, "name": name} | ({"consent": consent} if consent is not None else {})


def message(kind: str, payload: dict) -> mqtt_rx.Message:
    path = {"roster": "speakers", "event": "event", "status": "status"}[kind]
    return mqtt_rx.parse(f"sr/{DEVICE}/up/{path}", json.dumps({"deviceId": DEVICE, **payload}).encode())


def board_roster(version: int, enrolled: bool, enrolling: dict | None = None) -> mqtt_rx.Message:
    people = [{"id": "spk_001", "name": "Chủ nhà", "enrolled": enrolled}]
    return message("roster", {"version": version, "check": True, "speakers": people, **(enrolling or {})})


def test_the_example_loads_without_its_consent_codes() -> None:
    roster = speakers.load_roster(EXAMPLE)
    assert all("consent" not in p for p in roster["speakers"]) and len(roster["speakers"]) == 2
    assert speakers.stamped(roster, NOW_S)["version"] == 1_790_000_000


def test_a_person_with_no_consent_code_is_refused_by_id(tmp_path: Path) -> None:
    path = written(tmp_path, {"check": True, "speakers": [person("spk_001", "A"), person("spk_002", "B", None)]})
    with pytest.raises(speakers.RosterError, match="no consent code for spk_002"):
        speakers.load_roster(path)


def test_one_id_listed_twice_is_refused(tmp_path: Path) -> None:
    path = written(tmp_path, {"check": True, "speakers": [person("spk_001", "A"), person("spk_001", "B")]})
    with pytest.raises(speakers.RosterError, match="spk_001 listed more than once"):
        speakers.load_roster(path)


def test_a_roster_past_the_schema_is_refused_with_where(tmp_path: Path) -> None:
    nine = [person(f"spk_{k:03d}", f"P{k}") for k in range(1, 10)]
    with pytest.raises(speakers.RosterError, match="speakers"):
        speakers.load_roster(written(tmp_path, {"check": True, "speakers": nine}))
    with pytest.raises(speakers.RosterError, match="speakers/0/id"):
        speakers.load_roster(written(tmp_path, {"check": True, "speakers": [person("owner", "A")]}))


def test_the_answer_keeps_the_roster_refusals_and_each_enrolled_command() -> None:
    seen = []
    answer = speakers.Answer(watch=seen.append)
    answer.take(message("status", {"state": "ONLINE"}))
    answer.take(board_roster(7, False, {"enrollingId": "spk_001", "enrolCount": 1}))
    answer.take(
        message(
            "event",
            {
                "seq": 9,
                "kind": "REJECT",
                "code": "SPEAKER_ENROLLING",
                "speakerId": "spk_001",
                "enrolCount": 1,
                "commandId": "bat_den",
            },
        )
    )
    answer.take(message("event", {"seq": 10, "kind": "ERROR", "code": "SPEAKER_UNKNOWN"}))
    answer.take(message("event", {"seq": 11, "kind": "ERROR", "code": "COMMANDS_INVALID"}))
    assert answer.online and answer.roster["version"] == 7
    assert [e["enrolCount"] for e in answer.enrolled] == [1] and seen == answer.enrolled
    assert [r["code"] for r in answer.refusals] == ["SPEAKER_UNKNOWN"]


def test_an_enrolment_ends_once_the_roster_shows_the_person_enrolled_and_none_under_way() -> None:
    done = speakers.enrolment_done("spk_001")
    answer = speakers.Answer()
    answer.take(board_roster(7, True))
    assert not done(answer)
    answer.take(
        message(
            "event",
            {
                "seq": 9,
                "kind": "REJECT",
                "code": "SPEAKER_ENROLLING",
                "speakerId": "spk_001",
                "enrolCount": 10,
                "commandId": "bat_den",
            },
        )
    )
    answer.take(board_roster(7, True, {"enrollingId": "spk_001", "enrolCount": 9}))
    assert not done(answer)
    answer.take(board_roster(7, True))
    assert done(answer)


def test_the_report_waits_for_the_version_sent_and_lists_each_person() -> None:
    roster = speakers.stamped(speakers.load_roster(EXAMPLE), NOW_S)
    answer = speakers.Answer(online=True)
    answer.take(board_roster(5, True))
    assert "did not show it" in speakers.sent_report(DEVICE, roster, answer)[0]
    answer.take(board_roster(roster["version"], True))
    lines = speakers.sent_report(DEVICE, roster, answer)
    assert lines[0].startswith(f"v{roster['version']}, check on, 1 people") and "spk_001 Chủ nhà: enrolled" in lines[1]


def test_the_prompter_asks_each_command_in_turn_and_again_after_a_refusal() -> None:
    lines: list[str] = []
    prompter = speakers.Prompter({"bat_den": "bật đèn", "tat_den": "tắt đèn"}, total=3, out=lines.append)
    prompter.prompt()
    prompter({"kind": "REJECT", "code": "LOW_SCORE"})
    prompter({"kind": "REJECT", "code": speakers.ENROLLING, "enrolCount": 1, "commandId": "bat_den"})
    prompter({"kind": "REJECT", "code": speakers.ENROLLING, "enrolCount": 2, "commandId": "bat_den"})
    prompter({"kind": "REJECT", "code": speakers.ENROLLING, "enrolCount": 3, "commandId": "tat_den"})
    assert lines == [
        "  1/3 say: bật đèn",
        "  not taken (LOW_SCORE), say again: bật đèn",
        "  kept 1/3: heard bật đèn",
        "  2/3 say: tắt đèn",
        "  kept 2/3: heard bật đèn",
        "  3/3 say: bật đèn",
        "  kept 3/3: heard tắt đèn",
    ]


def test_the_default_prompts_are_the_baked_set_and_the_count_is_speaker_yamls() -> None:
    texts = speakers.set_texts(speakers.DEFAULT_SET)
    assert next(iter(texts.values())) == "bật đèn" and len(texts) == 10
    assert speakers.ENROL_COMMANDS == 10


def test_a_file_that_is_no_command_set_is_refused(tmp_path: Path) -> None:
    with pytest.raises(speakers.RosterError, match="not a command set"):
        speakers.set_texts(written(tmp_path, {"speakers": []}))


def test_renaming_keeps_a_listed_persons_consent_and_adding_one_takes_a_code() -> None:
    roster = {"check": True, "speakers": [person("spk_001", "Chủ nhà"), person("spk_002", "Người 2", "C002")]}
    named = speakers.renamed(roster, "spk_002", "Vợ", None)
    assert named["speakers"][1] == {"id": "spk_002", "name": "Vợ", "consent": "C002"}
    assert roster["speakers"][1]["name"] == "Người 2"
    added = speakers.renamed(roster, "spk_003", "Con", "C103")
    assert added["speakers"][-1] == {"id": "spk_003", "name": "Con", "consent": "C103"}
    with pytest.raises(speakers.RosterError, match="give a consent code"):
        speakers.renamed(roster, "spk_003", "Con", None)


def test_forgetting_sends_the_roster_without_the_person_and_refuses_one_not_listed() -> None:
    roster = speakers.load_roster(EXAMPLE)
    assert [p["id"] for p in speakers.without(roster, "spk_002")["speakers"]] == ["spk_001"]
    with pytest.raises(speakers.RosterError, match="spk_009 is not on the roster"):
        speakers.without(roster, "spk_009")


def test_a_written_roster_file_reads_back_whole_one_person_a_line(tmp_path: Path) -> None:
    roster = {"check": True, "speakers": [person("spk_001", "Chủ nhà"), person("spk_002", "Người 2", "C002")]}
    path = tmp_path / "home_vi.json"
    speakers.write_roster(path, roster)
    assert speakers.read_roster(path) == roster
    assert (
        '    {"id": "spk_002", "name": "Người 2", "consent": "C002"}' in path.read_text(encoding="utf-8").splitlines()
    )
