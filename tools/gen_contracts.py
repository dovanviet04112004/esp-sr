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
import unicodedata
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


LISTEN_NUMBERS = ("features", "pitch")


def listen_values(g: dict) -> dict:
    """listen.yaml, checked to hold numbers only in features and pitch; the cut and the window also in hops of the
    grid, rounded as Python rounds them, so the board cuts where Gate 3 does."""
    doc = load_yaml("listen.yaml")
    for section in LISTEN_NUMBERS:
        for name, value in doc[section].items():
            if not is_number(value):
                raise ValueError(f"listen.yaml {section}.{name} must be a number, got {value!r}")
    rate = g["frames_per_s"]
    return {
        **doc,
        "gap_hops": round(doc["utterance"]["gap_s"] * rate),
        "min_hops": round(doc["utterance"]["min_s"] * rate),
        "lead_hops": round(doc["utterance"]["lead_s"] * rate),
        "window_hops": round(doc["window_s"] * rate),
    }


def c_initializer(fields: dict) -> str:
    """A designated initializer of a struct whose fields are named as the contract's keys."""
    return "{" + ", ".join(f".{name} = {c_number(value)}" for name, value in fields.items()) + "}"


def gen_listen_h(v: dict) -> str:
    lines = [
        banner("contracts/listen.yaml", "//"),
        "#pragma once\n",
        f"#define GEN_LISTEN_VERSION {v['version']}",
        f"#define GEN_LISTEN_N_BANDS {v['features']['n_bands']}",
        f"#define GEN_LISTEN_MEL_CONFIG {c_initializer(v['features'])}       // dsp_spec_mel_config_t",
        f"#define GEN_LISTEN_PITCH_CONFIG {c_initializer(v['pitch'])}       // dsp_spec_pitch_config_t",
        f"#define GEN_LISTEN_UTTERANCE_GAP_HOPS {v['gap_hops']}",
        f"#define GEN_LISTEN_UTTERANCE_MIN_HOPS {v['min_hops']}",
        f"#define GEN_LISTEN_UTTERANCE_LEAD_HOPS {v['lead_hops']}",
        f"#define GEN_LISTEN_WINDOW_HOPS {v['window_hops']}",
    ]
    return "\n".join(lines) + "\n"


