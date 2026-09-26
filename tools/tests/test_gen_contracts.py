"""Check gen_contracts.py is deterministic, committed output is fresh, and the C it emits works."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import gen_contracts

REPO = Path(__file__).resolve().parents[2]
CJSON_CANDIDATES = [
    os.environ.get("CJSON_DIR", ""),
    str(REPO / "firmware/managed_components/espressif__cjson/cJSON"),
]

C_PROBE = r"""
#include <stdio.h>
#include <stdlib.h>
#include "cJSON.h"
#include "gen_array.h"
#include "gen_grid.h"
#include "gen_payload.h"
#include "gen_stream.h"
#include "gen_topics.h"

static char *read_all(FILE *f)
{
    static char buf[65536];
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    buf[n] = '\0';
    return buf;
}

int main(int argc, char **argv)
{
    if (argc < 2) { return 2; }
    cJSON *in = cJSON_Parse(read_all(stdin));
    char *text = NULL;
    if (argv[1][0] == 'c') {
        static command_set_t set;
        if (!command_set_from_json(in, &set)) { return 3; }
        text = cJSON_PrintUnformatted(command_set_to_json(&set));
    } else if (argv[1][0] == 't') {
        static telemetry_t t;
        if (!telemetry_from_json(in, &t)) { return 3; }
        text = cJSON_PrintUnformatted(telemetry_to_json(&t));
    } else if (argv[1][0] == 'd') {
        static device_cmd_t d;
        if (!device_cmd_from_json(in, &d)) { return 3; }
        text = cJSON_PrintUnformatted(device_cmd_to_json(&d));
    } else if (argv[1][0] == 'p') {
        char topic[GEN_TOPIC_MAX_LEN + 1];
        if (!gen_topic_event("sr-3485188f7a70", topic, sizeof(topic))) { return 4; }
        if (gen_topic_match(topic, strlen(topic), "sr-3485188f7a70") != GEN_TOPIC_EVENT) { return 5; }
        if (gen_topic_match(topic, strlen(topic), "sr-000000000000") != GEN_TOPIC_NONE) { return 6; }
        printf("%s %u %d\n", topic, (unsigned) GEN_GRID_HASH, gen_stream_mode_channels(GEN_STREAM_MODE_RAW_REF));
        return 0;
    }
    puts(text);
    return 0;
}
"""


def find_cjson() -> Path | None:
    for candidate in CJSON_CANDIDATES:
        if candidate and (Path(candidate) / "cJSON.c").exists():
            return Path(candidate)
    return None


class DeterminismTests(unittest.TestCase):
    def test_two_runs_give_identical_bytes(self) -> None:
        self.assertEqual(gen_contracts.outputs(), gen_contracts.outputs())

    def test_committed_output_matches_the_generator(self) -> None:
        stale = [
            rel for rel, text in gen_contracts.outputs().items() if (REPO / rel).read_text(encoding="utf-8") != text
        ]
        self.assertEqual(stale, [], "run: python3 tools/gen_contracts.py")

    def test_every_output_opens_with_the_three_line_banner(self) -> None:
        for rel, text in gen_contracts.outputs().items():
            with self.subTest(file=rel):
                self.assertIn("GENERATED FILE - DO NOT EDIT.", text.splitlines()[0])
                self.assertIn("Regenerate: python3 tools/gen_contracts.py", text.splitlines()[2])

    def test_every_generated_c_function_has_a_doc_comment_with_ctx(self) -> None:
        for rel, text in gen_contracts.outputs().items():
            if not rel.endswith(".h"):
                continue
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if not line.startswith("static inline"):
                    continue
                start = next((k for k in range(i - 1, max(i - 8, -1), -1) if lines[k].startswith("/**")), None)
                with self.subTest(file=rel, function=line[:60]):
                    self.assertEqual(lines[i - 1].strip(), "*/")
                    self.assertIsNotNone(start)
                    self.assertLessEqual(i - start, 6)
                    self.assertTrue(any("@ctx" in b for b in lines[start:i]))

    def test_unicode_strings_get_four_bytes_per_character(self) -> None:
        self.assertEqual(gen_contracts.string_bytes({"maxLength": 64}), 256)
        self.assertEqual(gen_contracts.string_bytes({"maxLength": 32, "pattern": "^[a-z]+$"}), 32)


@unittest.skipIf(find_cjson() is None or shutil.which("gcc") is None, "needs gcc and cJSON sources (set CJSON_DIR)")
class GeneratedCTests(unittest.TestCase):
    exe: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        (tmp / "probe.c").write_text(C_PROBE)
        cjson = find_cjson()
        cls.exe = tmp / "probe"
        subprocess.run(
            [
                "gcc",
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-O1",
                f"-I{REPO / gen_contracts.COMMON_INC}",
                f"-I{REPO / gen_contracts.MQTT_INC}",
                f"-I{cjson}",
                str(tmp / "probe.c"),
                str(cjson / "cJSON.c"),
                "-lm",
                "-o",
                str(cls.exe),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def run_probe(self, mode: str, payload: dict | None = None) -> subprocess.CompletedProcess:
        data = json.dumps(payload, ensure_ascii=False) if payload is not None else ""
        return subprocess.run([str(self.exe), mode], input=data.encode(), capture_output=True)

    def test_default_commands_survive_a_round_trip(self) -> None:
        original = json.loads((REPO / "contracts/commands/default_vi.json").read_text(encoding="utf-8"))
        result = self.run_probe("c", original)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), original)

    def test_telemetry_survives_a_round_trip(self) -> None:
        sample = {"doaDeg": 90, "doaConf": 200, "vad": True, "levelDbfs": -30, "gainDb": 6}
        original = {"deviceId": "sr-3485188f7a70", "seq": 1234, "state": "LISTEN", "samples": [sample] * 10}
        result = self.run_probe("t", original)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), original)

    def test_optional_nested_object_survives_a_round_trip(self) -> None:
        original = {"op": "SET_STREAM", "stream": {"mode": 3, "host": "192.168.40.10", "port": 7000}}
        result = self.run_probe("d", original)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), original)

    def test_out_of_range_integer_is_rejected(self) -> None:
        sample = {"doaDeg": 200, "doaConf": 0, "vad": False, "levelDbfs": -30, "gainDb": 0}
        bad = {"deviceId": "sr-3485188f7a70", "seq": 1, "state": "LISTEN", "samples": [sample]}
        self.assertEqual(self.run_probe("t", bad).returncode, 3)

    def test_unknown_enum_is_rejected(self) -> None:
        self.assertEqual(self.run_probe("d", {"op": "SELF_DESTRUCT"}).returncode, 3)

    def test_too_long_string_is_rejected_not_truncated(self) -> None:
        bad = {"version": 1, "commands": [{"id": "x" * 40, "text": "bật đèn"}]}
        self.assertEqual(self.run_probe("c", bad).returncode, 3)

    def test_too_many_array_items_are_rejected(self) -> None:
        sample = {"doaDeg": 0, "doaConf": 0, "vad": False, "levelDbfs": 0, "gainDb": 0}
        bad = {"deviceId": "sr-3485188f7a70", "seq": 1, "state": "LISTEN", "samples": [sample] * 11}
        self.assertEqual(self.run_probe("t", bad).returncode, 3)

    def test_topics_build_and_match(self) -> None:
        result = self.run_probe("p")
        self.assertEqual(result.returncode, 0, result.stderr)
        topic, grid_hash, channels = result.stdout.decode().split()
        self.assertEqual(topic, "sr/sr-3485188f7a70/up/event")
        self.assertEqual(int(grid_hash), gen_contracts.grid_values()["grid_hash"])
        self.assertEqual(int(channels), 3)


if __name__ == "__main__":
    unittest.main()
