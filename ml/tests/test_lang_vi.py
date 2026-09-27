"""srpipe.lang against hand-labelled readings (KEHOACH 3.12)."""

from __future__ import annotations

import unicodedata

import pytest

from srpipe.generated import lang_vi as rules
from srpipe.lang import g2p, lexicon
from srpipe.lang.normalize import ESP_ERR_INVALID_ARG, ESP_ERR_INVALID_SIZE, TEXT_MAX_BYTES, LangError, normalize

NORTH, CENTRAL, SOUTH = range(3)
SAME = object()
# Units in the north, the centre and the south; SAME repeats the north.
READINGS = {
    "bật": ("b_< @ t T6", "b_< @ k T6", "b_< @ k T6"),
    "đèn": ("d_< E n T2", "d_< E N T2", "d_< E N T2"),
    "gì": ("z i T2", "j i T2", "j i T2"),
    "gìn": ("z i n T2", "j i n T2", "j M n T2"),
    "giếng": ("z i@ N T5", "j i@ N T5", "j i@ N T5"),
    "giữa": ("z M@ T3", "j M@ T4", "j M@ T4"),
    "quốc": ("k u@ k T5", SAME, SAME),
    "cuốc": ("k u@ k T5", SAME, SAME),
    "quá": ("k w a: T5", "k w a: T5", "w a: T5"),
    "quyển": ("k w i@ n T4", "k w i@ N T4", "w i@ N T4"),
    "hoa": ("h w a: T1", "h w a: T1", "w a: T1"),
    "hoặc": ("h w a k T6", "h w a k T6", "w a k T6"),
    "huỳnh": ("h w i J T2", "h w i J T2", "w M n T2"),
    "khuya": ("x w i@ T1", SAME, SAME),
    "khuỷu": ("x w i w T4", SAME, SAME),
    "khoẻ": ("x w E T4", SAME, SAME),
    "khỏe": ("x w E T4", SAME, SAME),
    "thuở": ("t_h w @: T4", SAME, SAME),
    "anh": ("a J T1", "a J T1", "a n T1"),
    "ăn": ("a n T1", "a N T1", "a N T1"),
    "an": ("a: n T1", "a: N T1", "a: N T1"),
    "ai": ("a: j T1", SAME, SAME),
    "ay": ("a j T1", SAME, SAME),
    "ao": ("a: w T1", SAME, SAME),
    "au": ("a w T1", SAME, SAME),
    "tin": ("t i n T1", "t i n T1", "t M n T1"),
    "tính": ("t i J T5", "t i J T5", "t M n T5"),
    "xin": ("s i n T1", "s i n T1", "s M n T1"),
    "bệnh": ("b_< e J T6", "b_< e J T6", "b_< @ n T6"),
    "kiến": ("k i@ n T5", "k i@ N T5", "k i@ N T5"),
    "nguyễn": ("N w i@ n T3", "N w i@ N T4", "N w i@ N T4"),
    "chuyện": ("c w i@ n T6", "c w i@ N T6", "c w i@ N T6"),
    "trường": ("c M@ N T2", "t` M@ N T2", "t` M@ N T2"),
    "rồi": ("z o j T2", "z` o j T2", "z` o j T2"),
    "vui": ("v u j T1", "v u j T1", "j u j T1"),
    "sáng": ("s a: N T5", "s` a: N T5", "s` a: N T5"),
    "đường": ("d_< M@ N T2", SAME, SAME),
    "nghiêng": ("N i@ N T1", SAME, SAME),
    "ghế": ("G e T5", SAME, SAME),
    "phở": ("f @: T4", SAME, SAME),
    "xoong": ("s O N T1", SAME, SAME),
    "yêu": ("i@ w T1", SAME, SAME),
    "ỉa": ("i@ T4", SAME, SAME),
    "ạ": ("a: T6", SAME, SAME),
}
REFUSED = ["ci", "ka", "gé", "ngé", "gha", "ngha", "qa", "cuy", "coa", "bl", "tàc", "mãp", "áà", "giy", "iêu", "tyên",
           "Bật", "5", "fan", "b́"]  # fmt: skip
