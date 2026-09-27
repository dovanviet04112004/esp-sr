#!/usr/bin/env python3
"""Generate C headers and Python modules from contracts/ (KEHOACH 4.2).

One entry point for every target so CI can regenerate and diff in one step. The output is a
pure function of contracts/: running it twice must write identical bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pprint
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = REPO_ROOT / "contracts"

COMMON_INC = "firmware/components/common/include"
MQTT_INC = "firmware/components/net_mqtt/include"
AFE_INC = "firmware/components/dsp_afe/include"
ML_GEN = "ml/src/srpipe/generated"
HOST_GEN = "host/src/srhost/generated"

UTF8_MAX_BYTES_PER_CHAR = 4
C_INT_TYPES = [
    ("uint8_t", 0, 255),
    ("int8_t", -128, 127),
    ("uint16_t", 0, 65535),
    ("int16_t", -32768, 32767),
    ("uint32_t", 0, 4294967295),
    ("int32_t", -2147483648, 2147483647),
    ("int64_t", -(2**63), 2**63 - 1),
]
STRUCT_FORMAT = {"u8": "B", "u16": "H", "u32": "I", "u64": "Q"}
C_FIELD_TYPE = {"u8": "uint8_t", "u16": "uint16_t", "u32": "uint32_t", "u64": "uint64_t"}


def banner(source: str, mark: str) -> str:
    return (
        f"{mark} GENERATED FILE - DO NOT EDIT.\n"
        f"{mark} Source: {source}\n"
        f"{mark} Regenerate: python3 tools/gen_contracts.py\n"
    )


def load_yaml(name: str) -> dict:
    return yaml.safe_load((CONTRACTS / name).read_text(encoding="utf-8"))


def snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()


def pascal(name: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in re.split(r"_", snake(name)))


def c_int_type(node: dict) -> str:
    lo, hi = node["minimum"], node["maximum"]
    return next(t for t, tlo, thi in C_INT_TYPES if tlo <= lo and hi <= thi)


def string_bytes(node: dict) -> int:
    """Buffer size in bytes, NUL excluded; ASCII-patterned strings are one byte per character."""
    ascii_only = "pattern" in node and node["pattern"].isascii()
    return node["maxLength"] * (1 if ascii_only else UTF8_MAX_BYTES_PER_CHAR)


def grid_values() -> dict:
    grid = load_yaml("grid.yaml")
    canonical = "fs={sample_rate_hz};hop={hop_samples};fft={fft_size};window={window};v={version}".format(**grid)
    return {
        **grid,
        "n_bins": grid["fft_size"] // 2 + 1,
        "frames_per_s": grid["sample_rate_hz"] / grid["hop_samples"],
        "hop_us": grid["hop_samples"] * 1_000_000 // grid["sample_rate_hz"],
        "grid_hash": int.from_bytes(hashlib.sha256(canonical.encode()).digest()[:4], "big"),
        "canonical": canonical,
    }


def gen_grid_h(g: dict) -> str:
    return banner("contracts/grid.yaml", "//") + (
        "\n#pragma once\n\n"
        f"#define GEN_GRID_VERSION {g['version']}\n"
        f"#define GEN_GRID_SAMPLE_RATE_HZ {g['sample_rate_hz']}\n"
        f"#define GEN_GRID_HOP_SAMPLES {g['hop_samples']}\n"
        f"#define GEN_GRID_FFT_SIZE {g['fft_size']}\n"
        f"#define GEN_GRID_N_BINS {g['n_bins']}\n"
        f"#define GEN_GRID_HOP_US {g['hop_us']}\n"
        f"#define GEN_GRID_FRAMES_PER_S {g['frames_per_s']!r}f\n"
        f"#define GEN_GRID_WINDOW_{g['window'].upper()} 1\n"
        f'#define GEN_GRID_CANONICAL "{g["canonical"]}"\n'
        f"#define GEN_GRID_HASH 0x{g['grid_hash']:08x}u     // first 4 bytes of sha256(GEN_GRID_CANONICAL)\n"
    )


def gen_grid_py(g: dict) -> str:
    return banner("contracts/grid.yaml", "#") + (
        "\n"
        f"VERSION = {g['version']}\n"
        f"SAMPLE_RATE_HZ = {g['sample_rate_hz']}\n"
        f"HOP_SAMPLES = {g['hop_samples']}\n"
        f"FFT_SIZE = {g['fft_size']}\n"
        f"N_BINS = {g['n_bins']}\n"
        f"HOP_US = {g['hop_us']}\n"
        f"FRAMES_PER_S = {g['frames_per_s']!r}\n"
        f'WINDOW = "{g["window"]}"\n'
        f'GRID_CANONICAL = "{g["canonical"]}"\n'
        f"GRID_HASH = 0x{g['grid_hash']:08x}\n"
    )


def array_values(g: dict) -> dict:
    a = load_yaml("array.yaml")
    names = [c["name"] for c in a["channels"]]
    return {
        **a,
        "names": names,
        "phase_index": names.index(a["phase_reference"]),
        "zero_index": names.index(a["zero_degree_side"]),
        "max_delay_samples": a["spacing_m"] / a["speed_of_sound_m_s"] * g["sample_rate_hz"],
        "alias_hz": a["speed_of_sound_m_s"] / (2 * a["spacing_m"]),
    }


def gen_array_h(a: dict) -> str:
    lines = [
        banner("contracts/array.yaml", "//"),
        "#pragma once\n",
        f"#define GEN_ARRAY_VERSION {a['version']}",
        f"#define GEN_ARRAY_N_MICS {a['n_mics']}",
        f"#define GEN_ARRAY_SPACING_M {a['spacing_m']!r}f",
        f"#define GEN_ARRAY_SPEED_OF_SOUND_M_S {a['speed_of_sound_m_s']!r}f",
        f"#define GEN_ARRAY_PHASE_REFERENCE_INDEX {a['phase_index']}",
        f"#define GEN_ARRAY_ZERO_DEGREE_INDEX {a['zero_index']}",
        f"#define GEN_ARRAY_DOA_MIN_DEG {a['doa_range_deg'][0]}",
        f"#define GEN_ARRAY_DOA_MAX_DEG {a['doa_range_deg'][1]}",
        f"#define GEN_ARRAY_MAX_DELAY_SAMPLES {a['max_delay_samples']:.6f}f",
        f"#define GEN_ARRAY_ALIAS_HZ {a['alias_hz']:.3f}f",
    ]
    return "\n".join(lines) + "\n"


def gen_array_py(a: dict) -> str:
    return banner("contracts/array.yaml", "#") + (
        "\n"
        f"VERSION = {a['version']}\n"
        f"N_MICS = {a['n_mics']}\n"
        f"SPACING_M = {a['spacing_m']!r}\n"
        f"SPEED_OF_SOUND_M_S = {a['speed_of_sound_m_s']!r}\n"
        f"CHANNELS = {tuple(a['names'])!r}\n"
        f"PHASE_REFERENCE_INDEX = {a['phase_index']}\n"
        f"ZERO_DEGREE_INDEX = {a['zero_index']}\n"
        f"DOA_RANGE_DEG = {tuple(a['doa_range_deg'])!r}\n"
        f"MAX_DELAY_SAMPLES = {a['max_delay_samples']:.6f}\n"
        f"ALIAS_HZ = {a['alias_hz']:.3f}\n"
    )


AfeValue = int | float | tuple[int | float, ...]


def is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


AFE_KCONFIG = "firmware/components/dsp_afe/Kconfig"
SDKCONFIG_AFE = "firmware/sdkconfig.afe"


def afe_switches() -> list[str]:
    """Module names in the order dsp_afe's Kconfig declares their DSP_AFE_<NAME>_ENABLE switches."""
    text = (REPO_ROOT / AFE_KCONFIG).read_text(encoding="utf-8")
    return [m.lower() for m in re.findall(r"^\s*config DSP_AFE_(\w+)_ENABLE\s*$", text, flags=re.MULTILINE)]


