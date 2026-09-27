"""A command line to every distinct pronunciation across the dialects asked for (KEHOACH 3.12).

Mirror of firmware/components/lang_vi/src/lexicon.c: each dialect normalises the line its own way, reads it, and a
reading equal to an earlier one is dropped.
"""

from __future__ import annotations

from srpipe.generated import lang_vi as rules
from srpipe.lang.g2p import g2p
from srpipe.lang.normalize import ESP_ERR_INVALID_ARG, TEXT_MAX_BYTES, LangError, header_define, normalize

UNITS_MAX = header_define("LANG_VI_UNITS_MAX")
VARIANTS_MAX = header_define("LANG_VI_VARIANTS_MAX")
ALL_DIALECTS = (1 << len(rules.DIALECTS)) - 1


def entry(text: str | bytes, mask: int) -> list[list[int]]:
    """Unit ids of each distinct reading of text, dialects in the order of their bits in mask.

    Raises LangError: ESP_ERR_INVALID_ARG for an empty mask or line, bad UTF-8 or a syllable spelling cannot build;
    ESP_ERR_INVALID_SIZE for a reading longer than UNITS_MAX units.
    """
    if mask == 0 or mask & ~ALL_DIALECTS:
        raise LangError(ESP_ERR_INVALID_ARG, f"dialect mask {mask:#x}")
    variants: list[list[int]] = []
    for dialect in range(len(rules.DIALECTS)):
        if not mask & (1 << dialect):
            continue
        units = g2p(normalize(text, dialect, cap=TEXT_MAX_BYTES), dialect, cap=UNITS_MAX)
        if not units:
            raise LangError(ESP_ERR_INVALID_ARG, "no syllable")
        if units not in variants:
            variants.append(units)
    return variants
