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


class ListenTests(unittest.TestCase):
    """listen.yaml reaches the C initializers and Python with the same numbers, the cut in hops as Python rounds it."""

    def test_c_and_python_carry_every_number_of_the_contract(self) -> None:
        values = gen_contracts.listen_values(gen_contracts.grid_values())
        namespace: dict = {}
        exec(gen_contracts.gen_listen_py(values), namespace)
        self.assertEqual(namespace["FEATURES"], values["features"])
        self.assertEqual(namespace["PITCH"], values["pitch"])
        sections = {"features": "GEN_LISTEN_MEL_CONFIG", "pitch": "GEN_LISTEN_PITCH_CONFIG"}
        fields = [f"struct {s} {{ " + " ".join(f"double {n};" for n in values[s]) + " };" for s in sections]
        lines = [f"    struct {s} {s} = {m};" for s, m in sections.items()]
        for section in sections:
            lines += [f'    printf("{section}.{n} %.9g\\n", {section}.{n});' for n in values[section]]
        for name in ("UTTERANCE_GAP_HOPS", "UTTERANCE_MIN_HOPS", "UTTERANCE_LEAD_HOPS", "WINDOW_HOPS"):
            lines.append(f'    printf("{name} %d\\n", GEN_LISTEN_{name});')
        probe = (
            '#include <stdio.h>\n#include "gen_listen.h"\n'
            + "\n".join(fields)
            + "\nint main(void)\n{\n"
            + "\n".join(lines)
            + "\n    return 0;\n}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            src, exe = Path(tmp) / "listen.c", Path(tmp) / "listen"
            src.write_text(probe)
            inc = REPO / gen_contracts.COMMON_INC
            subprocess.run(["gcc", "-std=c11", "-Wall", "-Werror", f"-I{inc}", str(src), "-o", str(exe)], check=True)
            printed = dict(
                line.split() for line in subprocess.run([str(exe)], capture_output=True, text=True).stdout.splitlines()
            )
        for section in sections:
            for name, number in values[section].items():
                self.assertAlmostEqual(float(printed[f"{section}.{name}"]), float(number), places=6)
        rate = gen_contracts.grid_values()["frames_per_s"]
        self.assertEqual(int(printed["UTTERANCE_GAP_HOPS"]), round(values["utterance"]["gap_s"] * rate))
        self.assertEqual(int(printed["UTTERANCE_MIN_HOPS"]), round(values["utterance"]["min_s"] * rate))
        self.assertEqual(int(printed["UTTERANCE_LEAD_HOPS"]), round(values["utterance"]["lead_s"] * rate))
        self.assertEqual(namespace["UTTERANCE_LEAD_HOPS"], int(printed["UTTERANCE_LEAD_HOPS"]))
        self.assertEqual(int(printed["WINDOW_HOPS"]), round(values["window_s"] * rate))
        self.assertEqual(namespace["WINDOW_HOPS"], int(printed["WINDOW_HOPS"]))

    def test_a_half_hop_rounds_to_even_as_python_does(self) -> None:
        doc, grid = gen_contracts.load_yaml("listen.yaml"), gen_contracts.grid_values()
        original = gen_contracts.load_yaml
        gen_contracts.load_yaml = lambda name: {**doc, "window_s": 1.0}
        try:
            values = gen_contracts.listen_values(grid)
        finally:
            gen_contracts.load_yaml = original
        self.assertEqual(grid["frames_per_s"], 62.5)
        self.assertEqual(values["window_hops"], 62)

    def test_a_value_that_is_not_a_number_is_refused(self) -> None:
        doc, grid = gen_contracts.load_yaml("listen.yaml"), gen_contracts.grid_values()
        original = gen_contracts.load_yaml
        for section, name in (("features", "n_bands"), ("pitch", "min_f0_hz")):
            bad = {**doc, section: {**doc[section], name: "40"}}
            gen_contracts.load_yaml = lambda _, bad=bad: bad
            try:
                with self.assertRaisesRegex(ValueError, f"{section}.{name}"):
                    gen_contracts.listen_values(grid)
            finally:
                gen_contracts.load_yaml = original


if __name__ == "__main__":
    unittest.main()


class AfeTests(unittest.TestCase):
    """afe.yaml reaches C and Python with the same numbers, and nothing else gets through."""

    def test_c_and_python_carry_every_number_of_the_contract(self) -> None:
        values = dict(gen_contracts.afe_values())
        namespace: dict = {}
        exec(gen_contracts.gen_afe_py(list(values.items()), gen_contracts.afe_modules()), namespace)
        lines = []
        for name, value in values.items():
            if isinstance(value, tuple):
                lines.append(f"    {{ static const double t[] = GEN_AFE_{name};")
                lines += [f'      printf("{name}[{i}] %.9g\\n", t[{i}]);' for i in range(len(value))]
                lines.append("    }")
            else:
                lines.append(f'    printf("{name} %.9g\\n", (double)GEN_AFE_{name});')
        probe = (
            '#include <stdio.h>\n#include "gen_afe.h"\nint main(void)\n{\n' + "\n".join(lines) + "\n    return 0;\n}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            src, exe = Path(tmp) / "afe.c", Path(tmp) / "afe"
            src.write_text(probe)
            inc = REPO / gen_contracts.AFE_INC
            subprocess.run(["gcc", "-std=c11", "-Wall", "-Werror", f"-I{inc}", str(src), "-o", str(exe)], check=True)
            printed = dict(
                line.split() for line in subprocess.run([str(exe)], capture_output=True, text=True).stdout.splitlines()
            )
        for name, value in values.items():
            self.assertEqual(namespace[name], value)
            for key, number in (
                [(f"{name}[{i}]", v) for i, v in enumerate(value)] if isinstance(value, tuple) else [(name, value)]
            ):
                self.assertAlmostEqual(float(printed[key]), float(number), places=6)

    def test_a_negative_number_is_bracketed_for_the_preprocessor(self) -> None:
        self.assertEqual(gen_contracts.c_number(-3.0), "(-3.0f)")
        self.assertEqual(gen_contracts.c_number(8), "8")

    def test_the_module_list_turns_its_switches_on_and_the_rest_off(self) -> None:
        text = gen_contracts.gen_sdkconfig_afe(["vad"])
        self.assertIn("CONFIG_DSP_AFE_VAD_ENABLE=y\n", text)
        self.assertIn("# CONFIG_DSP_AFE_HPF_ENABLE is not set\n", text)
        self.assertEqual(text.count("CONFIG_DSP_AFE_"), len(gen_contracts.afe_switches()))

    def test_a_module_without_a_switch_is_refused(self) -> None:
        original = gen_contracts.load_yaml
        for bad in (["hpf", "sparkle"], ["vad", "vad"], "hpf"):
            gen_contracts.load_yaml = lambda name, bad=bad: {"version": 1, "modules": bad}
            try:
                with self.assertRaisesRegex(ValueError, "modules"):
                    gen_contracts.afe_modules()
            finally:
                gen_contracts.load_yaml = original

    def test_a_list_becomes_an_initializer(self) -> None:
        self.assertEqual(gen_contracts.c_value((1.5, -2.0, 3)), "{1.5f, (-2.0f), 3}")

    def test_a_value_that_is_not_a_number_is_refused(self) -> None:
        original = gen_contracts.load_yaml
        for bad in ("80", [80.0, "x"], [], True):
            gen_contracts.load_yaml = lambda name, bad=bad: {"version": 1, "hpf": {"cutoff_hz": bad}}
            try:
                with self.assertRaisesRegex(ValueError, "hpf.cutoff_hz"):
                    gen_contracts.afe_values()
            finally:
                gen_contracts.load_yaml = original


class LangViTest(unittest.TestCase):
    def patched(self, edit) -> None:
        doc = gen_contracts.load_yaml("lang_vi.yaml")
        edit(doc)
        original = gen_contracts.load_yaml
        gen_contracts.load_yaml = lambda name: doc
        try:
            gen_contracts.lang_vi_values()
        finally:
            gen_contracts.load_yaml = original

    def test_c_strings_escape_utf8_without_swallowing_the_next_digit(self) -> None:
        self.assertEqual(gen_contracts.c_str('à1"\\'), '"\\303\\2401\\042\\134"')

    def test_a_tone_form_that_is_not_its_base_with_the_mark_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "ặ"):
            self.patched(lambda doc: doc["vowels"]["a"].__setitem__(5, "ặ"))

    def test_a_key_yaml_reads_as_a_boolean_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "quote it"):
            self.patched(lambda doc: doc["rhymes"].__setitem__(True, ["", "O", "n"]))

    def test_a_rule_naming_an_unknown_unit_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "south rules"):
            self.patched(lambda doc: doc["dialect_rules"]["south"]["tone"].__setitem__("T3", "T9"))

    def test_the_c_tables_hold_every_row_of_the_contract(self) -> None:
        values = gen_contracts.lang_vi_values()
        header = gen_contracts.gen_lang_vi_h(values)
        for table in ("rhymes", "q_rhymes", "onsets", "dictionary"):
            self.assertIn(f"#define GEN_LANG_VI_N_{table.upper()} {len(values[table])}\n", header)
        self.assertEqual(len(values["letters"]), 2 * len(values["vowels"]) * len(values["tones"]) + 2)

    def test_the_tone_units_ai_engine_reads_are_the_ids_of_the_contracts_tones(self) -> None:
        values = gen_contracts.lang_vi_values()
        probe = (
            '#include <stdio.h>\n#include "gen_units.h"\nint main(void)\n{\n'
            '    for (int k = 0; k < GEN_UNITS_N_TONES; k++) { printf("%d\\n", GEN_UNITS_TONES[k]); }\n'
            "    return 0;\n}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            src, exe = Path(tmp) / "units.c", Path(tmp) / "units"
            src.write_text(probe)
            inc = REPO / gen_contracts.COMMON_INC
            subprocess.run(["gcc", "-std=c11", "-Wall", "-Werror", f"-I{inc}", str(src), "-o", str(exe)], check=True)
            printed = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual([int(p) for p in printed], [values["units"].index(t) for t in values["tones"]])
