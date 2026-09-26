"""Check contracts/ is valid, internally consistent and inside the generator's schema subset."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
FIELD_BYTES = {"u8": 1, "u16": 2, "u32": 4, "u64": 8}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def schemas() -> dict[str, dict]:
    return {p.name.removesuffix(".schema.json"): load_json(p) for p in (CONTRACTS / "schema").glob("*.schema.json")}


def subset_violations(node: dict, where: str) -> list[str]:
    """List every place a schema node falls outside what gen_contracts.py can turn into C."""
    found = []
    kind = node.get("type")
    if kind == "string" and "maxLength" not in node:
        found.append(f"{where}: string without maxLength")
    if kind == "array":
        if "maxItems" not in node:
            found.append(f"{where}: array without maxItems")
        found += subset_violations(node.get("items", {}), f"{where}[]")
    if kind == "integer" and not {"minimum", "maximum"} <= node.keys():
        found.append(f"{where}: integer without minimum and maximum")
    if kind == "object":
        if node.get("additionalProperties") is not False:
            found.append(f"{where}: object must set additionalProperties false")
        for name, child in node.get("properties", {}).items():
            found += subset_violations(child, f"{where}.{name}")
    if "enum" in node and not all(isinstance(v, str) and v.isupper() for v in node["enum"]):
        found.append(f"{where}: enum values must be upper-case strings")
    return found


class SchemaTests(unittest.TestCase):
    def test_every_schema_is_valid_draft_2020_12(self) -> None:
        for name, schema in schemas().items():
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(schema)

    def test_every_schema_stays_inside_the_generator_subset(self) -> None:
        for name, schema in schemas().items():
            with self.subTest(schema=name):
                self.assertEqual(subset_violations(schema, name), [])

    def test_subset_check_catches_an_unbounded_string(self) -> None:
        bad = {"type": "object", "additionalProperties": False, "properties": {"x": {"type": "string"}}}
        self.assertEqual(subset_violations(bad, "bad"), ["bad.x: string without maxLength"])


class DataFileTests(unittest.TestCase):
    def test_default_commands_match_their_schema(self) -> None:
        validator = Draft202012Validator(schemas()["command_set"])
        validator.validate(load_json(CONTRACTS / "commands" / "default_vi.json"))

    def test_responses_match_their_schema(self) -> None:
        validator = Draft202012Validator(schemas()["responses"])
        validator.validate(load_json(CONTRACTS / "responses" / "vi.json"))

    def test_every_command_response_exists(self) -> None:
        commands = load_json(CONTRACTS / "commands" / "default_vi.json")["commands"]
        replies = {r["id"] for r in load_json(CONTRACTS / "responses" / "vi.json")["responses"]}
        missing = [c["id"] for c in commands if c.get("response") and c["response"] not in replies]
        self.assertEqual(missing, [])

    def test_ids_are_unique(self) -> None:
        for path, key in [("commands/default_vi.json", "commands"), ("responses/vi.json", "responses")]:
            ids = [item["id"] for item in load_json(CONTRACTS / path)[key]]
            with self.subTest(file=path):
                self.assertEqual(len(ids), len(set(ids)))

    def test_a_command_without_text_is_rejected(self) -> None:
        validator = Draft202012Validator(schemas()["command_set"])
        self.assertFalse(validator.is_valid({"version": 1, "commands": [{"id": "x"}]}))


class TopicTests(unittest.TestCase):
    def test_every_topic_names_an_existing_schema(self) -> None:
        known = schemas().keys()
        for topic in load_yaml(CONTRACTS / "mqtt_topics.yaml")["topics"]:
            with self.subTest(topic=topic["id"]):
                self.assertIn(topic["schema"], known)
                self.assertIn("{deviceId}", topic["path"])
                self.assertIn(topic["direction"], {"up", "down"})
                self.assertIn(topic["qos"], {0, 1})

    def test_topic_ids_are_unique(self) -> None:
        ids = [t["id"] for t in load_yaml(CONTRACTS / "mqtt_topics.yaml")["topics"]]
        self.assertEqual(len(ids), len(set(ids)))


class LayoutTests(unittest.TestCase):
    def test_stream_header_fields_are_naturally_aligned(self) -> None:
        frame = load_yaml(CONTRACTS / "stream" / "frame.yaml")
        offset = 0
        for field in frame["header"]:
            size = FIELD_BYTES[field["type"]]
            with self.subTest(field=field["name"]):
                self.assertEqual(offset % size, 0)
            offset += size
        self.assertEqual(offset, frame["header_bytes"])

    def test_stream_magic_is_four_ascii_bytes(self) -> None:
        magic = load_yaml(CONTRACTS / "stream" / "frame.yaml")["magic"]
        self.assertEqual(len(magic.encode("ascii")), 4)

    def test_grid_is_a_half_overlap_power_of_two(self) -> None:
        grid = load_yaml(CONTRACTS / "grid.yaml")
        fft = grid["fft_size"]
        self.assertEqual(fft & (fft - 1), 0)
        self.assertEqual(fft, 2 * grid["hop_samples"])

    def test_array_names_two_channels_and_a_phase_reference(self) -> None:
        array = load_yaml(CONTRACTS / "array.yaml")
        names = [c["name"] for c in array["channels"]]
        self.assertEqual(len(names), array["n_mics"])
        self.assertIn(array["phase_reference"], names)
        self.assertIn(array["zero_degree_side"], names)


if __name__ == "__main__":
    unittest.main()
