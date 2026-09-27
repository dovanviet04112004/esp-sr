"""Write the golden cases of lang_vi into contracts/golden/{g2p,normalize,lexicon}/ (KEHOACH 3.12, 3.14).

Run from ml/: uv run python -m srpipe.lang.emit_golden. g2p covers every syllable Vietnamese spelling builds, one case
per onset, plus labelled phrases; the C side must match exactly. Output is deterministic.
"""

from __future__ import annotations

import argparse
import json
import unicodedata
from pathlib import Path

import numpy as np

from srpipe.generated import lang_vi as rules
from srpipe.golden.gold import write_gold
from srpipe.lang import g2p, lexicon
from srpipe.lang.normalize import ESP_OK, TEXT_MAX_BYTES, LangError, normalize

REPO_ROOT = Path(__file__).resolve().parents[4]
GOLDEN_ROOT = REPO_ROOT / "contracts" / "golden"
COMMANDS = REPO_ROOT / "contracts" / "commands" / "default_vi.json"
UNIT_PAD = 0xFF
UNITS_PER_SYLLABLE = 5
MARKED_VOWELS = frozenset("ăâêôơư")
SOUTH = rules.DIALECTS.index("south")
TOO_LONG_SYLLABLES = 10
G2P_PHRASES: list[str | bytes] = [
    "xin chào",
    "nguyễn văn an",
    "thành phố hồ chí minh",
    "khuya rồi",
    "quốc gia",
    "giường ngủ",
    "nghiêng ghế",
    "khuỷu tay",
    "ngoằn ngoèo",
    "oái oăm",
    "uyển chuyển",
    "hoà hòa thuý thúy",
    "  bật   đèn  ",
    "",
    "   ",
    "ci",
    "ka",
    "gé",
    "ngé",
    "gha",
    "ngha",
    "qa",
    "cuy",
    "coa",
    "bl",
    "tàc",
    "mãp",
    "đđ",
    "áà",
    "giy",
    "iêu",
    "Bật",
    "5",
    "fan",
    "b́",
    "bật đèn fan",
    " ".join(["nguyễn"] * TOO_LONG_SYLLABLES),
    b"\xc3",
]
NORMALIZE_INPUTS: list[str | bytes] = [
    "Bật Đèn",
    "BẬT ĐÈN!",
    unicodedata.normalize("NFD", "bật đèn giảm âm lượng"),
    "ặ ặ ợ é̂",
    "é à",
    "hòa hoà thúy thuý",
    "Wi-Fi",
    "wifi",
    "TV.",
    "(tivi)",
    "ok",
    "7h30",
    "100đ",
    "50%",
    "A&B",
    "1+1",
    "0",
    "5",
    "10",
    "15",
    "21",
    "24",
    "25",
    "105",
    "110",
    "1005",
    "2024",
    "12345",
    "1.000.000",
    "1000000000",
    "999999999999",
    "1000000000000",
    "007",
    "2,5",
    "3,14",
    "2,05",
    "1.5",
    "1.2.3",
    "1.000.",
    "thứ 1",
    "thứ 4",
    "thứ 2",
    "Thứ 4",
    "số 4",
    "mp3",
    "4g",
    "\tbật\nđèn\r",
    "đèn 💡 sáng",
    "Nguyễn Văn An",
    "TP. HCM",
    "café",
    "naïve",
    "b́",
    "",
    "...",
    "a" * (TEXT_MAX_BYTES + 1),
    "1234567890 " * 5,
    b"\xc3",
    b"\xc0\xaf",
    b"\xed\xa0\x80",
    b"\xf4\x90\x80\x80",
    b"\xff",
]
LEXICON_LINES: list[tuple[str | bytes, int]] = [
    ("Bật đèn phòng khách", 7),
    ("đặt hẹn giờ 105 phút", 7),
    ("mở YouTube", 7),
    ("tăng âm lượng lên 50%", 7),
    ("gọi Nguyễn Văn An", 7),
    ("bật wifi", 7),
    ("chuyện gì vậy", 7),
    ("quốc ca", 7),
    ("bật đèn", 1),
    ("bật đèn", 4),
    ("nguyễn", 5),
    ("bật đèn", 0),
    ("bật đèn", 8),
    ("", 7),
    ("!!!", 7),
    ("bật fan", 7),
    (" ".join(["nguyễn"] * TOO_LONG_SYLLABLES), 7),
    (b"\xff", 7),
]