NORTH_TEXT = {
    "Bật Đèn": "bật đèn",
    "BẬT ĐÈN!": "bật đèn",
    unicodedata.normalize("NFD", "giảm âm lượng"): "giảm âm lượng",
    "ặ ợ é̂": "ặ ợ ế",
    "Wi-Fi": "oai phai",
    "TV.": "ti vi",
    "TP. HCM": "thành phố hồ chí minh",
    "7h30": "bảy giờ ba mươi",
    "100đ": "một trăm đồng",
    "50%": "năm mươi phần trăm",
    "A&B": "a và b",
    "1+1": "một cộng một",
    "0": "không",
    "15": "mười lăm",
    "21": "hai mươi mốt",
    "24": "hai mươi tư",
    "25": "hai mươi lăm",
    "105": "một trăm linh năm",
    "110": "một trăm mười",
    "1005": "một nghìn không trăm linh năm",
    "2024": "hai nghìn không trăm hai mươi tư",
    "12345": "mười hai nghìn ba trăm bốn mươi lăm",
    "1.000.000": "một triệu",
    "1000000000": "một tỷ",
    "1000005": "một triệu không trăm linh năm",
    "1000000000000": "một" + " không" * 12,
    "007": "không không bảy",
    "2,5": "hai phẩy năm",
    "3,14": "ba phẩy mười bốn",
    "2,05": "hai phẩy không năm",
    "1.5": "một chấm năm",
    "thứ 1": "thứ nhất",
    "thứ 4": "thứ tư",
    "thứ 2": "thứ hai",
    "số 4": "số bốn",
    "\tbật\nđèn\r": "bật đèn",
    "đèn 💡 sáng": "đèn sáng",
    "": "",
    "...": "",
}


def units(text: str, dialect: int) -> str:
    return " ".join(rules.UNITS[i] for i in g2p.g2p(text, dialect))


@pytest.mark.parametrize("word", list(READINGS))
def test_each_dialect_reads_the_labelled_units(word: str) -> None:
    north = READINGS[word][NORTH]
    want = [north if r is SAME else r for r in READINGS[word]]
    assert [units(word, d) for d in range(len(rules.DIALECTS))] == want


@pytest.mark.parametrize("syllable", REFUSED)
def test_a_syllable_spelling_cannot_build_is_refused(syllable: str) -> None:
    with pytest.raises(LangError) as refused:
        g2p.g2p(syllable, NORTH)
    assert refused.value.code == ESP_ERR_INVALID_ARG


def test_a_syllable_keeps_its_parts_apart() -> None:
    assert g2p.syllables("khuyên", SOUTH) == [g2p.Syllable("x", "w", "i@", "N", "T1")]


def test_g2p_refuses_more_units_than_cap() -> None:
    with pytest.raises(LangError) as refused:
        g2p.g2p("nguyễn văn an", NORTH, cap=10)
    assert refused.value.code == ESP_ERR_INVALID_SIZE


@pytest.mark.parametrize("text", list(NORTH_TEXT))
def test_normalize_reads_the_labelled_text(text: str) -> None:
    assert normalize(text) == NORTH_TEXT[text]


def test_the_south_reads_numbers_its_own_way() -> None:
    assert normalize("105", SOUTH) == "một trăm lẻ năm"
    assert normalize("1000", SOUTH) == "một ngàn"
    assert normalize("105", CENTRAL) == "một trăm lẻ năm"


def test_normalize_refuses_bad_utf8_and_what_does_not_fit() -> None:
    for bad in (b"\xc3", b"\xc0\xaf", b"\xed\xa0\x80", b"\xf4\x90\x80\x80", b"\xff"):
        with pytest.raises(LangError) as refused:
            normalize(bad)
        assert refused.value.code == ESP_ERR_INVALID_ARG
    for long_text, cap in (("a" * (TEXT_MAX_BYTES + 1), None), ("1234567890 " * 5, TEXT_MAX_BYTES)):
        with pytest.raises(LangError) as refused:
            normalize(long_text, cap=cap)
        assert refused.value.code == ESP_ERR_INVALID_SIZE


def test_every_word_normalize_can_write_is_valid_in_every_dialect() -> None:
    words = [*rules.DICTIONARY.values(), *rules.SYMBOLS.values(), *rules.NUMBERS["digits"]]
    words += [v for k, v in rules.NUMBERS.items() if isinstance(v, str)]
    words += [w for k in ("thousand", "zero_tens") for w in rules.NUMBERS[k]] + list(rules.NUMBERS["ordinals"].values())
    for dialect in range(len(rules.DIALECTS)):
        for word in words:
            assert g2p.g2p(word, dialect), word


def test_lexicon_merges_equal_readings_and_keeps_dialect_order() -> None:
    assert [len(lexicon.entry(t, 7)) for t in ("bật đèn", "nguyễn", "gìn", "phở")] == [2, 2, 3, 1]
    north, south = lexicon.entry("bật đèn", 1 | 4)
    assert (north, south) == (g2p.g2p("bật đèn", NORTH), g2p.g2p("bật đèn", SOUTH))


def test_lexicon_refuses_empty_masks_lines_and_long_readings() -> None:
    for text, mask, code in (
        ("bật đèn", 0, ESP_ERR_INVALID_ARG),
        ("bật đèn", 8, ESP_ERR_INVALID_ARG),
        ("!!!", 7, ESP_ERR_INVALID_ARG),
        ("bật fan", 7, ESP_ERR_INVALID_ARG),
        (" ".join(["nguyễn"] * 10), 7, ESP_ERR_INVALID_SIZE),
    ):
        with pytest.raises(LangError) as refused:
            lexicon.entry(text, mask)
        assert refused.value.code == code, text
