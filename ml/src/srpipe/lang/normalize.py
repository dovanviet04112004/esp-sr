"""Text to what lang_vi reads: composed letters, lower case, numbers read out, abbreviations and loanwords expanded.

Mirror of firmware/components/lang_vi/src/normalize.c (KEHOACH 3.12): the tables of srpipe.generated.lang_vi and the
same steps, so both sides give the same bytes. Limits come from the frozen header lang_vi.h.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from srpipe.generated import lang_vi as rules

HEADER = Path(__file__).resolve().parents[4] / "firmware" / "components" / "lang_vi" / "include" / "lang_vi.h"
ESP_OK = 0
ESP_ERR_INVALID_ARG = 0x102
ESP_ERR_INVALID_SIZE = 0x104
SPACES = frozenset(" \t\n\v\f\r")
COMBINING_MARKS = range(0x0300, 0x0370)
NUMBER_JOINERS = frozenset(".,")
THOUSANDS = re.compile(r"[0-9]{1,3}(\.[0-9]{3})+")
TONE_OF_MARK = {mark: tone for tone, mark in enumerate(rules.TONE_MARKS) if mark}
NUMBERS = rules.NUMBERS


def header_define(name: str) -> int:
    """The integer a #define of lang_vi.h gives name."""
    text = HEADER.read_text(encoding="utf-8")
    return int(re.search(rf"^#define {name} (\d+)$", text, flags=re.MULTILINE).group(1))


TEXT_MAX_BYTES = header_define("LANG_VI_TEXT_MAX_BYTES")


class LangError(Exception):
    """A refusal of lang_vi, carrying the esp_err_t the C side returns."""

    def __init__(self, code: int, what: str) -> None:
        super().__init__(what)
        self.code = code


@dataclass
class Letter:
    """A letter as its lower-case base and tone index; a consonant keeps tone 0."""

    base: str
    tone: int

    def text(self) -> str:
        return rules.VOWELS[self.base][self.tone] if self.base in rules.VOWELS else self.base


Item = Letter | str


def decoded(text: str | bytes) -> str:
    """text as code points; bytes must be well-formed UTF-8 and a str must encode to it."""
    try:
        return text.decode("utf-8") if isinstance(text, bytes) else text.encode("utf-8").decode("utf-8")
    except UnicodeError as error:
        raise LangError(ESP_ERR_INVALID_ARG, "not UTF-8") from error


def compose(text: str) -> list[Item]:
    """Letters from precomposed code points or from combining marks in any order, lower case; others as they are."""
    items: list[Item] = []
    for ch in text:
        last = items[-1] if items and isinstance(items[-1], Letter) else None
        mark = rules.TONE_MARK_ALIASES.get(ch, ch)
        if ch in rules.LETTERS:
            items.append(Letter(*rules.LETTERS[ch]))
        elif ch.isascii() and ch.isalpha():
            items.append(Letter(ch.lower(), 0))
        elif mark in TONE_OF_MARK and last is not None and last.base in rules.VOWELS and last.tone == 0:
            last.tone = TONE_OF_MARK[mark]
        elif ch in rules.MODIFIERS and last is not None and last.base in rules.MODIFIERS[ch]:
            last.base = rules.MODIFIERS[ch][last.base]
        else:
            items.append(ch)
    return items


def kind(item: Item) -> str:
    if isinstance(item, Letter) or ord(item) in COMBINING_MARKS:
        return "letter"
    if "0" <= item <= "9":
        return "digit"
    if item in SPACES:
        return "space"
    return "symbol" if item in rules.SYMBOLS else "other"


def render(items: list[Item]) -> str:
    return "".join(item.text() if isinstance(item, Letter) else item for item in items)