def afe_modules() -> list[str]:
    """modules: of afe.yaml, each a switch dsp_afe's Kconfig has."""
    modules = load_yaml("afe.yaml").get("modules", [])
    known = afe_switches()
    if not isinstance(modules, list) or any(m not in known for m in modules) or len(set(modules)) != len(modules):
        raise ValueError(f"afe.yaml modules must be distinct names out of {known}, got {modules!r}")
    return modules


def gen_sdkconfig_afe(modules: list[str]) -> str:
    """Every dsp_afe switch, on for the product's modules and explicitly off for the rest."""
    lines = [
        f"CONFIG_DSP_AFE_{m.upper()}_ENABLE=y" if m in modules else f"# CONFIG_DSP_AFE_{m.upper()}_ENABLE is not set"
        for m in afe_switches()
    ]
    return banner("contracts/afe.yaml", "#") + "\n".join(lines) + "\n"


def afe_values() -> list[tuple[str, AfeValue]]:
    """(MODULE_PARAM, value) for every number or list of numbers of afe.yaml; a list becomes a tuple."""
    doc = load_yaml("afe.yaml")
    doc.pop("modules", None)
    values: list[tuple[str, AfeValue]] = [("VERSION", doc.pop("version"))]
    for module, params in doc.items():
        for name, value in params.items():
            if isinstance(value, list) and value and all(is_number(v) for v in value):
                value = tuple(value)
            elif not is_number(value):
                raise ValueError(f"afe.yaml {module}.{name} must be a number or a list of numbers, got {value!r}")
            values.append((f"{module}_{name}".upper(), value))
    return values


