"""One normalised utterance to units in one dialect: each syllable parsed by Vietnamese spelling, read by region.

Mirror of firmware/components/lang_vi/src/g2p.c (KEHOACH 3.12). parse gives the written structure, read the units a
dialect speaks; Syllable keeps onset, glide, nucleus, coda and tone apart so E11-T3 can build other unit sets.
"""

from __future__ import annotations

import string
from dataclasses import dataclass

from srpipe.generated import lang_vi as rules
from srpipe.lang.normalize import ESP_ERR_INVALID_ARG, ESP_ERR_INVALID_SIZE, LangError, decoded

UNIT_ID = {name: i for i, name in enumerate(rules.UNITS)}
LOWER_LETTERS = {form: (base, tone) for base, forms in rules.VOWELS.items() for tone, form in enumerate(forms)}
LOWER_LETTERS |= {letter: (letter, 0) for letter in rules.EXTRA_LETTERS}
LOWER_LETTERS |= {letter: (letter, 0) for letter in string.ascii_lowercase if letter not in LOWER_LETTERS}


@dataclass(frozen=True)
class Spelling:
    """A syllable as written: its onset spelling, the key of its rhyme in RHYMES or Q_RHYMES, its tone index."""

    onset: str
    rhyme: str
    tone: int

    def rhyme_units(self) -> list[str]:
        return (rules.Q_RHYMES if self.onset == "qu" else rules.RHYMES)[self.rhyme]


@dataclass(frozen=True)
class Syllable:
    """Unit names of one syllable as a dialect speaks it; "" where it has no onset, glide or coda."""

    onset: str
    glide: str
    nucleus: str
    coda: str
    tone: str

    def units(self) -> list[str]:
        return [u for u in (self.onset, self.glide, self.nucleus, self.coda, self.tone) if u]


def refuse(syllable: str, why: str) -> LangError:
    return LangError(ESP_ERR_INVALID_ARG, f"{syllable!r}: {why}")


def split_tone(syllable: str) -> tuple[str, int]:
    """The base letters of a lower-case syllable and its one tone, wherever the mark sits."""
    base, tone = "", 0
    for ch in syllable:
        if ch not in LOWER_LETTERS:
            raise refuse(syllable, f"{ch!r} is not a lower-case Vietnamese letter")
        letter, letter_tone = LOWER_LETTERS[ch]
        if letter_tone and tone:
            raise refuse(syllable, "two tone marks")
        base, tone = base + letter, tone or letter_tone
    return base, tone


def rhyme_key(syllable: str, onset: str, rest: str) -> str:
    """The table key of the written rhyme: after gi, the i gi shares with the rhyme comes back."""
    if onset != "gi":
        return rest
    if rest[:1] in rules.GI_NEVER_BEFORE:
        raise refuse(syllable, "gi before i or y")
    shares = not rest or rest[0] not in rules.VOWELS or rest[0] in rules.GI_SHARES_BEFORE
    return "i" + rest if shares else rest


def parse(syllable: str) -> Spelling:
    """syllable split by Vietnamese spelling, refused with ESP_ERR_INVALID_ARG when the spelling cannot build it."""
    base, tone = split_tone(syllable)
    onset = max((o for o in rules.ONSETS if base.startswith(o)), key=len, default="")
    rest = base[len(onset) :]
    key = rhyme_key(syllable, onset, rest)
    table = rules.Q_RHYMES if onset == "qu" else rules.RHYMES
    if key not in table:
        raise refuse(syllable, f"no rhyme {key!r} after {onset!r}")
    glide, _, coda = table[key]
    first = rest[:1]
    checks = {
        "a glide after an onset that takes none": glide and onset in rules.NO_GLIDE,
        f"{onset} before {first!r}": onset in rules.FRONT_ONLY and first not in rules.FRONT_ONLY[onset],
        f"{onset} spelled before {first!r}": onset in rules.NEVER_FRONT and first in rules.NEVER_FRONT[onset],
        "yê with an onset": table is rules.RHYMES and key in rules.ZERO_ONSET_ONLY and onset,
        "iê without an onset": table is rules.RHYMES and key in rules.ONSET_ONLY and not onset,
        "a stop coda without sắc or nặng": coda in rules.CHECKED_CODAS and rules.TONES[tone] not in rules.CHECKED_TONES,
    }
    for why, broken in checks.items():
        if broken:
            raise refuse(syllable, why)
    return Spelling(onset, key, tone)


def read(spelling: Spelling, dialect: int) -> Syllable:
    """The units dialect speaks for spelling, by the rules of its region in their fixed order."""
    region = rules.DIALECT_RULES[rules.DIALECTS[dialect]]
    onset = rules.ONSETS[spelling.onset][dialect] if spelling.onset else ""
    glide, nucleus, coda = spelling.rhyme_units()
    tone = rules.TONES[spelling.tone]
    spoken_nucleus, spoken_coda = nucleus, coda
    if glide and onset in region["glide_drops_onset"]:
        onset = ""
    if coda in region["velar_codas"] and nucleus not in region["velar_keep_after"]:
        spoken_coda = region["velar_codas"][coda]
    if coda in region["palatal_codas"]:
        spoken_coda = region["palatal_codas"][coda]
    if coda in region["centralize_before"] and nucleus in region["centralize"]:
        spoken_nucleus = region["centralize"][nucleus]
    return Syllable(onset, glide, spoken_nucleus, spoken_coda, region["tone"].get(tone, tone))


def check_dialect(dialect: int) -> None:
    if not 0 <= dialect < len(rules.DIALECTS):
        raise LangError(ESP_ERR_INVALID_ARG, f"dialect {dialect}")


def syllables(normalized: str | bytes, dialect: int) -> list[Syllable]:
    """Every syllable of a normalised utterance as dialect speaks it; syllables are split by ASCII spaces."""
    check_dialect(dialect)
    return [read(parse(token), dialect) for token in decoded(normalized).split(" ") if token]


def g2p(normalized: str | bytes, dialect: int, cap: int | None = None) -> list[int]:
    """Unit ids of a normalised utterance in one dialect; ESP_ERR_INVALID_SIZE when more than cap."""
    check_dialect(dialect)
    units: list[int] = []
    for token in decoded(normalized).split(" "):
        if not token:
            continue
        units += [UNIT_ID[u] for u in read(parse(token), dialect).units()]
        if cap is not None and len(units) > cap:
            raise LangError(ESP_ERR_INVALID_SIZE, f"more than {cap} units")
    return units
