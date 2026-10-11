"""Manage the speakers a board checks a command's voice against (KEHOACH 3.17, 4.6): send a roster retained on
down/speakers, rename or add one person of a roster file, have the board drop one voiceprint, enrol with SPEAKER_ENROL
prompting the commands to say, print the roster on up/speakers. Each person of a file carries consent, their consent
form's code (KEHOACH 1.4), refused if missing and dropped before sending; version is the Unix time of the send.
Run: uv run python -m srhost.speakers --device <deviceId> (<roster.json> [--rename spk_NNN --name <name> [--consent
<code>] | --forget spk_NNN] | --enrol spk_NNN [--set <file>] | --list | --clear)"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import paho.mqtt.client as mqtt
import yaml

from srhost.config import REPO_ROOT, ConfigError, MqttConfig, mqtt_config
from srhost.generated import topics
from srhost.generated.payload import SCHEMAS
from srhost.mqtt_rx import KEEPALIVE_S, Message, parse, schema_error

ANSWER_WAIT_S = 5.0
ENROL_WAIT_S = 180.0
BROKER_WAIT_S = 5.0
POLL_S = 0.2
VERSION_MAX = SCHEMAS["speaker_set"]["properties"]["version"]["maximum"]
DEVICE_ID = re.compile(SCHEMAS["status"]["properties"]["deviceId"]["pattern"])
SPEAKER_ID = re.compile(SCHEMAS["speaker_set"]["properties"]["speakers"]["items"]["properties"]["id"]["pattern"])
CONSENT = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
ENROLLING = "SPEAKER_ENROLLING"
DEFAULT_SET = REPO_ROOT / "contracts" / "commands" / "default_vi.json"
ENROL_COMMANDS = yaml.safe_load((REPO_ROOT / "contracts" / "speaker.yaml").read_text(encoding="utf-8"))[
    "enrol_commands"
]
MEANINGS = {
    "SPEAKERS_INVALID": "not a roster the board can hold",
    "SPEAKERS_REFUSED": "this image has no speaker branch, or the board's queue was full",
    "SPEAKERS_NOT_SAVED": "in use until the board restarts: NVS was not written",
    "SPEAKER_UNKNOWN": "that id is not on the board's roster: send the roster first",
}


class RosterError(ValueError):
    """The file is not a roster this tool sends; the message says what to fix."""


def read_roster(path: Path) -> dict:
    """The roster file as written, consent codes and all."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
        raise RosterError(f"{path}: {err}") from err


def write_roster(path: Path, roster: dict) -> None:
    """The roster file as host/speakers keeps one: check, then one person a line, consent codes included."""
    people = ",\n".join(f"    {json.dumps(p, ensure_ascii=False)}" for p in roster["speakers"])
    check = json.dumps(roster.get("check", False))
    path.write_text(f'{{\n  "check": {check},\n  "speakers": [\n{people}\n  ]\n}}\n', encoding="utf-8")


def load_roster(path: Path) -> dict:
    """The roster in path as speaker_set lays it out: every person with a consent code and an id of their own, the
    consent codes dropped, version left for stamped."""
    return checked_roster(read_roster(path), path)


def checked_roster(roster: dict, path: Path) -> dict:
    """load_roster on a roster file's content, path naming it in the errors."""
    people = roster.get("speakers") if isinstance(roster, dict) else None
    if not isinstance(people, list):
        raise RosterError(f"{path}: no speakers list")
    missing = [str(p.get("id")) for p in people if not CONSENT.fullmatch(str(p.get("consent", "")))]
    if missing:
        raise RosterError(f"{path}: no consent code for {', '.join(missing)} (KEHOACH 1.4)")
    ids = [p.get("id") for p in people]
    repeated = sorted({i for i in ids if ids.count(i) > 1}, key=str)
    if repeated:
        raise RosterError(f"{path}: id {', '.join(map(str, repeated))} listed more than once")
    sent = {
        "version": 1,
        "check": roster.get("check", False),
        "speakers": [{k: v for k, v in p.items() if k != "consent"} for p in people],
    }
    error = schema_error("speaker_set", sent)
    if error is not None:
        raise RosterError(f"{path}: {error}")
    return sent


