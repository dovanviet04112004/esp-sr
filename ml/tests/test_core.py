"""Check srpipe.core: config merging, data paths, run directories, seeding and int16 audio."""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import soundfile as sf
import yaml

from srpipe.core import audio_io, config, logger, run_dir, seed


def test_a_history_row_prints_counts_whole_and_measures_to_four_digits() -> None:
    row = {"step": 10000, "draws": np.int64(12000), "loss": 0.123456, "lr": 0.000855}
    assert logger.row_line(row) == "step 10000 draws 12000 loss 0.1235 lr 0.000855"


def test_later_config_wins_and_nested_keys_merge() -> None:
    merged = config.deep_merge({"a": 1, "b": {"x": 1, "y": 2}}, {"b": {"y": 3}, "c": 4})
    assert merged == {"a": 1, "b": {"x": 1, "y": 3}, "c": 4}


def test_overrides_keep_their_yaml_type() -> None:
    cfg = config.apply_overrides({"train": {"lr": 0.1}}, ["train.lr=0.001", "train.bands=[18, 22]", "name=ns"])
    assert cfg == {"train": {"lr": 0.001, "bands": [18, 22]}, "name": "ns"}


def test_malformed_override_is_refused() -> None:
    with pytest.raises(ValueError):
        config.apply_overrides({}, ["no_equals_sign"])


def test_data_root_env_moves_every_data_path(tmp_path: Path) -> None:
    paths = config.data_paths(env={config.DATA_ROOT_ENV: str(tmp_path)})
    assert paths["raw"] == tmp_path / "raw"
    assert paths["processed"] == tmp_path / "processed"
    assert paths["artifacts"] == config.ML_ROOT / "artifacts"
    assert paths["manifests"] == config.ML_ROOT / "data" / "manifests"
    assert paths["splits"] == config.ML_ROOT / "data" / "splits"


def test_default_data_root_sits_under_ml() -> None:
    assert config.data_paths(env={})["data_root"] == config.ML_ROOT / "data"


def test_run_dir_holds_config_split_lock_and_env(tmp_path: Path) -> None:
    split = tmp_path / "train.txt"
    split.write_text("spk_001/a.wav\n")
    cfg = {"model": "wake", "lr": 0.001}
    run = run_dir.create_run_dir(tmp_path / "artifacts", "wake", cfg, [split], today=date(2026, 9, 26))
    assert run.name.startswith("20260926_") and run.name.endswith(run_dir.config_hash(cfg))
    assert yaml.safe_load((run / "config.resolved.yaml").read_text()) == cfg
    assert "train.txt" in (run / "split.lock").read_text()
    assert "python" in (run / "env.txt").read_text()


def test_config_hash_ignores_key_order() -> None:
    assert run_dir.config_hash({"a": 1, "b": 2}) == run_dir.config_hash({"b": 2, "a": 1})


def test_same_seed_draws_the_same_numbers() -> None:
    first = seed.seed_everything(7).standard_normal(5)
    second = seed.seed_everything(7).standard_normal(5)
    np.testing.assert_array_equal(first, second)


def test_int16_round_trip_is_exact() -> None:
    pcm = np.array([-32768, -1, 0, 1, 12345, 32767], dtype=np.int16)
    np.testing.assert_array_equal(audio_io.to_int16(audio_io.to_float(pcm)), pcm)


def test_int16_saturates_instead_of_wrapping() -> None:
    np.testing.assert_array_equal(audio_io.to_int16(np.array([1.5, -1.5])), [32767, -32768])


def test_wav_round_trip_and_rate_check(tmp_path: Path) -> None:
    x = (np.arange(320, dtype=np.float32).reshape(160, 2) - 160) / 1024
    path = tmp_path / "a.wav"
    audio_io.write_wav(path, x)
    y, rate = audio_io.read_wav(path)
    assert rate == 16000 and y.shape == (160, 2)
    np.testing.assert_array_equal(audio_io.to_int16(y), audio_io.to_int16(x))
    audio_io.write_wav(path, x, rate_hz=8000)
    with pytest.raises(ValueError):
        audio_io.read_wav(path)


def _wav_bytes(x: np.ndarray, rate_hz: int) -> bytes:
    buffer = io.BytesIO()
    sf.write(buffer, x, rate_hz, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def test_items_come_from_files_and_parquet_rows_at_the_grid_rate_whole_or_a_span(tmp_path: Path) -> None:
    tone = 0.25 * np.sin(2 * np.pi * 440.0 * np.arange(4800) / 48000.0)
    rows = [{"bytes": _wav_bytes(tone * (k + 1) / 4, 48000), "path": None} for k in range(4)]
    table = pa.table({"audio": rows, "transcription": ["a", "b", "c", "d"]})
    pq.write_table(table, tmp_path / "corpus.parquet", row_group_size=3)
    audio_io.write_wav(tmp_path / "one.wav", np.full((160, 2), 0.5))
    reader = audio_io.ItemReader(tmp_path)
    assert reader.read("one.wav").shape == (160,)
    third, first = reader.read("corpus.parquet#3"), reader.read("corpus.parquet#0")
    assert third.shape == (1600,)
    assert abs(np.sqrt(np.mean(third[400:1200] ** 2)) / np.sqrt(np.mean(first[400:1200] ** 2)) - 4.0) < 0.01
    with pytest.raises(IndexError):
        reader.read("corpus.parquet#4")
    span = reader.read("corpus.parquet#3@0.025-0.075")
    assert span.shape == (800,)
    np.testing.assert_allclose(span[100:700], third[500:1100], atol=1e-3)
    assert reader.read("one.wav@0-0.005").shape == (80,)


def test_a_ramp_fades_both_edges_and_leaves_the_middle() -> None:
    x = np.ones(1000)
    y = audio_io.ramped(x, 0.005)
    n = round(0.005 * audio_io.grid.SAMPLE_RATE_HZ)
    assert y[0] < 0.01 and y[-1] < 0.01 and np.all(np.diff(y[:n]) > 0)
    np.testing.assert_array_equal(y[n:-n], 1.0)
    assert np.array_equal(audio_io.ramped(x, 0.0), x) and np.all(x == 1.0)
    assert audio_io.ramped(np.ones(10), 0.01).max() <= 1.0
