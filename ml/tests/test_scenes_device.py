"""srpipe.scenes.device: levels in dB SPL that distance and sensitivity turn into dBFS, ch0 hearing ch1 through
calib/bal, the datasheet self noise, drv_audio's shift, the same shards for the same seed; the playback file."""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from srpipe.core import screen
from srpipe.core.audio_io import write_wav
from srpipe.core.config import CONFIGS, load_yaml
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
    for channel in (0, 1):
        assert abs(mic_pair.noise_floor_dbfs(stats, channel) - (-90.0)) < 0.5


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
    return raw


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