def renamed(roster: dict, person: str, name: str, consent: str | None) -> dict:
    """A roster file's content with person named name, and their consent code set when given; a person it lacks is
    added at the end, which takes a consent code (KEHOACH 1.4)."""
    people = [dict(p) for p in roster.get("speakers", [])]
    listed = [p for p in people if p.get("id") == person]
    if listed:
        listed[0]["name"] = name
        if consent is not None:
            listed[0]["consent"] = consent
    elif consent is None:
        raise RosterError(f"{person} is not on the roster: give a consent code to add them (KEHOACH 1.4)")
    else:
        people.append({"id": person, "name": name, "consent": consent})
    return {**roster, "speakers": people}


def without(roster: dict, person: str) -> dict:
    """A roster to send with person left out, so the board drops their voiceprint (KEHOACH 3.17)."""
    if person not in {p["id"] for p in roster["speakers"]}:
        raise RosterError(f"{person} is not on the roster")
    return {**roster, "speakers": [p for p in roster["speakers"] if p["id"] != person]}


def stamped(roster: dict, now_s: float) -> dict:
    """The roster with version set to the Unix time of the send: the board takes only a newer one."""
    return {**roster, "version": min(max(int(now_s), 1), VERSION_MAX)}


def body(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@dataclass
class Answer:
    """What the board said while the tool listened: its status, the roster it keeps, refusals, enrolment progress."""

    online: bool | None = None
    roster: dict | None = None
    refusals: list[dict] = field(default_factory=list)
    enrolled: list[dict] = field(default_factory=list)
    watch: Callable[[dict], None] | None = None

    def take(self, message: Message) -> None:
        if message.rejected:
            return
        payload = message.payload
        if message.kind == "status":
            self.online = payload["state"] == "ONLINE"
        elif message.kind == "roster":
            self.roster = payload
        elif message.kind == "event" and payload["kind"] == "ERROR" and payload["code"] in MEANINGS:
            self.refusals.append(payload)
        elif message.kind == "event" and payload["kind"] == "REJECT":
            if payload["code"] == ENROLLING:
                self.enrolled.append(payload)
            if self.watch is not None:
                self.watch(payload)


def exchange(
    cfg: MqttConfig,
    device: str,
    sends: list[tuple[str, bytes]],
    wait_s: float,
    done: Callable[[Answer], bool],
    watch: Callable[[dict], None] | None = None,
) -> Answer:
    """Subscribe to the board's status, roster and events, publish each (topic id, payload) of sends, then gather its
    answer until done says so or wait_s passes; watch sees each enrolment event as it arrives."""
    answer = Answer(watch=watch)
    subscribed = threading.Event()
    listened = [(topics.BY_ID[t].build(device), topics.BY_ID[t].qos) for t in ("status", "roster", "event")]
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
        for topic_id, payload in sends:
            spec = topics.BY_ID[topic_id]
            sent = client.publish(spec.build(device), payload, qos=spec.qos, retain=spec.retain)
            if sent.rc != mqtt.MQTT_ERR_SUCCESS:
                raise ConnectionError(f"publish to {cfg.host}:{cfg.port} failed: {mqtt.error_string(sent.rc)}")
            sent.wait_for_publish(BROKER_WAIT_S)
            if not sent.is_published():
                raise ConnectionError(f"broker {cfg.host}:{cfg.port} did not acknowledge {topic_id}")
        until = time.monotonic() + wait_s
        while time.monotonic() < until and not done(answer):
            time.sleep(POLL_S)
    finally:
        client.disconnect()
        client.loop_stop()
    return answer


def roster_lines(roster: dict) -> list[str]:
    """The roster a board keeps, one line a person, the enrolment under way last."""
    lines = [f"v{roster['version']}, check {'on' if roster['check'] else 'off'}, {len(roster['speakers'])} people"]
    lines += [
        f"  {p['id']} {p['name']}: {'enrolled' if p['enrolled'] else 'no voiceprint'}" for p in roster["speakers"]
    ]
    if "enrollingId" in roster:
        lines.append(f"  enrolling {roster['enrollingId']}: {roster.get('enrolCount', 0)} commands so far")
    return lines


def refusal_lines(device: str, refusals: list[dict]) -> list[str]:
    return [f"{device} refused: {r['code']}, {MEANINGS[r['code']]}" for r in refusals]


def sent_report(device: str, roster: dict, answer: Answer) -> list[str]:
    """Refusals, else the roster the board keeps once it shows the version sent, else why it does not."""
    if answer.refusals:
        return refusal_lines(device, answer.refusals)
    if answer.online is None:
        return [f"no status from {device}: is the deviceId right? The roster stays retained for it"]
    if not answer.online:
        return [f"{device} is offline: it takes v{roster['version']} when it connects again"]
    if answer.roster is None or answer.roster["version"] != roster["version"]:
        return [f"sent v{roster['version']} to {device}; its roster did not show it within {ANSWER_WAIT_S:.0f} s"]
    return roster_lines(answer.roster)


def enrol_report(device: str, answer: Answer) -> list[str]:
    """Refusals, else the roster once the enrolment ended, else what the board did not show."""
    if answer.refusals:
        return refusal_lines(device, answer.refusals)
    if not answer.enrolled:
        return [f"{device} enrolled no command within {ENROL_WAIT_S:.0f} s: is it online, and its check on the chip?"]
    if answer.roster is None:
        return [f"{device} enrolled {len(answer.enrolled)} commands and showed no roster"]
    return roster_lines(answer.roster)


def set_texts(path: Path) -> dict[str, str]:
    """The id and text of every command of a command set file, in its order."""
    try:
        listed = json.loads(path.read_text(encoding="utf-8"))["commands"]
        return {c["id"]: c["text"] for c in listed}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as err:
        raise RosterError(f"{path}: not a command set: {err}") from err


class Prompter:
    """Prompts the commands of a set in turn, one for each command an enrolment still needs; follows the board's
    REJECT events: one it keeps prints the count and the command it heard, then the next prompt, any other repeats the
    prompt."""

    def __init__(self, texts: dict[str, str], total: int = ENROL_COMMANDS, out: Callable[[str], None] = print) -> None:
        self.texts, self.total, self.out, self.kept = texts, total, out, 0

    def wanted(self) -> str:
        listed = list(self.texts.values())
        return listed[self.kept % len(listed)]

    def prompt(self) -> None:
        self.out(f"  {self.kept + 1}/{self.total} say: {self.wanted()}")

    def __call__(self, event: dict) -> None:
        if event["code"] != ENROLLING:
            self.out(f"  not taken ({event['code']}), say again: {self.wanted()}")
            return
        self.kept = event.get("enrolCount", self.kept + 1)
        heard = self.texts.get(event.get("commandId", ""), event.get("commandId", "?"))
        self.out(f"  kept {self.kept}/{self.total}: heard {heard}")
        if self.kept < self.total:
            self.prompt()


def enrolment_done(person: str) -> Callable[[Answer], bool]:
    """Whether the board refused the enrolment, or its roster shows person enrolled with no enrolment under way, after
    the first command of it."""

    def done(answer: Answer) -> bool:
        if answer.refusals:
            return True
        if not answer.enrolled or answer.roster is None or "enrollingId" in answer.roster:
            return False
        return any(p["id"] == person and p["enrolled"] for p in answer.roster["speakers"])

    return done


def send(cfg: MqttConfig, device: str, roster: dict) -> Answer:
    """Send roster retained and wait until the board's roster shows its version, or ANSWER_WAIT_S passes."""

    def shows_it(answer: Answer) -> bool:
        return answer.roster is not None and answer.roster["version"] == roster["version"]

    return exchange(cfg, device, [("speakers", body(roster))], ANSWER_WAIT_S, shows_it)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", required=True, help="deviceId of the board, as its boot log prints it")
    parser.add_argument("roster", nargs="?", default="", help="roster file, laid out as host/speakers/example_vi.json")
    parser.add_argument("--rename", help="the id of a person of the roster file to rename, or to add with --consent")
    parser.add_argument("--name", help="the name --rename gives")
    parser.add_argument("--consent", help="the consent code --rename sets, needed for a person the file lacks")
    parser.add_argument("--forget", help="the id of a person of the roster file whose voiceprint the board drops")
    parser.add_argument("--enrol", help="the id of a person on the roster: their next commands become their voiceprint")
    parser.add_argument("--set", type=Path, default=DEFAULT_SET, help="command set whose commands --enrol prompts")
    parser.add_argument("--list", action="store_true", help="print the roster the board keeps")
    parser.add_argument("--clear", action="store_true", help="drop the retained roster; the board keeps the one it has")
    args = parser.parse_args(argv)
    if not DEVICE_ID.fullmatch(args.device):
        parser.error(f"--device {args.device!r} is not a deviceId")
    edits = sum(x is not None for x in (args.rename, args.forget))
    if edits > 1 or (edits and not args.roster):
        parser.error("--rename and --forget each edit a roster file: give the file and one of them")
    if sum((bool(args.roster), args.enrol is not None, args.list, args.clear)) != 1:
        parser.error("give one of a roster file, --enrol, --list or --clear")
    if args.rename is not None and not args.name:
        parser.error("--rename needs --name")
    for flag, person in (("--enrol", args.enrol), ("--rename", args.rename), ("--forget", args.forget)):
        if person is not None and not SPEAKER_ID.fullmatch(person):
            parser.error(f"{flag} {person!r} is not a speaker id")
    try:
        cfg = mqtt_config()
        if args.clear:
            exchange(cfg, args.device, [("speakers", b"")], 0.0, lambda _: True)
            print(f"retained roster of {args.device} dropped; the board keeps the roster it has")
            return 0
        if args.list:
            answer = exchange(cfg, args.device, [], ANSWER_WAIT_S, lambda a: a.roster is not None)
            print("\n".join(roster_lines(answer.roster)) if answer.roster else f"{args.device} shows no roster")
            return 0
        if args.enrol is not None:
            prompter = Prompter(set_texts(args.set), out=lambda line: print(line, flush=True))
            cmd = body({"op": "SPEAKER_ENROL", "speakerId": args.enrol})
            print(f"{args.enrol}: about 1 m from {args.device}, say each command when it is shown")
            prompter.prompt()
            answer = exchange(cfg, args.device, [("cmd", cmd)], ENROL_WAIT_S, enrolment_done(args.enrol), prompter)
            print("\n".join(enrol_report(args.device, answer)))
            return 1 if answer.refusals else 0
        path = Path(args.roster)
        if args.rename is not None:
            edited = renamed(read_roster(path), args.rename, args.name, args.consent)
            roster = stamped(checked_roster(edited, path), time.time())
            write_roster(path, edited)
        else:
            roster = stamped(load_roster(path), time.time())
        if args.forget is not None:
            dropped = {**without(roster, args.forget), "version": roster["version"]}
            answer = send(cfg, args.device, dropped)
            if answer.refusals or answer.roster is None or answer.roster["version"] != dropped["version"]:
                print("\n".join(sent_report(args.device, dropped, answer)))
                return 1
            roster = {**roster, "version": min(roster["version"] + 1, VERSION_MAX)}
        answer = send(cfg, args.device, roster)
    except (ConfigError, RosterError, OSError) as err:
        print(f"speakers: {err}", file=sys.stderr)
        return 2
    print("\n".join(sent_report(args.device, roster, answer)))
    return 1 if answer.refusals else 0


if __name__ == "__main__":
    raise SystemExit(main())
