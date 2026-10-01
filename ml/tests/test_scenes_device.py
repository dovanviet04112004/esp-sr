"""srpipe.scenes.device: levels in dB SPL that distance and sensitivity turn into dBFS, ch0 hearing ch1 through
calib/bal, the datasheet self noise or the board's captured floor, a talker's spectral tilt, drv_audio's shift, the
same shards for the same seed, pitch and wider pads that leave every other file alone; the playback file."""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from srpipe.core import screen
from srpipe.core.audio_io import INT16_SCALE, to_float, write_wav
from srpipe.core.config import CONFIGS, load_yaml
from srpipe.dsp.spec.pitch import PitchConfig, pitch_features
from srpipe.generated import array, grid
from srpipe.metrics import mic_pair
from srpipe.scenes import device, room

FS = grid.SAMPLE_RATE_HZ


def fake_vivos(root: Path) -> Path:
    rng = np.random.default_rng(3)
    lines = []
    for spk in ("SPK02", "SPK01"):
        for n in (2, 1):
            utterance = f"{spk}_R00{n}"
            write_wav(root / "waves" / spk / f"{utterance}.wav", 0.01 * n * rng.standard_normal(grid.SAMPLE_RATE_HZ))
            lines.append(f"{utterance} CÂU {n}")
    (root / "prompts.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def test_playback_takes_speakers_in_turn_at_one_level(tmp_path: Path) -> None:
    signal, items = device.playback(fake_vivos(tmp_path), seconds=10.0)
    assert [i["utterance"] for i in items] == ["SPK01_R001", "SPK02_R001", "SPK01_R002", "SPK02_R002"]
    assert items[0]["text"] == "CÂU 1"
    for item in items:
        start = round(item["start_s"] * grid.SAMPLE_RATE_HZ)
        x = signal[start : start + grid.SAMPLE_RATE_HZ]
        assert abs(10 * np.log10(np.mean(x**2)) - device.LEVEL_DBFS) < 0.1


def test_playback_stops_once_long_enough(tmp_path: Path) -> None:
    _, items = device.playback(fake_vivos(tmp_path), seconds=1.0)
    assert len(items) == 1


def board_b() -> device.Microphones:
    return device.load_microphones(load_yaml(CONFIGS / "scenes" / "device.yaml")["microphone"])


def flat(pcm_shift: int) -> device.Microphones:
    freqs = np.arange(grid.N_BINS) * FS / grid.FFT_SIZE
    zeros = np.zeros(grid.N_BINS)
    return device.Microphones(np.ones(grid.N_BINS, np.complex64), freqs, zeros, zeros, -29.0, 0.0, pcm_shift)


def test_a_talker_level_in_spl_lands_at_the_dbfs_of_its_distance() -> None:
    cfg = load_yaml(CONFIGS / "scenes" / "device.yaml")
    mics_m = np.array([[2.0 - array.SPACING_M / 2, 2.0, 1.0], [2.0 + array.SPACING_M / 2, 2.0, 1.0]]).T
    distance_m = 2.0
    p = room.Placement(np.array([5.0, 4.0, 3.0]), mics_m, np.array([2.0, 2.0 + distance_m, 1.0]), None)
    built, _ = room.fitted_room(p, 0.0, cfg["rooms"])
    tone = np.sin(2 * np.pi * 1000.0 * np.arange(FS) / FS) * math.sqrt(2.0)
    spl_db = 65.0
    level = -29.0 + spl_db - device.SENSITIVITY_SPL_DB
    source = tone * device.TALKER_REFERENCE_M * 10.0 ** (level / 20.0)
    at_mic = np.convolve(source, built.rir[1][0])[FS // 4 : FS]
    expected = level - 20.0 * math.log10(distance_m)
    assert abs(10.0 * math.log10(np.mean(at_mic**2)) - expected) < 0.1


def test_ch0_hears_ch1_through_calib_bal() -> None:
    mics = dataclasses.replace(board_b(), self_noise_rms=0.0, pcm_shift=12)
    air = np.random.default_rng(4).standard_normal(20 * FS) * 0.004
    pcm = device.hear(np.stack([air, air]), mics, np.random.default_rng(5)).astype(np.float64)
    frames = pcm[: len(pcm) // grid.FFT_SIZE * grid.FFT_SIZE].reshape(-1, grid.FFT_SIZE, 2)
    spectra = np.fft.rfft(frames * np.hanning(grid.FFT_SIZE)[:, None], axis=1)
    s00, s11 = (np.sum(np.abs(spectra[:, :, c]) ** 2, axis=0) for c in (0, 1))
    s10 = np.sum(spectra[:, :, 1] * np.conj(spectra[:, :, 0]), axis=0)
    band = slice(4, grid.N_BINS - 4)
    level_error_db = 10 * np.log10(s00 / s11) - 20 * np.log10(np.abs(mics.gains))
    phase_error_deg = np.degrees(np.angle(s10 * mics.gains))
    assert np.max(np.abs(level_error_db[band])) < 0.2
    assert np.max(np.abs(phase_error_deg[band])) < 1.0


def test_the_floor_of_silence_is_the_datasheet_self_noise() -> None:
    mics = board_b()
    pcm = device.hear(np.zeros((2, 30 * FS)), mics, np.random.default_rng(6))
    stats = mic_pair.pair_stats(pcm[:, 0], pcm[:, 1])
    datasheet = load_yaml(CONFIGS / "scenes" / "device.yaml")["microphone"]["self_noise_dbfs_a"]
    # The datasheet level is on the slot's scale; the shift sets how far int16 full scale sits below it.
    expected = datasheet + 20 * math.log10(2.0 ** (device.SLOT_FRACTION_BITS - mics.pcm_shift) / INT16_SCALE)
    for channel in (0, 1):
        assert abs(mic_pair.noise_floor_dbfs(stats, channel) - expected) < 0.5


def test_the_shift_floors_and_saturates_as_drv_audio() -> None:
    air = np.array([[0.5, -0.5, -1e-5, 1e-5, 0.99, -1.0]] * 2)
    np.testing.assert_array_equal(
        device.hear(air, flat(16), np.random.default_rng(0))[:, 1], [16384, -16384, -1, 0, 32440, -32768]
    )
    np.testing.assert_array_equal(
        device.hear(air, flat(15), np.random.default_rng(0))[:, 1], [32767, -32768, -1, 0, 32767, -32768]
    )


@pytest.fixture
def raw_root(tmp_path: Path) -> Path:
    rng = np.random.default_rng(7)
    raw = tmp_path / "raw"
    for spk in ("A", "B", "C"):
        for n in range(2):
            burst = rng.standard_normal(FS) * np.hanning(FS) * 0.1
            write_wav(raw / "speech" / "corpus" / spk / f"{spk}{n}.wav", burst)
    write_wav(raw / "noise" / "hum" / "hum.wav", 0.05 * rng.standard_normal(3 * FS))
    floor_session(raw, "floor_a", "probe", 13, rng.integers(-40, 40, (2, FS // 2)))
    return raw


def floor_session(raw: Path, name: str, kind: str, pcm_shift: int, samples: np.ndarray) -> Path:
    """A capture session of int16 samples (2, n) as srhost.session writes it, under raw/device/board_b."""
    folder = raw / "device" / "board_b" / name
    for m in range(array.N_MICS):
        write_wav(folder / f"ch{m}.wav", samples[m] / INT16_SCALE)
    (folder / "session.json").write_text(json.dumps({"kind": kind, "pcm_shift": pcm_shift}), encoding="utf-8")
    return folder


def tiny(**changes: object) -> dict:
    cfg = load_yaml(CONFIGS / "scenes" / "device.yaml")
    small = {
        "rooms": {
            **cfg["rooms"],
            "count": 2,
            "room_m": {"x": [3.0, 3.5], "y": [3.0, 3.5], "z": [2.4, 2.5]},
            "rt60_s": [0.15, 0.2],
            "talker": {"distance_m": [0.5, 1.0], "height_m": [1.0, 1.2]},
            "interferer": {"distance_m": [0.5, 1.0], "height_m": [1.0, 1.2], "min_separation_deg": 0.0},
        },
        "session": {"items": 2, "lead_s": 0.5, "gap_s": [0.2, 0.3], "pad_s": 0.1},
        "noise": {**cfg["noise"], "probability": 0.5, "pools": [{"dir": "noise/hum", "glob": "*.wav", "weight": 1}]},
        "sessions_per_shard": 2,
        "microphone": {**cfg["microphone"], "floor": {"board": "board_b", "sessions": ["floor_a"]}},
    }
    return {**cfg, **small, **changes}


def split_file(path: Path, raw: Path, origin: str = "public") -> Path:
    items = sorted(str(p.relative_to(raw)) for p in (raw / "speech").rglob("*.wav"))
    lines = [f"{item}\t{Path(item).parent.name}\t-\t{origin}" for item in items[:5]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def screened(interim: Path, *rejected: str) -> Path:
    """interim with the rejects.tsv make screen would write, listing rejected."""
    rows = [{"item": item, "corpus": "-", "reason": "silent", "seconds": 1.0} for item in rejected]
    screen.write_tsv(interim / "screen" / "rejects.tsv", screen.REJECT_FIELDS, rows)
    return interim


def test_the_same_seed_writes_the_same_shards_and_items_line_up(raw_root: Path, tmp_path: Path) -> None:
    split = split_file(tmp_path / "train.txt", raw_root)
    for interim in ("interim", "interim_again"):
        screened(tmp_path / interim)
    first = device.build(tiny(), split, raw_root, tmp_path / "interim", tmp_path / "a", workers=2)
    second = device.build(tiny(), split, raw_root, tmp_path / "interim_again", tmp_path / "b", workers=1)
    assert yaml.safe_load(first.read_text()) == yaml.safe_load(second.read_text())
    features = np.concatenate([np.load(p) for p in sorted((tmp_path / "a").glob("*.features.npy"))])
    assert features.shape[1] == 40 and features.dtype == np.float32
    shards = sorted((tmp_path / "a").glob("*.items.jsonl"))
    assert sum(len(shard.read_text().splitlines()) for shard in shards) == 5
    for shard in shards:
        rows = [json.loads(line) for line in shard.read_text().splitlines()]
        stem = str(shard).removesuffix(".items.jsonl")
        n = len(np.load(stem + ".features.npy"))
        assert rows[0]["frame_offset"] == 0 and rows[-1]["frame_offset"] + rows[-1]["n_frames"] == n
        assert len(np.load(stem + ".figures.npy")) == n and len(np.load(stem + ".pcm.npy")) == n * grid.HOP_SAMPLES
        for row in rows:
            assert 0 < row["speech_frames"][0] < row["speech_frames"][1] <= row["n_frames"]
            assert row["room"] == "-" and 0 <= row["bank_room"] < 2


def test_board_recordings_are_refused(raw_root: Path, tmp_path: Path) -> None:
    split = split_file(tmp_path / "test.txt", raw_root, origin="board")
    with pytest.raises(ValueError, match="clean speech"):
        device.build(tiny(), split, raw_root, tmp_path / "interim", tmp_path / "out")


def test_screening_rejects_leave_the_noise_pools_and_refuse_a_split(raw_root: Path, tmp_path: Path) -> None:
    write_wav(raw_root / "noise" / "hum" / "zero.wav", np.zeros(FS))
    assert device.noise_files(tiny(), raw_root, set()) == [["noise/hum/hum.wav", "noise/hum/zero.wav"]]
    assert device.noise_files(tiny(), raw_root, {"noise/hum/zero.wav"}) == [["noise/hum/hum.wav"]]
    split = split_file(tmp_path / "train.txt", raw_root)
    interim = screened(tmp_path / "interim", split.read_text().split("\t")[0])
    with pytest.raises(ValueError, match="screening rejected"):
        device.build(tiny(), split, raw_root, interim, tmp_path / "out")


def test_synth_rows_are_read_from_interim(raw_root: Path, tmp_path: Path) -> None:
    interim = screened(tmp_path / "interim")
    burst = np.random.default_rng(3).standard_normal(FS) * np.hanning(FS) * 0.1
    write_wav(interim / "wake" / "synth_pos" / "f5" / "clone_a.wav", burst)
    split = split_file(tmp_path / "train.txt", raw_root)
    split.write_text(split.read_text() + "wake/synth_pos/f5/clone_a.wav\t-\t-\tsynth\n", encoding="utf-8")
    device.build(tiny(), split, raw_root, interim, tmp_path / "out")
    items = [
        json.loads(line)
        for p in sorted((tmp_path / "out").glob("*.items.jsonl"))
        for line in p.read_text().splitlines()
    ]
    assert [i["origin"] for i in items] == ["public"] * 5 + ["synth"]


def test_repeats_pass_over_the_split_again_in_other_sessions(raw_root: Path, tmp_path: Path) -> None:
    split = split_file(tmp_path / "train.txt", raw_root)
    manifest = device.build(tiny(), split, raw_root, screened(tmp_path / "interim"), tmp_path / "out", repeats=2)
    items = [
        json.loads(line)
        for p in sorted((tmp_path / "out").glob("*.items.jsonl"))
        for line in p.read_text().splitlines()
    ]
    assert yaml.safe_load(manifest.read_text())["repeats"] == 2 and len(items) == 10
    assert [i["item"] for i in items[:5]] == [i["item"] for i in items[5:]]
    assert all(first["session"] != again["session"] for first, again in zip(items[:5], items[5:], strict=True))


def items_of(out: Path) -> list[dict]:
    return [json.loads(line) for p in sorted(out.glob("*.items.jsonl")) for line in p.read_text().splitlines()]


def test_pitch_rides_along_and_changes_no_other_file(raw_root: Path, tmp_path: Path) -> None:
    split = split_file(tmp_path / "train.txt", raw_root)
    interim = screened(tmp_path / "interim")
    plain = yaml.safe_load(device.build(tiny(), split, raw_root, interim, tmp_path / "plain").read_text())
    with_pitch = yaml.safe_load(
        device.build(tiny(), split, raw_root, interim, tmp_path / "pitch", pitch=True).read_text()
    )
    assert "pitch" not in plain and "pads_s" not in plain and with_pitch["pitch"] is True
    assert {n: s for n, s in with_pitch["sha256"].items() if not n.endswith(".pitch.npy")} == plain["sha256"]
    cfg = PitchConfig(**tiny()["pitch"])
    for shard in sorted((tmp_path / "pitch").glob("*.items.jsonl")):
        stem = str(shard).removesuffix(".items.jsonl")
        feats, pcm, pitch = (np.load(f"{stem}.{kind}.npy") for kind in ("features", "pcm", "pitch"))
        assert pitch.shape == (len(feats), 3) and pitch.dtype == np.float32
        for row in (json.loads(line) for line in shard.read_text().splitlines()):
            at, n = row["frame_offset"], row["n_frames"]
            fresh, _ = pitch_features(to_float(pcm[at * grid.HOP_SAMPLES : (at + n) * grid.HOP_SAMPLES]), cfg)
            assert np.array_equal(pitch[at : at + n], fresh)


def test_wider_pads_keep_more_hops_before_each_item(raw_root: Path, tmp_path: Path) -> None:
    split = split_file(tmp_path / "train.txt", raw_root)
    interim = screened(tmp_path / "interim")
    pad = tiny()["session"]["pad_s"]
    device.build(tiny(), split, raw_root, interim, tmp_path / "plain")
    manifest = device.build(tiny(), split, raw_root, interim, tmp_path / "wide", pads_s=(pad + 0.2, pad))
    assert yaml.safe_load(manifest.read_text())["pads_s"] == [pad + 0.2, pad]
    whole = round(0.2 * FS) // grid.HOP_SAMPLES
    for plain, wide in zip(items_of(tmp_path / "plain"), items_of(tmp_path / "wide"), strict=True):
        extra = wide["speech_frames"][0] - plain["speech_frames"][0]
        assert extra in (whole, whole + 1)
        assert wide["n_frames"] - plain["n_frames"] == extra
        assert (
            wide["speech_frames"][1] - wide["speech_frames"][0] == plain["speech_frames"][1] - plain["speech_frames"][0]
        )


def hear_as_one_formula(air: np.ndarray, mics: device.Microphones, rng: np.random.Generator) -> np.ndarray:
    """hear as one expression, the form it had while wake and command features were built from it."""
    n = air.shape[1]
    size = device.sfft.next_fast_len(n + 2 * grid.FFT_SIZE)
    freqs = np.fft.rfftfreq(size, 1.0 / FS)
    level = np.interp(freqs, mics.freqs_hz, mics.level_db)
    response = 10.0 ** (level / 20.0) * np.exp(1j * np.interp(freqs, mics.freqs_hz, mics.phase_rad))
    ch0 = device.sfft.irfft(device.sfft.rfft(air[0], size) * response, size)[:n]
    louder = max(1.0, float(np.median(np.abs(mics.gains))))
    x = np.stack([ch0, air[1]]) / louder + mics.self_noise_rms * rng.standard_normal((2, n))
    pcm = np.floor(x * 2.0 ** (device.SLOT_FRACTION_BITS - mics.pcm_shift))
    return np.clip(pcm, -32768, 32767).astype(np.int16).T.copy()


def test_hear_is_respond_plus_self_noise_then_the_shift_byte_for_byte() -> None:
    mics = board_b()
    air = 0.01 * np.random.default_rng(5).standard_normal((2, 3 * FS))
    got = device.hear(air, mics, np.random.default_rng(6))
    assert np.array_equal(got, hear_as_one_formula(air, mics, np.random.default_rng(6)))
    assert not np.array_equal(got, hear_as_one_formula(air, mics, np.random.default_rng(7)))


def test_the_slot_bins_are_the_chains_hop_by_hop() -> None:
    from srpipe.dsp.afe import balance, hpf
    from srpipe.dsp.spec.stft import Stft

    mics = board_b()
    pcm = device.hear(0.05 * np.random.default_rng(8).standard_normal((2, 2 * FS)), mics, np.random.default_rng(9))
    x = pcm.T.astype(np.float32) / np.float32(INT16_SCALE)
    got = device.slot_bins(x, mics.gains)
    filters, stfts = hpf.Hpf(), [Stft(), Stft()]
    for t, hop in enumerate(pcm.reshape(-1, grid.HOP_SAMPLES, array.N_MICS)):
        samples = hop.astype(np.float32) / np.float32(INT16_SCALE)
        bins = [stfts[m].analyze(filters.process(m, samples[:, m])) for m in range(array.N_MICS)]
        want = np.float32(0.5) * (bins[0] + balance.apply(bins[1], mics.gains))
        power, want_power = np.abs(got[t]) ** 2, np.abs(want) ** 2
        db = np.abs(10 * np.log10((power + 1e-30) / (want_power + 1e-30)))
        loud = want_power > want_power.max() * 1e-6
        # Bins 0-3 lie in the hpf's stopband, where float32 and float64 filtering part by up to a dB.
        assert np.all(db[4:][loud[4:]] < 1e-2) and np.all(db[:4] < 1.5)


def test_the_diffuse_pair_has_the_coherence_of_the_spacing() -> None:
    from scipy import signal

    rng = np.random.default_rng(10)
    pair = device.diffuse_pair(rng.standard_normal(40 * FS), rng.standard_normal(40 * FS))
    freqs, coherence = signal.csd(pair[0], pair[1], FS, nperseg=grid.FFT_SIZE)
    _, p0 = signal.welch(pair[0], FS, nperseg=grid.FFT_SIZE)
    _, p1 = signal.welch(pair[1], FS, nperseg=grid.FFT_SIZE)
    measured = np.real(coherence) / np.sqrt(p0 * p1)
    expected = np.sinc(2.0 * freqs * array.SPACING_M / array.SPEED_OF_SOUND_M_S)
    assert np.max(np.abs(measured - expected)) < 0.05
    independent = np.real(signal.csd(pair[0], rng.standard_normal(40 * FS), FS, nperseg=grid.FFT_SIZE)[1])
    assert np.max(np.abs(independent / np.sqrt(p0 * p0))) < 0.1


def test_a_stopped_build_goes_on_from_its_finished_shards_and_refuses_another_config(
    raw_root: Path, tmp_path: Path
) -> None:
    split = split_file(tmp_path / "train.txt", raw_root)
    interim, out = screened(tmp_path / "interim"), tmp_path / "out"
    whole = yaml.safe_load(device.build(tiny(), split, raw_root, interim, tmp_path / "whole").read_text())
    device.build(tiny(), split, raw_root, interim, out)
    (out / "manifest.yaml").unlink()
    device.shard_done(out, 1).unlink()
    for path in device.shard_files(out, 1, False, True):
        path.unlink()
    kept = (out / "shard_00000.features.npy").stat().st_mtime_ns
    again = yaml.safe_load(device.build(tiny(), split, raw_root, interim, out).read_text())
    assert again == whole and (out / "shard_00000.features.npy").stat().st_mtime_ns == kept
    (out / "manifest.yaml").unlink()
    with pytest.raises(ValueError, match="another config"):
        device.build(tiny(), split, raw_root, interim, out, repeats=2)


def test_a_build_without_clean_samples_keeps_every_other_file(raw_root: Path, tmp_path: Path) -> None:
    split = split_file(tmp_path / "train.txt", raw_root)
    interim = screened(tmp_path / "interim")
    full = yaml.safe_load(device.build(tiny(), split, raw_root, interim, tmp_path / "full").read_text())
    lean = yaml.safe_load(device.build(tiny(), split, raw_root, interim, tmp_path / "lean", keep_pcm=False).read_text())
    assert lean.pop("pcm") is False and not list((tmp_path / "lean").glob("*.pcm.npy"))
    assert {k: v for k, v in full["sha256"].items() if not k.endswith(".pcm.npy")} == lean.pop("sha256")
    assert {k: v for k, v in full.items() if k != "sha256"} == lean


def test_the_captured_floor_comes_back_sample_for_sample_wrapping_round(tmp_path: Path) -> None:
    samples = np.random.default_rng(8).integers(-300, 300, (2, 1000))
    floor_session(tmp_path, "f", "probe", 13, samples)
    floor = device.load_floor({"board": "board_b", "sessions": ["f"]}, tmp_path, 13)
    mics = dataclasses.replace(board_b(), pcm_shift=13)
    pcm = device.digitise(np.zeros((2, 2500)), mics, np.random.default_rng(0), floor)
    wrapped = np.concatenate([samples] * 4, axis=1)
    assert sum(np.array_equal(wrapped[:, k : k + 2500], pcm.T) for k in range(1000)) == 1


def test_a_floor_is_a_probe_at_the_products_shift(tmp_path: Path) -> None:
    samples = np.zeros((2, 100), dtype=np.int64)
    floor_session(tmp_path, "noise", "noise", 13, samples)
    floor_session(tmp_path, "shift16", "probe", 16, samples)
    for name in ("noise", "shift16"):
        with pytest.raises(ValueError, match="a floor a probe at 13"):
            device.load_floor({"board": "board_b", "sessions": [name]}, tmp_path, 13)


def test_a_tilt_is_flat_below_its_corner_and_slopes_by_the_octave_above() -> None:
    t = np.arange(4 * FS) / FS
    for hz, octaves in ((500.0, 0.0), (2000.0, 1.0), (4000.0, 2.0)):
        tone = np.sin(2 * np.pi * hz * t)
        gain_db = 20 * np.log10(np.std(device.tilted(tone, -6.0, 1000.0)) / np.std(tone))
        assert abs(gain_db - (-6.0 * octaves)) < 0.01


def test_a_band_level_is_the_power_of_what_falls_in_the_band() -> None:
    t = np.arange(4 * FS) / FS
    levels = device.band_levels(0.1 * np.sin(2 * np.pi * 1500.0 * t))
    assert abs(levels[2] - 10 * np.log10(0.1**2 / 2)) < 0.1
    assert max(levels[:2] + levels[3:]) < levels[2] - 60