def number_words(value: int, dialect: int) -> list[str]:
    """value read out: groups of three digits, zero groups skipped, every group after the first in full."""
    if value == 0:
        return [NUMBERS["digits"][0]]
    names = ["", NUMBERS["thousand"][dialect], NUMBERS["million"], NUMBERS["billion"]]
    groups = [(value // 1000**i) % 1000 for i in range(len(names))]
    words: list[str] = []
    for i in reversed(range(len(names))):
        if groups[i] == 0:
            continue
        words += hundreds(groups[i], full=bool(words), dialect=dialect)
        words += [names[i]] if names[i] else []
    return words


def unit_after_tens(tens: int, unit: int) -> str:
    if unit == 5:
        return NUMBERS["five_after_ten"]
    if tens > 1 and unit == 1:
        return NUMBERS["one_after_tens"]
    if tens > 1 and unit == 4:
        return NUMBERS["four_after_tens"]
    return NUMBERS["digits"][unit]


def hundreds(group: int, full: bool, dialect: int) -> list[str]:
    """One group of three digits; full says "không trăm" and "linh" where a leading group would not."""
    digits = NUMBERS["digits"]
    h, t, u = group // 100, group // 10 % 10, group % 10
    words = [digits[h], NUMBERS["hundred"]] if full or h > 0 else []
    if t == 0:
        linh = [NUMBERS["zero_tens"][dialect]] if words else []
        return [*words, *linh, digits[u]] if u > 0 else words
    words += [NUMBERS["ten"]] if t == 1 else [digits[t], NUMBERS["tens"]]
    return [*words, unit_after_tens(t, u)] if u > 0 else words


def integer_words(digits_text: str, dialect: int) -> list[str]:
    """A run of digits: a number up to max_digits without a leading 0, else digit by digit."""
    if len(digits_text) > NUMBERS["max_digits"] or (len(digits_text) > 1 and digits_text[0] == "0"):
        return [NUMBERS["digits"][int(d)] for d in digits_text]
    return number_words(int(digits_text), dialect)


def number_part_words(text: str, dialect: int, previous: str | None) -> list[str]:
    """Digits with . and , between them: thousands groups, else each piece read out with chấm or phẩy."""
    if THOUSANDS.fullmatch(text):
        return integer_words(text.replace(".", ""), dialect)
    ordinal_value = int(text) if text.isdigit() and len(text) <= NUMBERS["max_digits"] and text[0] != "0" else None
    if previous == NUMBERS["ordinal_word"] and ordinal_value in NUMBERS["ordinals"]:
        return [NUMBERS["ordinals"][ordinal_value]]
    words: list[str] = []
    for piece in re.split(r"([.,])", text):
        joiner = {",": NUMBERS["decimal_comma"], ".": NUMBERS["dot"]}.get(piece)
        words += [joiner] if joiner else integer_words(piece, dialect)
    return words


def runs(chunk: list[Item]) -> list[list[Item]]:
    """Letter and digit runs of a chunk, with . and , kept between two digits; a symbol is a run of its own."""
    found: list[list[Item]] = []
    i = 0
    while i < len(chunk):
        if kind(chunk[i]) == "symbol":
            found.append([chunk[i]])
            i += 1
            continue
        if kind(chunk[i]) not in ("letter", "digit"):
            i += 1
            continue
        j = i
        while j < len(chunk) and (
            kind(chunk[j]) in ("letter", "digit")
            or (
                chunk[j] in NUMBER_JOINERS
                and kind(chunk[j - 1]) == "digit"
                and j + 1 < len(chunk)
                and kind(chunk[j + 1]) == "digit"
            )
        ):
            j += 1
        found.append(chunk[i:j])
        i = j
    return found


def parts(run: list[Item]) -> list[list[Item]]:
    """A run cut where letters meet digits; . and , always sit inside the digits."""
    cut: list[list[Item]] = []
    for item in run:
        is_letter = kind(item) == "letter"
        if cut and (kind(cut[-1][0]) == "letter") == is_letter:
            cut[-1].append(item)
        else:
            cut.append([item])
    return cut


def read_run(run: list[Item], dialect: int, words: list[str]) -> None:
    key = render(run)
    if len(run) == 1 and kind(run[0]) == "symbol":
        words += rules.SYMBOLS[key].split(" ")
        return
    if key in rules.DICTIONARY:
        words += rules.DICTIONARY[key].split(" ")
        return
    for part in parts(run):
        text = render(part)
        if kind(part[0]) == "letter":
            words += rules.DICTIONARY.get(text, text).split(" ")
        else:
            words += number_part_words(text, dialect, words[-1] if words else None)


def read_chunk(chunk: list[Item], dialect: int, words: list[str]) -> None:
    lo, hi = 0, len(chunk)
    while lo < hi and kind(chunk[lo]) == "other":
        lo += 1
    while hi > lo and kind(chunk[hi - 1]) == "other":
        hi -= 1
    key = render(chunk[lo:hi])
    if key in rules.DICTIONARY:
        words += rules.DICTIONARY[key].split(" ")
        return
    for run in runs(chunk[lo:hi]):
        read_run(run, dialect, words)


def normalize(text: str | bytes, dialect: int = 0, cap: int | None = None) -> str:
    """text in lower-case composed letters, one space between words, numbers read as dialect reads them.

    Raises LangError: ESP_ERR_INVALID_ARG for text that is not UTF-8, ESP_ERR_INVALID_SIZE past TEXT_MAX_BYTES
    letters and marks, or when the result with its NUL does not fit cap bytes.
    """
    items = compose(decoded(text))
    if len(items) > TEXT_MAX_BYTES:
        raise LangError(ESP_ERR_INVALID_SIZE, f"more than {TEXT_MAX_BYTES} characters")
    words: list[str] = []
    chunk: list[Item] = []
    for item in [*items, " "]:
        if kind(item) != "space":
            chunk.append(item)
        elif chunk:
            read_chunk(chunk, dialect, words)
            chunk = []
    out = " ".join(words)
    if cap is not None and len(out.encode("utf-8")) + 1 > cap:
        raise LangError(ESP_ERR_INVALID_SIZE, f"{out!r} does not fit {cap} bytes")
    return out
