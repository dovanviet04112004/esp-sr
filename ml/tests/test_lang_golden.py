"""The committed lang_vi golden cases are what srpipe.lang.emit_golden writes, over every valid syllable."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from srpipe.generated import lang_vi as rules
from srpipe.golden.gold import read_gold
from srpipe.lang import emit_golden

HARD_WORDS = ["gì", "gìn", "giếng", "giữa", "quốc", "quyển", "hoặc", "huỳnh", "khuya", "khuỷu", "khoẻ", "thuở", "xoong",
              "nghiêng", "ghế", "kiến", "yêu", "ỉa", "ạ"]  # fmt: skip


def test_every_valid_syllable_round_trips_and_the_labelled_words_are_among_them() -> None:
    every = {s for onset in ["", *rules.ONSETS] for s in emit_golden.valid_syllables(onset)}
    assert len(every) > 16000
    assert set(HARD_WORDS) <= every


def test_committed_lang_cases_match_a_fresh_emit(tmp_path: Path) -> None:
    written = emit_golden.emit_g2p(tmp_path) + emit_golden.emit_normalize(tmp_path) + emit_golden.emit_lexicon(tmp_path)
    for path in written:
        committed = emit_golden.GOLDEN_ROOT / path.relative_to(tmp_path)
        assert committed.read_bytes() == path.read_bytes(), f"{committed} is stale: rerun srpipe.lang.emit_golden"


def test_each_negative_control_differs_from_its_positive_case() -> None:
    root = emit_golden.GOLDEN_ROOT
    d_case = 1 + list(rules.ONSETS).index("d")
    pairs = {
        "g2p": (f"case_{d_case:03d}", "units"),
        "normalize": ("case_000", "output"),
        "lexicon": ("case_000", "units"),
    }
    for block, (positive, tensor) in pairs.items():
        right = read_gold(root / block / f"{positive}.gold")[tensor]
        wrong = read_gold(root / block / "case_neg_000.gold")[tensor]
        assert right.shape == wrong.shape and np.count_nonzero(right != wrong) > 0, block