def c_number(value: int | float) -> str:
    text = str(value) if isinstance(value, int) else f"{float(value)!r}f"
    return f"({text})" if value < 0 else text


def c_value(value: AfeValue) -> str:
    """A number, or a list as an initializer the caller gives a type: static const float t[] = GEN_AFE_X;"""
    return "{" + ", ".join(c_number(v) for v in value) + "}" if isinstance(value, tuple) else c_number(value)


def gen_afe_h(values: list[tuple[str, AfeValue]]) -> str:
    lines = [banner("contracts/afe.yaml", "//"), "#pragma once\n"]
    lines += [f"#define GEN_AFE_{name} {c_value(value)}" for name, value in values]
    return "\n".join(lines) + "\n"


def gen_afe_py(values: list[tuple[str, AfeValue]], modules: list[str]) -> str:
    lines = "".join(f"{name} = {value!r}\n" for name, value in values)
    return banner("contracts/afe.yaml", "#") + "\n" + lines + f"MODULES = {tuple(modules)!r}\n"


def stream_values() -> dict:
    f = load_yaml("stream/frame.yaml")
    offset, fields = 0, []
    for field in f["header"]:
        size = int(field["type"][1:]) // 8
        if offset % size:
            sys.exit(f"stream/frame.yaml: {field['name']} is not aligned")
        fields.append({**field, "offset": offset})
        offset += size
    if offset != f["header_bytes"]:
        sys.exit(f"stream/frame.yaml: header is {offset} bytes, header_bytes says {f['header_bytes']}")
    return {**f, "fields": fields, "magic_u32": int.from_bytes(f["magic"].encode("ascii"), "little")}


def gen_stream_h(s: dict) -> str:
    out = [
        banner("contracts/stream/frame.yaml", "//"),
        "#pragma once\n",
        "#include <stddef.h>",
        "#include <stdint.h>\n",
    ]
    out.append(f'#define GEN_STREAM_MAGIC 0x{s["magic_u32"]:08x}u     // "{s["magic"]}" little-endian')
    out.append(f"#define GEN_STREAM_VERSION {s['version']}")
    out.append(f"#define GEN_STREAM_HEADER_BYTES {s['header_bytes']}\n")
    out.append("typedef enum {")
    out += [f"    GEN_STREAM_MODE_{m['name'].upper()} = {m['id']}," for m in s["modes"]]
    out.append("} gen_stream_mode_t;\n")
    out.append("typedef enum {")
    out += [f"    GEN_STREAM_FORMAT_{fm['name'].upper()} = {fm['id']}," for fm in s["formats"]]
    out.append("} gen_stream_format_t;\n")
    out.append("typedef struct {")
    out += [f"    {C_FIELD_TYPE[fl['type']]} {fl['name']};" for fl in s["fields"]]
    out.append("} gen_stream_header_t;\n")
    out.append('_Static_assert(sizeof(gen_stream_header_t) == GEN_STREAM_HEADER_BYTES, "stream header size");')
    out += [
        f'_Static_assert(offsetof(gen_stream_header_t, {fl["name"]}) == {fl["offset"]}, "{fl["name"]} offset");'
        for fl in s["fields"]
    ]
    out.append("\n/** Channels a frame carries in this mode; 0 for an unknown mode.\n *  @ctx any | non-blocking\n */")
    out.append("static inline uint8_t gen_stream_mode_channels(gen_stream_mode_t mode)\n{\n    switch (mode) {")
    out += [f"    case GEN_STREAM_MODE_{m['name'].upper()}: return {len(m['channels'])};" for m in s["modes"]]
    out.append("    default: return 0;\n    }\n}")
    return "\n".join(out) + "\n"