def gen_listen_py(v: dict) -> str:
    def literal(value: object) -> str:
        return pprint.pformat(value, width=112, sort_dicts=False)

    return banner("contracts/listen.yaml", "#") + (
        "\n"
        f"VERSION = {v['version']}\n"
        f"FEATURES = {literal(v['features'])}\n"
        f"PITCH = {literal(v['pitch'])}\n"
        f"N_BANDS = {v['features']['n_bands']}\n"
        f"UTTERANCE_GAP_S = {v['utterance']['gap_s']!r}\n"
        f"UTTERANCE_MIN_S = {v['utterance']['min_s']!r}\n"
        f"UTTERANCE_LEAD_S = {v['utterance']['lead_s']!r}\n"
        f"UTTERANCE_GAP_HOPS = {v['gap_hops']}\n"
        f"UTTERANCE_MIN_HOPS = {v['min_hops']}\n"
        f"UTTERANCE_LEAD_HOPS = {v['lead_hops']}\n"
        f"WINDOW_S = {v['window_s']!r}\n"
        f"WINDOW_HOPS = {v['window_hops']}\n"
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


LANG_INC = "firmware/components/lang_vi/priv_include"
LANG_UNIT_NONE = 0xFF
LANG_RULE_KEYS = (
    "glide_drops_onset",
    "velar_codas",
    "velar_keep_after",
    "palatal_codas",
    "centralize",
    "centralize_before",
    "tone",
)
LANG_RHYME_ZERO_ONSET_ONLY = 1
LANG_RHYME_ONSET_ONLY = 2


def _lang_check(ok: bool, what: str) -> None:
    if not ok:
        raise ValueError(f"lang_vi.yaml: {what}")


def _lang_strings(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in _lang_strings(k) + _lang_strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _lang_strings(v)]
    return []


def lang_vi_values() -> dict:
    """lang_vi.yaml, checked: units known, letters and marks as Unicode composes them, text in NFC lower case."""
    doc = load_yaml("lang_vi.yaml")
    tables = ("onsets", "rhymes", "q_rhymes", "symbols", "dictionary")
    _lang_check(all(isinstance(k, str) for t in tables for k in doc[t]), "YAML read a key as a non-string: quote it")
    units = doc["units"]
    _lang_check(len(set(units)) == len(units) < LANG_UNIT_NONE, "units must be distinct and fewer than 255")
    known = set(units)
    composed = {k: v for k, v in doc.items() if k != "tone_mark_aliases"}
    _lang_check(all(unicodedata.is_normalized("NFC", s) for s in _lang_strings(composed)), "strings must be NFC")
    n_dialects = len(doc["dialects"])
    tones, marks = doc["tones"], doc["tone_marks"]
    _lang_check(len(tones) == len(marks) and set(tones) <= known, "tones must be units, one mark each")
    for base, forms in doc["vowels"].items():
        _lang_check(len(forms) == len(tones) and forms[0] == base, f"vowel {base} needs its {len(tones)} tone forms")
        for form, mark in zip(forms, marks, strict=True):
            _lang_check(unicodedata.normalize("NFC", base + mark) == form, f"{form} is not {base} with its mark")
            _lang_check(len(form.upper()) == 1 and form.upper().lower() == form, f"{form} has no single upper case")
    for mark, pairs in doc["modifiers"].items():
        _lang_check(all(unicodedata.normalize("NFC", a + mark) == b for a, b in pairs.items()), f"modifier {mark!r}")
    _lang_check(all(unicodedata.normalize("NFC", a) == b for a, b in doc["tone_mark_aliases"].items()), "aliases")
    for spelling, per_dialect in doc["onsets"].items():
        _lang_check(len(per_dialect) == n_dialects and set(per_dialect) <= known, f"onset {spelling}")
    for table in ("rhymes", "q_rhymes"):
        for spelling, (glide, nucleus, coda) in doc[table].items():
            _lang_check(glide in ("", "w") and nucleus in known and coda in known | {""}, f"{table} {spelling}")
    _lang_check(set(doc["zero_onset_only"]) | set(doc["onset_only"]) <= set(doc["rhymes"]), "rhyme contexts")
    _lang_check(set(doc["checked_codas"]) | set(doc["checked_tones"]) <= known, "checked codas and tones")
    for dialect in doc["dialects"]:
        rules = doc["dialect_rules"][dialect]
        _lang_check(tuple(rules) == LANG_RULE_KEYS, f"{dialect} rules must be {LANG_RULE_KEYS}")
        _lang_check(set(_lang_strings(list(rules.values()))) <= known, f"{dialect} rules name unknown units")
    numbers = doc["numbers"]
    _lang_check(len(numbers["digits"]) == 10, "numbers need ten digits")
    _lang_check(all(len(numbers[k]) == n_dialects for k in ("thousand", "zero_tens")), "per-dialect number words")
    _lang_check(all(k == k.lower() for k in doc["dictionary"]), "dictionary keys must be lower case")
    letters = {}
    for base, forms in doc["vowels"].items():
        for tone, form in enumerate(forms):
            letters[form] = letters[form.upper()] = (base, tone)
    for extra in doc["extra_letters"]:
        letters[extra] = letters[extra.upper()] = (extra, 0)
    return {**doc, "letters": letters}


def c_str(text: str) -> str:
    """A C literal of text in UTF-8; other bytes as three-digit octal, which never swallows the next character."""
    body = "".join(chr(b) if 0x20 <= b < 0x7F and chr(b) not in '"\\' else f"\\{b:03o}" for b in text.encode("utf-8"))
    return f'"{body}"'


def _c_array(ctype: str, name: str, size: str, rows: list[str]) -> str:
    return f"static const {ctype} {name}[{size}] = {{\n" + "".join(f"    {r},\n" for r in rows) + "};\n"


def gen_units_h(v: dict) -> str:
    uid = {name: i for i, name in enumerate(v["units"])}
    return (
        banner("contracts/lang_vi.yaml", "//")
        + "\n#pragma once\n\n#include <stdint.h>\n\n"
        + f"#define GEN_UNITS_N_TONES {len(v['tones'])}\n\n"
        + "// lang_vi's tone units: every syllable's units end on exactly one of them.\n"
        + _c_array("uint8_t", "GEN_UNITS_TONES", "GEN_UNITS_N_TONES", [str(uid[t]) for t in v["tones"]])
    )


def gen_lang_vi_h(v: dict) -> str:
    uid = {name: i for i, name in enumerate(v["units"])}

    def unit(name: str) -> str:
        return str(uid[name]) if name else "GEN_LANG_VI_UNIT_NONE"

    def units(names: list[str]) -> str:
        return "{" + ", ".join(unit(n) for n in names) + "}"

    def pairs(mapping: dict) -> str:
        return "{" + ", ".join(f"{{{unit(a)}, {unit(b)}}}" for a, b in mapping.items()) + "}" if mapping else "{{0, 0}}"

    def rhymes(table: str) -> list[str]:
        flags = {s: LANG_RHYME_ZERO_ONSET_ONLY for s in v["zero_onset_only"]} if table == "rhymes" else {}
        flags |= {s: LANG_RHYME_ONSET_ONLY for s in v["onset_only"]} if table == "rhymes" else {}
        rows = v[table].items()
        return [f"{{{c_str(s)}, {unit(g)}, {unit(n)}, {unit(c)}, {flags.get(s, 0)}}}" for s, (g, n, c) in rows]

    rules = [v["dialect_rules"][d] for d in v["dialects"]]
    rule_max = max(len(r[k]) for r in rules for k in LANG_RULE_KEYS)
    spelling_rules = [(o, True, ls) for o, ls in v["front_only"].items()]
    spelling_rules += [(o, False, ls) for o, ls in v["never_front"].items()]
    front_max = max(len(ls) for _, _, ls in spelling_rules)
    onsets = sorted(v["onsets"].items(), key=lambda item: -len(item[0].encode("utf-8")))
    numbers = v["numbers"]
    tone_marks = [(m, t) for t, m in enumerate(v["tone_marks"]) if m]
    tone_marks += [(a, v["tone_marks"].index(m)) for a, m in v["tone_mark_aliases"].items()]
    modifiers = [(m, a, b) for m, ps in v["modifiers"].items() for a, b in ps.items()]
    counts = {
        "VERSION": v["version"],
        "N_UNITS": len(v["units"]),
        "N_DIALECTS": len(v["dialects"]),
        "N_TONES": len(v["tones"]),
        "N_VOWELS": len(v["vowels"]),
        "N_LETTERS": len(v["letters"]),
        "N_TONE_MARKS": len(tone_marks),
        "N_MODIFIERS": len(modifiers),
        "N_ONSETS": len(onsets),
        "N_SPELLING_RULES": len(spelling_rules),
        "FRONT_MAX": front_max,
        "N_NO_GLIDE": len(v["no_glide"]),
        "N_GI_SHARES_BEFORE": len(v["gi_shares_before"]),
        "N_GI_NEVER_BEFORE": len(v["gi_never_before"]),
        "N_RHYMES": len(v["rhymes"]),
        "N_Q_RHYMES": len(v["q_rhymes"]),
        "N_CHECKED_CODAS": len(v["checked_codas"]),
        "N_CHECKED_TONES": len(v["checked_tones"]),
        "RULE_MAX": rule_max,
        "NUMBER_MAX_DIGITS": numbers["max_digits"],
        "N_ORDINALS": len(numbers["ordinals"]),
        "N_SYMBOLS": len(v["symbols"]),
        "N_DICTIONARY": len(v["dictionary"]),
        "DICTIONARY_KEY_MAX_BYTES": max(len(k.encode("utf-8")) for k in v["dictionary"]),
    }
    out = [banner("contracts/lang_vi.yaml", "//"), "#pragma once\n", "#include <stdbool.h>", "#include <stdint.h>\n"]
    out += [f"#define GEN_LANG_VI_{name} {value}" for name, value in counts.items()]
    out += [
        f"#define GEN_LANG_VI_UNIT_NONE 0x{LANG_UNIT_NONE:02X}",
        f"#define GEN_LANG_VI_RHYME_ZERO_ONSET_ONLY {LANG_RHYME_ZERO_ONSET_ONLY}u",
        f"#define GEN_LANG_VI_RHYME_ONSET_ONLY {LANG_RHYME_ONSET_ONLY}u",
    ]
    for key in ("ten", "tens", "hundred", "million", "billion", "one_after_tens", "four_after_tens"):
        out.append(f"#define GEN_LANG_VI_NUMBER_{key.upper()} {c_str(numbers[key])}")
    for key in ("five_after_ten", "decimal_comma", "dot", "ordinal_word"):
        out.append(f"#define GEN_LANG_VI_NUMBER_{key.upper()} {c_str(numbers[key])}")
    out += [
        "",
        "typedef struct { uint32_t codepoint; uint32_t base; uint8_t tone; } gen_lang_vi_letter_t;",
        "typedef struct { uint32_t mark; uint8_t tone; } gen_lang_vi_tone_mark_t;",
        "typedef struct { uint32_t mark; uint32_t from; uint32_t to; } gen_lang_vi_modifier_t;",
        "typedef struct { const char *spelling; uint8_t units[GEN_LANG_VI_N_DIALECTS]; } gen_lang_vi_onset_t;",
        "typedef struct {",
        "    const char *onset;",
        "    bool only;",
        "    uint8_t n_letters;",
        "    uint32_t letters[GEN_LANG_VI_FRONT_MAX];",
        "} gen_lang_vi_spelling_rule_t;",
        "typedef struct {",
        "    const char *spelling;",
        "    uint8_t glide;",
        "    uint8_t nucleus;",
        "    uint8_t coda;",
        "    uint8_t flags;",
        "} gen_lang_vi_rhyme_t;",
        "typedef struct { uint8_t from; uint8_t to; } gen_lang_vi_pair_t;",
        "typedef struct {",
    ]
    for key in LANG_RULE_KEYS:
        kind = "gen_lang_vi_pair_t" if isinstance(rules[0][key], dict) else "uint8_t"
        out += [f"    uint8_t n_{key};", f"    {kind} {key}[GEN_LANG_VI_RULE_MAX];"]
    out += [
        "} gen_lang_vi_dialect_rules_t;",
        "typedef struct { uint32_t value; const char *word; } gen_lang_vi_ordinal_t;",
        "typedef struct { uint32_t codepoint; const char *text; } gen_lang_vi_symbol_t;",
        "typedef struct { const char *key; const char *text; } gen_lang_vi_entry_t;",
        "",
    ]
    out.append(_c_array("char *const", "GEN_LANG_VI_UNIT_NAMES", "GEN_LANG_VI_N_UNITS", [c_str(u) for u in v["units"]]))
    out.append(_c_array("uint8_t", "GEN_LANG_VI_TONE_UNITS", "GEN_LANG_VI_N_TONES", [unit(t) for t in v["tones"]]))
    out.append(
        _c_array(
            "uint32_t",
            "GEN_LANG_VI_VOWEL_FORMS",
            "GEN_LANG_VI_N_VOWELS][GEN_LANG_VI_N_TONES",
            ["{" + ", ".join(f"0x{ord(f):04X}" for f in forms) + "}" for forms in v["vowels"].values()],
        )
    )
    out.append(
        _c_array(
            "gen_lang_vi_letter_t",
            "GEN_LANG_VI_LETTERS",
            "GEN_LANG_VI_N_LETTERS",
            [f"{{0x{ord(c):04X}, 0x{ord(b):04X}, {t}}}" for c, (b, t) in v["letters"].items()],
        )
    )
    out.append(
        _c_array(
            "gen_lang_vi_tone_mark_t",
            "GEN_LANG_VI_TONE_MARKS",
            "GEN_LANG_VI_N_TONE_MARKS",
            [f"{{0x{ord(m):04X}, {t}}}" for m, t in tone_marks],
        )
    )
    out.append(
        _c_array(
            "gen_lang_vi_modifier_t",
            "GEN_LANG_VI_MODIFIERS",
            "GEN_LANG_VI_N_MODIFIERS",
            [f"{{0x{ord(m):04X}, 0x{ord(a):04X}, 0x{ord(b):04X}}}" for m, a, b in modifiers],
        )
    )
    out.append(
        _c_array(
            "gen_lang_vi_onset_t",
            "GEN_LANG_VI_ONSETS",
            "GEN_LANG_VI_N_ONSETS",
            [f"{{{c_str(s)}, {units(us)}}}" for s, us in onsets],
        )
    )
    out.append(
        _c_array(
            "gen_lang_vi_spelling_rule_t",
            "GEN_LANG_VI_SPELLING_RULES",
            "GEN_LANG_VI_N_SPELLING_RULES",
            [
                f"{{{c_str(o)}, {'true' if only else 'false'}, {len(ls)}, "
                + "{"
                + ", ".join(f"0x{ord(ch):04X}" for ch in ls)
                + "}}"
                for o, only, ls in spelling_rules
            ],
        )
    )
    out.append(
        _c_array("char *const", "GEN_LANG_VI_NO_GLIDE", "GEN_LANG_VI_N_NO_GLIDE", [c_str(o) for o in v["no_glide"]])
    )
    out.append(
        _c_array(
            "uint32_t",
            "GEN_LANG_VI_GI_SHARES_BEFORE",
            "GEN_LANG_VI_N_GI_SHARES_BEFORE",
            [f"0x{ord(ch):04X}" for ch in v["gi_shares_before"]],
        )
    )
    out.append(
        _c_array(
            "uint32_t",
            "GEN_LANG_VI_GI_NEVER_BEFORE",
            "GEN_LANG_VI_N_GI_NEVER_BEFORE",
            [f"0x{ord(ch):04X}" for ch in v["gi_never_before"]],
        )
    )
    out.append(_c_array("gen_lang_vi_rhyme_t", "GEN_LANG_VI_RHYMES", "GEN_LANG_VI_N_RHYMES", rhymes("rhymes")))
    out.append(_c_array("gen_lang_vi_rhyme_t", "GEN_LANG_VI_Q_RHYMES", "GEN_LANG_VI_N_Q_RHYMES", rhymes("q_rhymes")))
    out.append(
        _c_array(
            "uint8_t", "GEN_LANG_VI_CHECKED_CODAS", "GEN_LANG_VI_N_CHECKED_CODAS", [unit(c) for c in v["checked_codas"]]
        )
    )
    out.append(
        _c_array(
            "uint8_t", "GEN_LANG_VI_CHECKED_TONES", "GEN_LANG_VI_N_CHECKED_TONES", [unit(t) for t in v["checked_tones"]]
        )
    )
    rule_rows = []
    for r in rules:
        fields = []
        for key in LANG_RULE_KEYS:
            body = pairs(r[key]) if isinstance(r[key], dict) else units(r[key]) if r[key] else "{0}"
            fields.append(f".n_{key} = {len(r[key])}, .{key} = {body}")
        rule_rows.append("{" + ", ".join(fields) + "}")
    out.append(
        _c_array("gen_lang_vi_dialect_rules_t", "GEN_LANG_VI_DIALECT_RULES", "GEN_LANG_VI_N_DIALECTS", rule_rows)
    )
    out.append(_c_array("char *const", "GEN_LANG_VI_NUMBER_DIGITS", "10", [c_str(d) for d in numbers["digits"]]))
    for key in ("thousand", "zero_tens"):
        out.append(
            _c_array(
                "char *const",
                f"GEN_LANG_VI_NUMBER_{key.upper()}",
                "GEN_LANG_VI_N_DIALECTS",
                [c_str(w) for w in numbers[key]],
            )
        )
    out.append(
        _c_array(
            "gen_lang_vi_ordinal_t",
            "GEN_LANG_VI_ORDINALS",
            "GEN_LANG_VI_N_ORDINALS",
            [f"{{{n}, {c_str(w)}}}" for n, w in numbers["ordinals"].items()],
        )
    )
    out.append(
        _c_array(
            "gen_lang_vi_symbol_t",
            "GEN_LANG_VI_SYMBOLS",
            "GEN_LANG_VI_N_SYMBOLS",
            [f"{{0x{ord(s):04X}, {c_str(t)}}}" for s, t in v["symbols"].items()],
        )
    )
    out.append(
        _c_array(
            "gen_lang_vi_entry_t",
            "GEN_LANG_VI_DICTIONARY",
            "GEN_LANG_VI_N_DICTIONARY",
            [f"{{{c_str(k)}, {c_str(t)}}}" for k, t in v["dictionary"].items()],
        )
    )
    return "\n".join(out)


def gen_lang_vi_py(v: dict) -> str:
    names = [
        "version",
        "dialects",
        "units",
        "tones",
        "tone_marks",
        "tone_mark_aliases",
        "vowels",
        "modifiers",
        "extra_letters",
        "letters",
        "onsets",
        "front_only",
        "never_front",
        "no_glide",
        "gi_shares_before",
        "gi_never_before",
        "rhymes",
        "zero_onset_only",
        "onset_only",
        "q_rhymes",
        "checked_codas",
        "checked_tones",
        "dialect_rules",
        "numbers",
        "symbols",
        "dictionary",
    ]
    body = "".join(f"{n.upper()} = {pprint.pformat(v[n], width=116, sort_dicts=False)}\n" for n in names)
    # Combining marks only ever sit inside string literals here; escaped, they stay visible.
    body = "".join(f"\\u{ord(ch):04x}" if unicodedata.category(ch) == "Mn" else ch for ch in body)
    return banner("contracts/lang_vi.yaml", "#") + "\n" + body


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
    lang = lang_vi_values()
    listen = listen_values(g)
    init = banner("contracts/", "#")
    return {
        f"{COMMON_INC}/gen_grid.h": gen_grid_h(g),
        f"{COMMON_INC}/gen_array.h": gen_array_h(a),
        f"{COMMON_INC}/gen_stream.h": gen_stream_h(s),
        f"{COMMON_INC}/gen_units.h": gen_units_h(lang),
        f"{COMMON_INC}/gen_listen.h": gen_listen_h(listen),
        f"{MQTT_INC}/gen_topics.h": gen_topics_h(t),
        f"{MQTT_INC}/gen_payload.h": gen_payload_h(),
        f"{AFE_INC}/gen_afe.h": gen_afe_h(afe),
        SDKCONFIG_AFE: gen_sdkconfig_afe(modules),
        f"{LANG_INC}/gen_lang_vi.h": gen_lang_vi_h(lang),
        f"{ML_GEN}/__init__.py": init,
        f"{ML_GEN}/grid.py": gen_grid_py(g),
        f"{ML_GEN}/array.py": gen_array_py(a),
        f"{ML_GEN}/afe.py": gen_afe_py(afe, modules),
        f"{ML_GEN}/lang_vi.py": gen_lang_vi_py(lang),
        f"{ML_GEN}/listen.py": gen_listen_py(listen),
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
