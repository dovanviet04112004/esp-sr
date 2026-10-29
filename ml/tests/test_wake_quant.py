"""Wake's int8 ladder and deploy on a tiny run: a row scored as the chip scores, then its threshold and smoothing
carried through meta.json and the lock into the image header."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from srpipe.core.config import load_yaml  # noqa: E402
from srpipe.export import pack_models, update_lock  # noqa: E402
from srpipe.tasks.wake import CONFIG, quant  # noqa: E402
from srpipe.tasks.wake.model.tcn import Tcn  # noqa: E402

BANDS = 8
MODEL = {"kernel": 3, "channels": 8, "dilations": [1, 2]}


def shard_folder(folder: Path, rng: np.random.Generator, marked: bool, sessions: int = 6, hops: int = 64) -> None:
    """A finished build of sessions of hops each; a positive's speech in hops 20..40, a bump in band 0."""
    folder.mkdir(parents=True)
    features = rng.normal(-8.0, 1.0, (sessions * hops, BANDS)).astype(np.float32)
    rows = []
    for k in range(sessions):
        if marked:
            features[k * hops + 20 : k * hops + 40, 0] += 6.0
        span = [20, 40] if marked else [0, hops]
        rows.append(
            {"item": f"i{k}", "origin": "public", "frame_offset": k * hops, "n_frames": hops, "speech_frames": span}
        )
    np.save(folder / "shard_00000.features.npy", features)
    (folder / "shard_00000.items.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    listed = {"shard_00000.features.npy": "-", "shard_00000.items.jsonl": "-"}
    (folder / "manifest.yaml").write_text(yaml.safe_dump({"sha256": listed}))


def tiny_run(tmp_path: Path, cfg: dict) -> tuple[Path, dict]:
    rng = np.random.default_rng(5)
    root = tmp_path / "processed" / "wake" / "s1"
    for name in ("train_pos", "val_pos"):
        shard_folder(root / name, rng, True)
    for name in ("train_neg", "val_neg", "test_neg"):
        shard_folder(root / name, rng, False)
    run = tmp_path / "run"
    run.mkdir()
    torch.manual_seed(5)
    torch.save(Tcn(BANDS, **MODEL).state_dict(), run / "model.pt")
    np.savez(run / "band_stats.npz", mean=np.full(BANDS, -8.0, np.float32), std=np.ones(BANDS, np.float32))
    (run / "metrics.yaml").write_text(yaml.safe_dump({"val": {"step": 1, "threshold": 0.5}, "history": []}))
    resolved = cfg | {"model": MODEL, "train": cfg["train"] | {"split": "s1"}}
    (run / "config.resolved.yaml").write_text(yaml.safe_dump(resolved))
    (run / "split.lock").write_text("")
    return run, {"processed": tmp_path / "processed"}


def test_a_row_is_scored_as_the_chip_scores_and_its_threshold_reaches_the_header(tmp_path, monkeypatch) -> None:
    cfg = load_yaml(CONFIG)
    cfg["train"] = cfg["train"] | {"window_hops": 32}
    cfg["quant"] = cfg["quant"] | {"calib_windows": 4, "calibrations": ["minmax"]}
    run, paths = tiny_run(tmp_path, cfg)
    ladder = yaml.safe_load(quant.ptq(cfg, run, paths).read_text(encoding="utf-8"))
    row = ladder["rows"]["minmax"]
    assert ladder["calib_windows"] == 4 and 50 <= row["threshold_permille"] <= 999
    assert 0.0 <= row["val_recall"] <= 1.0 and row["test_fa_per_hour"] >= 0.0
    with pytest.raises(ValueError, match="no row"):
        quant.deploy(cfg, run, "kl")

    lock = tmp_path / "models.lock.json"
    lock.write_text(json.dumps({"models": {}}))
    monkeypatch.setattr(update_lock, "MODELS", tmp_path / "models")
    monkeypatch.setattr(update_lock, "LOCK", lock)
    monkeypatch.setattr(pack_models, "MODELS", tmp_path / "models")
    monkeypatch.setattr(pack_models, "LOCK", lock)
    meta, _ = quant.deploy(cfg, run, "minmax")
    deployed = json.loads(meta.read_text(encoding="utf-8"))
    assert deployed["threshold"] == {"permille": row["threshold_permille"]} and deployed["step_frames"] == 8
    assert (tmp_path / "models" / "wake" / quant.NORM_FILE).stat().st_size == 2 * BANDS * 4
    locked = pack_models.locked()
    assert (locked.wake_th_permille, locked.wake_smooth_hops) == (row["threshold_permille"], cfg["eval"]["smooth_hops"])
    assert [e.kind for e in locked.entries] == ["espdl", "norm"]