def gen_stream_py(s: dict) -> str:
    fmt = "<" + "".join(STRUCT_FORMAT[fl["type"]] for fl in s["fields"])
    modes = {m["id"]: (m["name"], len(m["channels"]), tuple(m["channels"])) for m in s["modes"]}
    formats = {fm["id"]: (fm["name"], fm["bytes"]) for fm in s["formats"]}
    return banner("contracts/stream/frame.yaml", "#") + (
        "\nimport struct\n\n"
        f"MAGIC = {s['magic'].encode('ascii')!r}\n"
        f"MAGIC_U32 = 0x{s['magic_u32']:08x}\n"
        f"VERSION = {s['version']}\n"
        f"HEADER_BYTES = {s['header_bytes']}\n"
        f'HEADER = struct.Struct("{fmt}")\n'
        f"HEADER_FIELDS = {tuple(fl['name'] for fl in s['fields'])!r}\n"
        f"MODES = {modes!r}\n"
        f"FORMATS = {formats!r}\n"
    )


def topic_values() -> dict:
    t = load_yaml("mqtt_topics.yaml")
    topics = []
    for topic in t["topics"]:
        prefix, suffix = topic["path"].split("{deviceId}")
        topics.append({**topic, "prefix": prefix, "suffix": suffix, "will": topic.get("will", False)})
    max_len = max(len(x["prefix"]) + t["device_id_max"] + len(x["suffix"]) for x in topics)
    return {**t, "topics": topics, "max_len": max_len}


def gen_topics_h(t: dict) -> str:
    out = [banner("contracts/mqtt_topics.yaml", "//"), "#pragma once\n"]
    out += ["#include <stdbool.h>", "#include <stddef.h>", "#include <stdint.h>", "#include <string.h>\n"]
    out += ["#ifdef __cplusplus", 'extern "C" {', "#endif\n"]
    out.append(f"#define GEN_TOPIC_DEVICE_ID_MAX {t['device_id_max']}")
    out.append(f"#define GEN_TOPIC_MAX_LEN {t['max_len']}")
    out.append(f"#define GEN_TOPIC_HEARTBEAT_INTERVAL_S {t['heartbeat_interval_s']}")
    out.append(f"#define GEN_TOPIC_TELEMETRY_PERIOD_MS {t['telemetry_period_ms']}")
    out.append(f"#define GEN_TOPIC_TELEMETRY_SAMPLES {t['telemetry_samples']}\n")
    out.append("typedef enum {")
    out += [f"    GEN_TOPIC_{x['id'].upper()} = {i}," for i, x in enumerate(t["topics"])]
    out.append(f"    GEN_TOPIC_COUNT = {len(t['topics'])},")
    out.append("    GEN_TOPIC_NONE = GEN_TOPIC_COUNT,")
    out.append("} gen_topic_id_t;\n")
    out.append("typedef struct {")
    out += ["    const char *prefix;", "    const char *suffix;", "    uint8_t qos;", "    bool retain;"]
    out += ["    bool up;", "    bool will;", "} gen_topic_info_t;\n"]
    out.append("static const gen_topic_info_t GEN_TOPIC_INFO[GEN_TOPIC_COUNT] = {")
    for x in t["topics"]:
        flags = (
            f"{x['qos']}, {str(x['retain']).lower()}, {str(x['direction'] == 'up').lower()}, {str(x['will']).lower()}"
        )
        out.append(f'    [GEN_TOPIC_{x["id"].upper()}] = {{"{x["prefix"]}", "{x["suffix"]}", {flags}}},')
    out.append("};\n")
    out.append(
        "/** Write the NUL-terminated topic path of id for device_id into out.\n"
        " *  @ctx any | non-blocking | caller owns out, GEN_TOPIC_MAX_LEN + 1 bytes is always enough\n"
        " *  @ret false for an unknown id, an empty or over-long device_id, or a short buffer\n"
        " */\n"
        "static inline bool gen_topic_build(gen_topic_id_t id, const char *device_id, char *out, size_t cap)\n"
        "{\n"
        "    if (id >= GEN_TOPIC_COUNT || device_id == NULL || out == NULL) { return false; }\n"
        "    const size_t id_len = strlen(device_id);\n"
        "    if (id_len == 0 || id_len > GEN_TOPIC_DEVICE_ID_MAX) { return false; }\n"
        "    const size_t pre = strlen(GEN_TOPIC_INFO[id].prefix);\n"
        "    const size_t suf = strlen(GEN_TOPIC_INFO[id].suffix);\n"
        "    if (cap < pre + id_len + suf + 1) { return false; }\n"
        "    memcpy(out, GEN_TOPIC_INFO[id].prefix, pre);\n"
        "    memcpy(out + pre, device_id, id_len);\n"
        "    memcpy(out + pre + id_len, GEN_TOPIC_INFO[id].suffix, suf + 1);\n"
        "    return true;\n"
        "}\n"
    )
    out.append(
        "/** Which topic of device_id a received path is.\n"
        " *  @ctx any | non-blocking | topic need not be NUL-terminated, len is its length\n"
        " *  @ret GEN_TOPIC_NONE when the path belongs to no topic of this device\n"
        " */\n"
        "static inline gen_topic_id_t gen_topic_match(const char *topic, size_t len, const char *device_id)\n"
        "{\n"
        "    if (topic == NULL || device_id == NULL) { return GEN_TOPIC_NONE; }\n"
        "    const size_t id_len = strlen(device_id);\n"
        "    for (int id = 0; id < GEN_TOPIC_COUNT; id++) {\n"
        "        const size_t pre = strlen(GEN_TOPIC_INFO[id].prefix);\n"
        "        const size_t suf = strlen(GEN_TOPIC_INFO[id].suffix);\n"
        "        if (len != pre + id_len + suf) { continue; }\n"
        "        if (memcmp(topic, GEN_TOPIC_INFO[id].prefix, pre) != 0) { continue; }\n"
        "        if (memcmp(topic + pre, device_id, id_len) != 0) { continue; }\n"
        "        if (memcmp(topic + pre + id_len, GEN_TOPIC_INFO[id].suffix, suf) == 0) { return (gen_topic_id_t) id; }\n"
        "    }\n"
        "    return GEN_TOPIC_NONE;\n"
        "}\n"
    )
    for x in t["topics"]:
        name = x["id"]
        out.append(
            f"/** gen_topic_build fixed to GEN_TOPIC_{name.upper()}.\n *  @ctx any | non-blocking | caller owns out\n */\n"
            f"static inline bool gen_topic_{name}(const char *device_id, char *out, size_t cap)\n"
            f"{{\n    return gen_topic_build(GEN_TOPIC_{name.upper()}, device_id, out, cap);\n}}\n"
        )
    out += ["#ifdef __cplusplus", "}", "#endif"]
    return "\n".join(out) + "\n"