def tone_position(written: str, units: list[str], onset: str) -> int:
    """Where a tone mark conventionally sits in a written rhyme; -1 for the i of gi when the rhyme has no vowel."""
    vowels = [i for i, ch in enumerate(written) if ch in rules.VOWELS]
    marked = [i for i in vowels if written[i] in MARKED_VOWELS]
    if not vowels or marked:
        return marked[-1] if marked else -1
    glide, _, coda = units
    if glide and onset != "qu":
        vowels = vowels[1:]
    if coda in ("j", "w") and len(vowels) > 1 and vowels[-1] == len(written) - 1:
        vowels = vowels[:-1]
    return vowels[0]


def spell(onset: str, written: str, units: list[str], tone: int) -> str:
    text = onset + written
    at = len(onset) + tone_position(written, units, onset)
    return text[:at] + rules.VOWELS[text[at]][tone] + text[at + 1 :]


def valid_syllables(onset: str) -> list[str]:
    """Every syllable spelling builds on onset, in table order then tone order; parse must give back the pair."""
    table = rules.Q_RHYMES if onset == "qu" else rules.RHYMES
    found: list[str] = []
    for key, units in table.items():
        for written in (key, key[1:]) if onset == "gi" and key[0] == "i" else (key,):
            for tone in range(len(rules.TONES)):
                text = spell(onset, written, units, tone)
                try:
                    parsed = g2p.parse(text)
                except LangError:
                    continue
                if (parsed.onset, parsed.rhyme) == (onset, key) and text not in found:
                    found.append(text)
    return found


def texts(items: list[str | bytes]) -> np.ndarray:
    """Every item as UTF-8 (bytes as they are), each ended by a NUL."""
    raw = b"".join((i if isinstance(i, bytes) else i.encode("utf-8")) + b"\0" for i in items)
    return np.frombuffer(raw, dtype=np.uint8).copy()


def padded(rows: list[list[int]], width: int) -> np.ndarray:
    return np.array([r + [UNIT_PAD] * (width - len(r)) for r in rows], dtype=np.uint8).reshape(len(rows), width)


def g2p_case(items: list[str | bytes], width: int) -> dict[str, np.ndarray]:
    """g2p of each item in each dialect with room for width units: status and units padded with 0xFF."""
    status, units = [], []
    for item in items:
        for dialect in range(len(rules.DIALECTS)):
            try:
                units.append(g2p.g2p(item, dialect, cap=width))
                status.append(ESP_OK)
            except LangError as error:
                units.append([])
                status.append(error.code)
    n, d = len(items), len(rules.DIALECTS)
    return {
        "text": texts(items),
        "status": np.array(status, dtype=np.int16).reshape(n, d),
        "units": padded(units, width).reshape(n, d, width),
    }


def normalize_case(items: list[str | bytes]) -> dict[str, np.ndarray]:
    """lang_vi_normalize of each item into TEXT_MAX_BYTES: status and the NUL-padded output."""
    status, out = [], np.zeros((len(items), TEXT_MAX_BYTES), dtype=np.uint8)
    for row, item in enumerate(items):
        try:
            text = normalize(item, cap=TEXT_MAX_BYTES).encode("utf-8")
            out[row, : len(text)] = np.frombuffer(text, dtype=np.uint8)
            status.append(ESP_OK)
        except LangError as error:
            status.append(error.code)
    return {"text": texts(items), "status": np.array(status, dtype=np.int16), "output": out}


