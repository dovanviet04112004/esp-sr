"""The committed golden cases are exactly what srpipe.dsp.emit_golden writes, and the negative control is wrong."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from srpipe.dsp import emit_golden
from srpipe.dsp.afe import balance
from srpipe.dsp.spec import mel, stft
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


def test_committed_mel_cases_match_a_fresh_emit(tmp_path: Path) -> None:
    for path in emit_golden.emit_mel(tmp_path):
        committed = emit_golden.GOLDEN_ROOT / path.relative_to(tmp_path)
        assert committed.read_bytes() == path.read_bytes(), f"{committed} is stale: rerun emit_golden"


def test_the_mel_negative_control_is_one_band_off() -> None:
    case = read_gold(emit_golden.GOLDEN_ROOT / "mel" / "case_neg_000.gold")
    cfg, _ = emit_golden.MEL_CASES[0]
    bank = mel.Mel(cfg)
    right = np.stack([bank.log(b[:, 0] + 1j * b[:, 1]) for b in case["bins"]])
    np.testing.assert_array_equal(case["log_mel"], np.roll(right, -1, axis=1))


def test_committed_chain_cases_match_a_fresh_emit(tmp_path: Path) -> None:
    for path in emit_golden.emit_chain(tmp_path):
        committed = emit_golden.GOLDEN_ROOT / path.relative_to(tmp_path)
        assert committed.read_bytes() == path.read_bytes(), f"{committed} is stale: rerun emit_golden"


def test_the_chain_negative_control_is_one_sample_late() -> None:
    case = read_gold(emit_golden.GOLDEN_ROOT / "chain" / "case_neg_000.gold")
    right = emit_golden.chain_case(
        case["input"][:, 0::2].reshape(-1), case["input"][:, 1::2].reshape(-1), case["reset"]
    )
    got, want = case["pcm"].reshape(-1), right["pcm"].reshape(-1)
    np.testing.assert_array_equal(got[1:], want[:-1])
    assert np.max(np.abs(got.astype(int) - want)) > 1000


def test_committed_hpf_cases_match_a_fresh_emit(tmp_path: Path) -> None:
    for path in emit_golden.emit_hpf(tmp_path):
        committed = emit_golden.GOLDEN_ROOT / path.relative_to(tmp_path)
        assert committed.read_bytes() == path.read_bytes(), f"{committed} is stale: rerun emit_golden"


def test_an_hpf_case_filters_at_the_contract_cutoff_and_removes_dc() -> None:
    case = read_gold(emit_golden.GOLDEN_ROOT / "hpf" / "case_001.gold")
    assert case["cutoff_hz"][0] == emit_golden.afe.HPF_CUTOFF_HZ
    ten_hum_periods = 10 * emit_golden.grid.SAMPLE_RATE_HZ // 50
    tail = case["output"][:, -ten_hum_periods:]
    assert np.all(np.abs(tail.mean(axis=1)) < np.abs(case["input"].mean(axis=1)) / 100)


def test_the_hpf_negative_control_is_one_sample_late() -> None:
    case = read_gold(emit_golden.GOLDEN_ROOT / "hpf" / "case_neg_000.gold")
    right = emit_golden.hpf_case(case["input"])["output"]
    assert np.array_equal(case["output"][:, 1:], right[:, :-1])
    assert not np.allclose(case["output"], right, atol=1e-3)


def test_committed_balance_cases_match_a_fresh_emit(tmp_path: Path) -> None:
    for path in emit_golden.emit_balance(tmp_path):
        committed = emit_golden.GOLDEN_ROOT / path.relative_to(tmp_path)
        assert committed.read_bytes() == path.read_bytes(), f"{committed} is stale: rerun emit_golden"


def test_the_unit_balance_case_leaves_the_bins_unchanged() -> None:
    case = read_gold(emit_golden.GOLDEN_ROOT / "balance" / "case_001.gold")
    assert np.all(case["gains"] == [1.0, 0.0])
    np.testing.assert_array_equal(case["output"], case["bins"])


def test_the_balance_negative_control_takes_the_conjugate_gains() -> None:
    case = read_gold(emit_golden.GOLDEN_ROOT / "balance" / "case_neg_000.gold")
    bins = case["bins"][..., 0] + 1j * case["bins"][..., 1]
    gains = case["gains"][:, 0] + 1j * case["gains"][:, 1]
    got = case["output"][..., 0] + 1j * case["output"][..., 1]
    np.testing.assert_array_equal(got, balance.apply(bins, np.conj(gains)))
    assert np.max(np.abs(got - balance.apply(bins, gains))) > 1.0


def test_committed_vad_cases_match_a_fresh_emit(tmp_path: Path) -> None:
    for path in emit_golden.emit_vad(tmp_path):
        committed = emit_golden.GOLDEN_ROOT / path.relative_to(tmp_path)
        assert committed.read_bytes() == path.read_bytes(), f"{committed} is stale: rerun emit_golden"


def test_the_long_vad_case_outlives_the_minimum_tracker_window() -> None:
    case = read_gold(emit_golden.GOLDEN_ROOT / "vad" / "case_000.gold")
    assert len(case["raw"]) > emit_golden.afe.VAD_MIN_TRACK_WINDOW_HOPS
    assert 0 < case["raw"].sum() < len(case["raw"])


def test_the_vad_negative_control_has_speech_one_hop_late() -> None:
    case = read_gold(emit_golden.GOLDEN_ROOT / "vad" / "case_neg_000.gold")
    right = emit_golden.vad_case(case["pcm"].reshape(-1), int(case["config"][0]))["speech"]
    assert np.array_equal(case["speech"][1:], right[:-1])
    assert not np.array_equal(case["speech"], right)