def gen_topics_py(t: dict) -> str:
    rows = "".join(
        f'    Topic("{x["id"]}", "{x["path"]}", "{x["direction"]}", {x["qos"]}, {x["retain"]}, "{x["schema"]}", {x["will"]}),\n'
        for x in t["topics"]
    )
    return banner("contracts/mqtt_topics.yaml", "#") + (
        "\nfrom dataclasses import dataclass\n\n"
        f"DEVICE_ID_MAX = {t['device_id_max']}\n"
        f"HEARTBEAT_INTERVAL_S = {t['heartbeat_interval_s']}\n"
        f"TELEMETRY_PERIOD_MS = {t['telemetry_period_ms']}\n"
        f"TELEMETRY_SAMPLES = {t['telemetry_samples']}\n\n\n"
        "@dataclass(frozen=True)\n"
        "class Topic:\n"
        "    id: str\n    path: str\n    direction: str\n    qos: int\n    retain: bool\n    schema: str\n    will: bool\n\n"
        "    def build(self, device_id: str) -> str:\n"
        '        return self.path.replace("{deviceId}", device_id)\n\n\n'
        f"TOPICS = (\n{rows})\n"
        "BY_ID = {t.id: t for t in TOPICS}\n\n\n"
        "def match(topic: str) -> tuple[Topic, str] | None:\n"
        "    for t in TOPICS:\n"
        '        prefix, suffix = t.path.split("{deviceId}")\n'
        "        if topic.startswith(prefix) and topic.endswith(suffix) and len(topic) > len(prefix) + len(suffix):\n"
        "            return t, topic[len(prefix) : len(topic) - len(suffix)]\n"
        "    return None\n"
    )


@dataclass
class Field:
    json_name: str
    c_name: str
    node: dict
    required: bool


def fields_of(node: dict) -> list[Field]:
    required = set(node.get("required", []))
    return [Field(k, snake(k), v, k in required) for k, v in node.get("properties", {}).items()]