def lexicon_case(lines: list[tuple[str | bytes, int]]) -> dict[str, np.ndarray]:
    """lang_vi_lexicon_entry of each line and mask, laid out as the zeroed lang_vi_pron_t the C side fills."""
    n = len(lines)
    status = np.zeros(n, dtype=np.int16)
    n_variants = np.zeros(n, dtype=np.uint8)
    n_units = np.zeros((n, lexicon.VARIANTS_MAX), dtype=np.uint8)
    units = np.zeros((n, lexicon.VARIANTS_MAX, lexicon.UNITS_MAX), dtype=np.uint8)
    for row, (text, mask) in enumerate(lines):
        try:
            variants = lexicon.entry(text, mask)
        except LangError as error:
            status[row] = error.code
            continue
        n_variants[row] = len(variants)
        for v, ids in enumerate(variants):
            n_units[row, v] = len(ids)
            units[row, v, : len(ids)] = ids
    masks = np.array([mask for _, mask in lines], dtype=np.uint8)
    body = {"status": status, "n_variants": n_variants, "n_units": n_units, "units": units}
    return {"text": texts([t for t, _ in lines]), "mask": masks, **body}


def emit_g2p(root: Path) -> list[Path]:
    """One case per onset (none first) over every valid syllable, the labelled phrases, and a negative control."""
    written = []
    for index, onset in enumerate(["", *rules.ONSETS]):
        path = root / "g2p" / f"case_{index:03d}.gold"
        write_gold(path, g2p_case(valid_syllables(onset), UNITS_PER_SYLLABLE))
        written.append(path)
    path = root / "g2p" / f"case_{len(written):03d}.gold"
    write_gold(path, g2p_case(G2P_PHRASES, lexicon.UNITS_MAX))
    written.append(path)
    negative = g2p_case(valid_syllables("d"), UNITS_PER_SYLLABLE)
    south_onsets = negative["units"][:, SOUTH, 0]
    south_onsets[south_onsets == g2p.UNIT_ID["j"]] = g2p.UNIT_ID["z"]
    path = root / "g2p" / "case_neg_000.gold"
    write_gold(path, negative)
    return [*written, path]


def emit_normalize(root: Path) -> list[Path]:
    """The labelled inputs, and a negative control that reads 105 the southern way."""
    path = root / "normalize" / "case_000.gold"
    write_gold(path, normalize_case(NORMALIZE_INPUTS))
    negative = normalize_case(NORMALIZE_INPUTS)
    row = NORMALIZE_INPUTS.index("105")
    south = normalize("105", SOUTH).encode("utf-8")
    negative["output"][row] = 0
    negative["output"][row, : len(south)] = np.frombuffer(south, dtype=np.uint8)
    neg_path = root / "normalize" / "case_neg_000.gold"
    write_gold(neg_path, negative)
    return [path, neg_path]


def emit_lexicon(root: Path) -> list[Path]:
    """The default command set and labelled lines, and a negative control whose south keeps the ngã of nguyễn."""
    commands = [(c["text"], (1 << len(rules.DIALECTS)) - 1) for c in json.loads(COMMANDS.read_text())["commands"]]
    lines = commands + LEXICON_LINES
    path = root / "lexicon" / "case_000.gold"
    write_gold(path, lexicon_case(lines))
    negative = lexicon_case(lines)
    row = lines.index(("nguyễn", 5))
    negative["units"][row, 1, negative["n_units"][row, 1] - 1] = g2p.UNIT_ID["T3"]
    neg_path = root / "lexicon" / "case_neg_000.gold"
    write_gold(neg_path, negative)
    return [path, neg_path]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=GOLDEN_ROOT)
    args = parser.parse_args()
    for path in emit_g2p(args.out) + emit_normalize(args.out) + emit_lexicon(args.out):
        print(path.relative_to(args.out) if path.is_relative_to(args.out) else path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
