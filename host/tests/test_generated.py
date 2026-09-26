"""srhost.generated agrees with contracts/ and with what a real board sends (KEHOACH 4.2, 7.7)."""

from __future__ import annotations

import copy
import typing

import jsonschema
import pytest

from srhost.generated import payload, stream, topics

# Sent by board B on 26/09 over sr-emqx; only the device id identifies it.
BOARD_HEARTBEAT = {
    "deviceId": "sr-3485188f7a70",
    "fw": "0.1.0",
    "uptimeS": 38,
    "heapInternalFree": 93799,
    "heapInternalMin": 88075,
    "heapPsramFree": 8317652,
    "heapPsramMin": 8317232,
    "dmaOverflows": 0,
    "framesDropped": 0,
    "cleanDropped": 0,
    "eventsDropped": 0,
    "jsonArenaPeak": 1800,
    "rssiDbm": -27,
}


def validator(name: str) -> jsonschema.Draft202012Validator:
    schema = payload.SCHEMAS[name]
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def test_every_topic_has_its_schema() -> None:
    assert {t.schema for t in topics.TOPICS} <= set(payload.SCHEMAS)


@pytest.mark.parametrize("name", sorted(payload.SCHEMAS))
def test_every_schema_is_valid_draft_2020_12(name: str) -> None:
    validator(name)


@pytest.mark.parametrize("name", sorted(payload.SCHEMAS))
def test_typed_dicts_carry_exactly_the_schema_fields(name: str) -> None:
    cls = getattr(payload, "".join(part.capitalize() for part in name.split("_")))
    assert set(typing.get_type_hints(cls)) == set(payload.SCHEMAS[name]["properties"])


def test_a_heartbeat_from_board_b_validates() -> None:
    validator("heartbeat").validate(BOARD_HEARTBEAT)


@pytest.mark.parametrize(
    ("field", "value"),
    [("framesDropped", -1), ("deviceId", "no spaces allowed"), ("smuggled", 1)],
)
def test_a_tampered_heartbeat_is_refused(field: str, value: object) -> None:
    tampered = copy.deepcopy(BOARD_HEARTBEAT)
    tampered[field] = value
    with pytest.raises(jsonschema.ValidationError):
        validator("heartbeat").validate(tampered)


def test_telemetry_accepts_the_level_of_digital_silence() -> None:
    sample = {"doaDeg": -1, "doaConf": 0, "vad": False, "levelDbfs": -128, "gainDb": 0}
    validator("telemetry").validate({"deviceId": "sr-3485188f7a70", "seq": 0, "state": "LISTEN", "samples": [sample]})


def test_topics_build_and_match_back() -> None:
    for t in topics.TOPICS:
        path = t.build("sr-3485188f7a70")
        assert topics.match(path) == (t, "sr-3485188f7a70")
    assert topics.match("sr//up/status") is None


def test_the_stream_header_packs_to_its_declared_size() -> None:
    assert stream.HEADER.size == stream.HEADER_BYTES
    assert int.from_bytes(stream.MAGIC, "little") == stream.MAGIC_U32
    assert len(stream.HEADER_FIELDS) == len(stream.HEADER.unpack(bytes(stream.HEADER_BYTES)))