class PayloadC:
    """Emit C structs plus cJSON parse and build functions for one schema tree."""

    def __init__(self) -> None:
        self.out: list[str] = []

    def enum(self, tname: str, values: list[str]) -> None:
        upper = tname.upper().removesuffix("_T")
        self.out.append("typedef enum {")
        self.out += [f"    {upper}_{v} = {i}," for i, v in enumerate(values)]
        self.out.append(f"    {upper}_COUNT = {len(values)},")
        self.out.append(f"}} {tname};\n")
        base = tname.removesuffix("_t")
        cases = "".join(f'    case {upper}_{v}: return "{v}";\n' for v in values)
        self.out.append(
            '/** Contract spelling of a value; "" when it is out of range.\n'
            " *  @ctx any | non-blocking | returns a static string\n */\n"
            f"static inline const char *{base}_str({tname} v)\n{{\n    switch (v) {{\n{cases}"
            '    default: return "";\n    }\n}\n'
        )
        checks = "".join(f'    if (strcmp(s, "{v}") == 0) {{ *out = {upper}_{v}; return true; }}\n' for v in values)
        self.out.append(
            "/** Value of a contract spelling.\n *  @ctx any | non-blocking\n"
            " *  @ret false for NULL or a spelling the contract does not list\n */\n"
            f"static inline bool {base}_parse(const char *s, {tname} *out)\n{{\n"
            f"    if (s == NULL || out == NULL) {{ return false; }}\n{checks}    return false;\n}}\n"
        )

    def ctype(self, owner: str, f: Field) -> tuple[str, str]:
        node = f.node
        if "enum" in node:
            return f"{owner}_{f.c_name}_t", ""
        kind = node["type"]
        if kind == "string":
            return "char", f"[{string_bytes(node) + 1}]"
        if kind == "integer":
            return c_int_type(node), ""
        if kind == "boolean":
            return "bool", ""
        if kind == "object":
            return f"{owner}_{f.c_name}_t", ""
        if kind == "array":
            items = node["items"]
            inner = f"{owner}_{f.c_name}_item_t" if items["type"] == "object" else c_int_type(items)
            return inner, f"[{node['maxItems']}]"
        raise SystemExit(f"{owner}.{f.json_name}: unsupported type {kind}")

    def declare(self, owner: str, node: dict) -> None:
        for f in fields_of(node):
            if "enum" in f.node:
                self.enum(f"{owner}_{f.c_name}_t", f.node["enum"])
            elif f.node.get("type") == "object":
                self.declare(f"{owner}_{f.c_name}", f.node)
            elif f.node.get("type") == "array" and f.node["items"].get("type") == "object":
                self.declare(f"{owner}_{f.c_name}_item", f.node["items"])
        self.struct(owner, node)
        self.from_json(owner, node)
        self.to_json(owner, node)

    def struct(self, owner: str, node: dict) -> None:
        self.out.append("typedef struct {")
        fs = fields_of(node)
        for f in fs:
            ctype, suffix = self.ctype(owner, f)
            self.out.append(f"    {ctype} {f.c_name}{suffix};")
            if f.node.get("type") == "array":
                self.out.append(f"    uint8_t {f.c_name}_count;")
        self.out += [f"    bool has_{f.c_name};" for f in fs if not f.required]
        self.out.append(f"}} {owner}_t;\n")

    def parse_value(self, owner: str, f: Field, src: str, dst: str, indent: str) -> list[str]:
        node, lines = f.node, []
        if "enum" in node:
            lines.append(
                f"{indent}if (!cJSON_IsString({src}) || !{owner}_{f.c_name}_parse({src}->valuestring, &{dst})) {{ return false; }}"
            )
        elif node["type"] == "string":
            lines.append(
                f"{indent}if (!cJSON_IsString({src}) || strlen({src}->valuestring) >= sizeof({dst})) {{ return false; }}"
            )
            lines.append(f"{indent}strcpy({dst}, {src}->valuestring);")
        elif node["type"] == "integer":
            lines.append(f"{indent}if (!cJSON_IsNumber({src})) {{ return false; }}")
            lines.append(
                f"{indent}if ({src}->valuedouble < {node['minimum']}.0 || {src}->valuedouble > {node['maximum']}.0) {{ return false; }}"
            )
            lines.append(f"{indent}{dst} = ({c_int_type(node)}) {src}->valuedouble;")
        elif node["type"] == "boolean":
            lines.append(f"{indent}if (!cJSON_IsBool({src})) {{ return false; }}")
            lines.append(f"{indent}{dst} = cJSON_IsTrue({src});")
        elif node["type"] == "object":
            lines.append(f"{indent}if (!{owner}_{f.c_name}_from_json({src}, &{dst})) {{ return false; }}")
        return lines

    def from_json(self, owner: str, node: dict) -> None:
        body = [
            f"/** Fill out from a parsed {owner.split('_item')[0]} object, checking type, range, enum and size.",
            " *  @ctx any | non-blocking | out is cleared first; strings are copied, not borrowed",
            " *  @ret false on the first field outside the contract; out is then partly filled",
            " */",
            f"static inline bool {owner}_from_json(const cJSON *root, {owner}_t *out)",
            "{",
        ]
        body += ["    if (!cJSON_IsObject(root) || out == NULL) { return false; }", "    memset(out, 0, sizeof(*out));"]
        body.append("    const cJSON *item = NULL;")
        for f in fields_of(node):
            body.append(f'    item = cJSON_GetObjectItemCaseSensitive(root, "{f.json_name}");')
            miss = "return false;" if f.required else "item = NULL;"
            body.append(f"    if (item == NULL) {{ {miss} }}")
            body.append("    if (item != NULL) {")
            if f.node.get("type") == "array":
                items = f.node["items"]
                elem = Field(f.json_name, f.c_name, items, True)
                body.append(
                    f"        if (!cJSON_IsArray(item) || cJSON_GetArraySize(item) > {f.node['maxItems']}) {{ return false; }}"
                )
                body.append(f"        if (cJSON_GetArraySize(item) < {f.node.get('minItems', 0)}) {{ return false; }}")
                body.append("        const cJSON *el = NULL;")
                body.append("        cJSON_ArrayForEach(el, item) {")
                dst = f"out->{f.c_name}[out->{f.c_name}_count]"
                if items["type"] == "object":
                    body.append(f"            if (!{owner}_{f.c_name}_item_from_json(el, &{dst})) {{ return false; }}")
                else:
                    body += self.parse_value(owner, elem, "el", dst, "            ")
                body.append(f"            out->{f.c_name}_count++;")
                body.append("        }")
            else:
                body += self.parse_value(owner, f, "item", f"out->{f.c_name}", "        ")
            if not f.required:
                body.append(f"        out->has_{f.c_name} = true;")
            body.append("    }")
        body += ["    return true;", "}\n"]
        self.out += body

    def build_value(self, owner: str, f: Field, src: str, parent: str, key: str | None, indent: str) -> list[str]:
        node = f.node
        add = (
            (lambda kind, val: f'{indent}cJSON_Add{kind}ToObject({parent}, "{key}", {val});')
            if key
            else (lambda kind, val: f"{indent}cJSON_AddItemToArray({parent}, cJSON_Create{kind}({val}));")
        )
        if "enum" in node:
            return [add("String", f"{owner}_{f.c_name}_str({src})")]
        kind = node["type"]
        if kind == "string":
            return [add("String", src)]
        if kind == "integer":
            return [add("Number", f"(double) {src}")]
        if kind == "boolean":
            return [add("Bool", src)]
        if kind == "object":
            call = f"{owner}_{f.c_name}_to_json(&{src})"
            return [f'{indent}cJSON_AddItemToObject({parent}, "{key}", {call});']
        raise SystemExit(f"{owner}.{f.json_name}: unsupported type {kind}")

    def to_json(self, owner: str, node: dict) -> None:
        body = [
            f"/** Build the JSON object of one {owner.split('_item')[0]} for publishing.",
            " *  @ctx any | non-blocking | allocates through cJSON: caller frees with cJSON_Delete",
            " *  @ret NULL when the root object cannot be allocated",
            " */",
            f"static inline cJSON *{owner}_to_json(const {owner}_t *in)",
            "{",
        ]
        body += ["    if (in == NULL) { return NULL; }", "    cJSON *root = cJSON_CreateObject();"]
        body.append("    if (root == NULL) { return NULL; }")
        for f in fields_of(node):
            indent = "    "
            if not f.required:
                body.append(f"    if (in->has_{f.c_name}) {{")
                indent = "        "
            if f.node.get("type") == "array":
                items = f.node["items"]
                body.append(f'{indent}cJSON *{f.c_name}_arr = cJSON_AddArrayToObject(root, "{f.json_name}");')
                body.append(f"{indent}for (int i = 0; {f.c_name}_arr != NULL && i < in->{f.c_name}_count; i++) {{")
                if items["type"] == "object":
                    body.append(
                        f"{indent}    cJSON_AddItemToArray({f.c_name}_arr, {owner}_{f.c_name}_item_to_json(&in->{f.c_name}[i]));"
                    )
                else:
                    elem = Field(f.json_name, f.c_name, items, True)
                    body += self.build_value(
                        owner, elem, f"in->{f.c_name}[i]", f"{f.c_name}_arr", None, indent + "    "
                    )
                body.append(f"{indent}}}")
            else:
                body += self.build_value(owner, f, f"in->{f.c_name}", "root", f.json_name, indent)
            if not f.required:
                body.append("    }")
        body += ["    return root;", "}\n"]
        self.out += body


