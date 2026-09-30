"""The committed golden cases are exactly what srpipe.dsp.emit_golden writes, and the negative control is wrong."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

from srpipe.dsp import emit_golden
from srpipe.dsp.afe import balance, chain, doa, gsc, ns_omlsa
from srpipe.dsp.spec import mel, pitch, stft
from srpipe.generated import afe, grid
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
        case["input"][:, 0::2].reshape(-1),
        case["input"][:, 1::2].reshape(-1),
        case["reset"],
        chain.ChainConfig(modules=()),
    )
    got, want = case["pcm"].reshape(-1), right["pcm"].reshape(-1)
    np.testing.assert_array_equal(got[1:], want[:-1])
    assert np.max(np.abs(got.astype(int) - want)) > 1000


def test_committed_chain_modules_cases_match_a_fresh_emit(tmp_path: Path) -> None:
    for path in emit_golden.emit_chain_modules(tmp_path):
        committed = emit_golden.GOLDEN_ROOT / path.relative_to(tmp_path)
        assert committed.read_bytes() == path.read_bytes(), f"{committed} is stale: rerun emit_golden"


def test_chain_modules_cases_carry_the_products_settings_and_calib() -> None:
    case = read_gold(emit_golden.GOLDEN_ROOT / "chain_modules" / "case_000.gold")
    np.testing.assert_array_equal(case["config"], [afe.NS_FLOOR_DB, afe.AGC_TARGET_DBFS, afe.VAD_AGGRESSIVENESS])
    assert case["gains"].shape == (grid.N_BINS, 2)
    assert "gains" not in read_gold(emit_golden.GOLDEN_ROOT / "chain_modules" / "case_001.gold")


def test_the_chain_modules_negative_control_skipped_its_calib() -> None:
    case = read_gold(emit_golden.GOLDEN_ROOT / "chain_modules" / "case_neg_000.gold")
    gains = case["gains"][:, 0] + 1j * case["gains"][:, 1]
    ch0, ch1 = case["input"][:, 0::2].reshape(-1), case["input"][:, 1::2].reshape(-1)
    right = emit_golden.chain_case(ch0, ch1, case["reset"], chain.ChainConfig(balance_gains=gains))
    assert np.max(np.abs(case["pcm"].astype(int) - right["pcm"])) > 100


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


def test_committed_agc_cases_match_a_fresh_emit(tmp_path: Path) -> None:
    for path in emit_golden.emit_agc(tmp_path):
        committed = emit_golden.GOLDEN_ROOT / path.relative_to(tmp_path)
        assert committed.read_bytes() == path.read_bytes(), f"{committed} is stale: rerun emit_golden"


def test_the_quiet_agc_case_climbs_then_freezes() -> None:
    case = read_gold(emit_golden.GOLDEN_ROOT / "agc" / "case_000.gold")
    gain, speech = case["gain_db"], case["speech"].astype(bool)
    assert np.all(np.diff(gain)[speech[1:]] > 0)
    assert np.all(np.diff(gain)[~speech[1:]] == 0)


def test_the_agc_negative_control_never_moves_its_gain() -> None:
    case = read_gold(emit_golden.GOLDEN_ROOT / "agc" / "case_neg_000.gold")
    right = emit_golden.agc_case(case["pcm"].reshape(-1), case["speech"], float(case["config"][0]))
    assert np.all(case["gain_db"] == 0.0) and np.any(right["gain_db"] != 0.0)


def test_committed_ns_omlsa_cases_match_a_fresh_emit(tmp_path: Path) -> None:
    for path in emit_golden.emit_ns_omlsa(tmp_path):
        committed = emit_golden.GOLDEN_ROOT / path.relative_to(tmp_path)
        assert committed.read_bytes() == path.read_bytes(), f"{committed} is stale: rerun emit_golden"


def test_the_ns_omlsa_negative_control_left_the_smoothing_unsquared() -> None:
    case = read_gold(emit_golden.GOLDEN_ROOT / "ns_omlsa" / "case_neg_000.gold")
    right = emit_golden.ns_omlsa_case(case["power"], float(case["config"][0]))
    wrong = emit_golden.ns_omlsa_case(case["power"], float(case["config"][0]), cfg=ns_omlsa.OmlsaConfig(hop_s=0.008))
    np.testing.assert_array_equal(case["gain"], wrong["gain"])
    assert np.max(np.abs(case["gain"] - right["gain"])) > 0.1


def test_every_ns_omlsa_case_fits_the_parity_read_buffer() -> None:
    for path in (emit_golden.GOLDEN_ROOT / "ns_omlsa").glob("*.gold"):
        assert path.stat().st_size <= 512 * 1024


def test_committed_doa_cases_match_a_fresh_emit(tmp_path: Path) -> None:
    for path in emit_golden.emit_doa(tmp_path):
        committed = emit_golden.GOLDEN_ROOT / path.relative_to(tmp_path)
        assert committed.read_bytes() == path.read_bytes(), f"{committed} is stale: rerun emit_golden"


def test_the_doa_negative_control_is_one_grid_step_off() -> None:
    case = read_gold(emit_golden.GOLDEN_ROOT / "doa" / "case_neg_000.gold")
    searcher = doa.Doa(doa.DoaConfig(*[float(v) for v in case["config"]]))
    bins = [case[f"bins{m}"][..., 0] + 1j * case[f"bins{m}"][..., 1] for m in range(2)]
    right = [searcher.process(bins[0][h], bins[1][h], bool(u)).angle_deg for h, u in enumerate(case["update"])]
    known = np.array(right) != doa.ANGLE_UNKNOWN_DEG
    assert known.any()
    np.testing.assert_array_equal(case["angle"][known] - np.array(right)[known], round(afe.DOA_GRID_STEP_DEG))


def test_committed_gsc_cases_match_a_fresh_emit(tmp_path: Path) -> None:
    for path in emit_golden.emit_gsc(tmp_path):
        committed = emit_golden.GOLDEN_ROOT / path.relative_to(tmp_path)
        assert committed.read_bytes() == path.read_bytes(), f"{committed} is stale: rerun emit_golden"


def test_the_gsc_negative_control_comes_from_weights_that_never_learn() -> None:
    case = read_gold(emit_golden.GOLDEN_ROOT / "gsc" / "case_neg_000.gold")
    cfg = gsc.GscConfig(*[float(v) for v in case["config"]])
    bins = [case[f"bins{m}"][..., 0] + 1j * case[f"bins{m}"][..., 1] for m in range(2)]
    hops = range(len(case["adapt"]))

    def output(learn: bool) -> np.ndarray:
        canceller = gsc.Gsc(cfg)
        adapt = case["adapt"].astype(bool) & learn
        y = np.stack([canceller.process(bins[0][h], bins[1][h], float(case["angle"][h]), bool(adapt[h])) for h in hops])
        return np.stack([y.real, y.imag], axis=-1)

    np.testing.assert_array_equal(case["out"], output(learn=False))
    assert np.abs(case["out"] - output(learn=True)).max() > 1.0


def test_committed_pitch_cases_match_a_fresh_emit(tmp_path: Path) -> None:
    for path in emit_golden.emit_pitch(tmp_path, emit_golden.pitch_config()):
        committed = emit_golden.GOLDEN_ROOT / path.relative_to(tmp_path)
        assert committed.read_bytes() == path.read_bytes(), committed


def test_the_pitch_negative_control_is_one_hop_late() -> None:
    case = read_gold(emit_golden.GOLDEN_ROOT / "pitch" / "case_neg_000.gold")
    right = read_gold(emit_golden.GOLDEN_ROOT / "pitch" / "case_000.gold")
    np.testing.assert_array_equal(case["features"][1:], right["features"][:-1])
    assert not np.array_equal(case["features"], right["features"])


def test_the_pitch_reset_case_starts_over_after_its_reset() -> None:
    case = read_gold(emit_golden.GOLDEN_ROOT / "pitch" / "case_003.gold")
    at = int(np.flatnonzero(case["reset"])[0])
    assert np.all(case["features"][at : at + pitch.LEAD_HOPS] == 0.0)
    assert np.any(case["features"][at + pitch.LEAD_HOPS] != 0.0)


def test_every_pitch_case_fits_the_parity_read_buffer() -> None:
    for path in (emit_golden.GOLDEN_ROOT / "pitch").glob("*.gold"):
        assert path.stat().st_size <= 512 * 1024


def test_the_pitch_tolerance_rejects_the_negative_control() -> None:
    limits = yaml.safe_load((emit_golden.GOLDEN_ROOT / "pitch" / "tolerance.yaml").read_text())["tensors"]["features"]
    wrong = read_gold(emit_golden.GOLDEN_ROOT / "pitch" / "case_neg_000.gold")["features"].astype(np.float64)
    right = read_gold(emit_golden.GOLDEN_ROOT / "pitch" / "case_000.gold")["features"].astype(np.float64)
    snr_db = 10.0 * np.log10(np.sum(right**2) / np.sum((wrong - right) ** 2))
    assert np.max(np.abs(wrong - right)) > limits["max_abs"] and snr_db < limits["min_snr_db"]
