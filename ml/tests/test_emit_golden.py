"""The committed golden cases are exactly what srpipe.dsp.emit_golden writes, and the negative control is wrong."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from srpipe.dsp import emit_golden
from srpipe.dsp.spec import stft
from srpipe.golden.gold import read_gold


def test_committed_stft_cases_match_a_fresh_emit(tmp_path: Path) -> None:
    written = emit_golden.emit_stft(tmp_path)
    assert written
    for path in written:
        committed = emit_golden.GOLDEN_ROOT / path.relative_to(tmp_path)
        assert committed.read_bytes() == path.read_bytes(), f"{committed} is stale: rerun emit_golden"


def test_a_positive_case_is_the_reference_itself() -> None:
    case = read_gold(emit_golden.GOLDEN_ROOT / "stft" / "case_000.gold")
    spectra = stft.analyze_signal(case["signal"])
    np.testing.assert_array_equal(case["bins"][..., 0], spectra.real)
    np.testing.assert_array_equal(case["rebuilt"], stft.synthesize_signal(spectra))


def test_the_negative_control_is_one_sample_late() -> None:
    case = read_gold(emit_golden.GOLDEN_ROOT / "stft" / "case_neg_000.gold")
    spectra = case["bins"][..., 0] + 1j * case["bins"][..., 1]
    right = stft.synthesize_signal(spectra)
    np.testing.assert_array_equal(case["rebuilt"][1:], right[:-1])
    assert np.max(np.abs(case["rebuilt"] - right)) > 0.1