def schemas() -> list[tuple[str, dict]]:
    paths = sorted((CONTRACTS / "schema").glob("*.schema.json"))
    return [(p.name.removesuffix(".schema.json"), json.loads(p.read_text(encoding="utf-8"))) for p in paths]


def gen_payload_h() -> str:
    gen = PayloadC()
    for name, schema in schemas():
        gen.declare(name, schema)
    head = [banner("contracts/schema/*.schema.json", "//"), "#pragma once\n"]
    head += ["#include <stdbool.h>", "#include <stdint.h>", "#include <string.h>\n", '#include "cJSON.h"\n']
    head += ["#ifdef __cplusplus", 'extern "C" {', "#endif\n"]
    return "\n".join(head + gen.out + ["#ifdef __cplusplus", "}", "#endif"]) + "\n"


def py_type(owner: str, name: str, node: dict, extra: list[str]) -> str:
    if "enum" in node:
        alias = pascal(owner) + pascal(name)
        extra.append(f"{alias} = Literal[{', '.join(repr(v) for v in node['enum'])}]\n")
        return alias
    kind = node["type"]
    if kind == "object":
        cls = pascal(owner) + pascal(name)
        extra.append(py_typed_dict(cls, owner + "_" + snake(name), node))
        return cls
    if kind == "array":
        return f"list[{py_type(owner, name + '_item', node['items'], extra)}]"
    return {"string": "str", "integer": "int", "boolean": "bool"}[kind]


def py_typed_dict(cls: str, owner: str, node: dict) -> str:
    extra: list[str] = []
    rows = []
    for f in fields_of(node):
        t = py_type(owner, f.json_name, f.node, extra)
        rows.append(f"    {f.json_name}: {t if f.required else f'NotRequired[{t}]'}\n")
    return "".join(extra) + f"\n\nclass {cls}(TypedDict):\n" + "".join(rows) + "\n"


def py_schemas() -> str:
    rows = [
        "\n\n# Every schema as parsed JSON, for host/ to validate what boards send (KEHOACH 7.7).",
        "SCHEMAS: dict[str, dict] = {",
    ]
    for name, schema in schemas():
        literal = pprint.pformat(schema, width=112, sort_dicts=False).replace("\n", "\n    ")
        rows.append(f"    {name!r}: {literal},")
    return "\n".join([*rows, "}"]) + "\n"


def gen_payload_py() -> str:
    body = "".join(py_typed_dict(pascal(name), name, schema) for name, schema in schemas())
    return banner("contracts/schema/*.schema.json", "#") + (
        "\nfrom typing import Literal, NotRequired, TypedDict\n" + body.rstrip("\n") + "\n" + py_schemas()
    )


def outputs() -> dict[str, str]:
    g = grid_values()
    a = array_values(g)
    s = stream_values()
    t = topic_values()
    afe = afe_values()
    modules = afe_modules()
    init = banner("contracts/", "#")
    return {
        f"{COMMON_INC}/gen_grid.h": gen_grid_h(g),
        f"{COMMON_INC}/gen_array.h": gen_array_h(a),
        f"{COMMON_INC}/gen_stream.h": gen_stream_h(s),
        f"{MQTT_INC}/gen_topics.h": gen_topics_h(t),
        f"{MQTT_INC}/gen_payload.h": gen_payload_h(),
        f"{AFE_INC}/gen_afe.h": gen_afe_h(afe),
        SDKCONFIG_AFE: gen_sdkconfig_afe(modules),
        f"{ML_GEN}/__init__.py": init,
        f"{ML_GEN}/grid.py": gen_grid_py(g),
        f"{ML_GEN}/array.py": gen_array_py(a),
        f"{ML_GEN}/afe.py": gen_afe_py(afe, modules),
        f"{HOST_GEN}/__init__.py": init,
        f"{HOST_GEN}/grid.py": gen_grid_py(g),
        f"{HOST_GEN}/stream.py": gen_stream_py(s),
        f"{HOST_GEN}/topics.py": gen_topics_py(t),
        f"{HOST_GEN}/payload.py": gen_payload_py(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-root", type=Path, default=REPO_ROOT, help="write under this root instead of the repo")
    args = parser.parse_args()
    for rel, text in outputs().items():
        path = args.out_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            print(f"wrote {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
