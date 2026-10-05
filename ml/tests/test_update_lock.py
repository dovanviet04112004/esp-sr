"""update_lock: a branch's meta.json and lock row carry each deployed file's entry, kind and sha256, the grid, the run
and the train files of its split; another branch's row stays, and a file outside the branch's folder is refused."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from srpipe.export import pack_models, update_lock
from srpipe.generated import grid, listen


def run_dir(root: Path) -> Path:
    run = root / "runs" / "20261002_abc_123"
    run.mkdir(parents=True)
    (run / "config.resolved.yaml").write_text(yaml.safe_dump({"split": {"version": "v2"}}), encoding="utf-8")
    (run / "split.lock").write_text("aa  test.txt\nbb  train_vivos.txt\ncc  val.txt\n", encoding="utf-8")
    return run


def test_a_branch_records_its_files_grid_run_and_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock = tmp_path / "models.lock.json"
    lock.write_text(json.dumps({"version": 1, "models": {"wake": {"run": "older"}}}), encoding="utf-8")
    monkeypatch.setattr(update_lock, "LOCK", lock)
    monkeypatch.setattr(update_lock, "MODELS", tmp_path / "models")
    folder = tmp_path / "models" / "command"
    folder.mkdir(parents=True)
    (folder / "command_ctc.espdl").write_bytes(b"graph")
    files = [update_lock.Deployed(folder / "command_ctc.espdl", "command_ctc", "espdl")]
    meta, _ = update_lock.record("command", run_dir(tmp_path), files, {"backend": "ctc"})
    row = json.loads(lock.read_text(encoding="utf-8"))["models"]
    assert row["wake"] == {"run": "older"} and row["command"]["backend"] == "ctc"
    assert row["command"]["files"] == [
        {
            "file": "command_ctc.espdl",
            "entry": "command_ctc",
            "kind": "espdl",
            "sha256": hashlib.sha256(b"graph").hexdigest(),
        }
    ]
    assert row["command"]["grid_hash"] == f"0x{grid.GRID_HASH:08x}" and row["command"]["run"] == "20261002_abc_123"
    assert row["command"]["learnt_from"] == {"split": "v2", "train": [{"file": "train_vivos.txt", "sha256": "bb"}]}
    assert json.loads(meta.read_text(encoding="utf-8")) == {"branch": "command"} | row["command"]


def test_a_file_outside_the_branch_folder_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_lock, "MODELS", tmp_path / "models")
    (tmp_path / "stray.espdl").write_bytes(b"graph")
    with pytest.raises(ValueError, match="deploy into"):
        update_lock.record(
            "command", run_dir(tmp_path), [update_lock.Deployed(tmp_path / "stray.espdl", "x", "espdl")], {}
        )


def test_the_packer_takes_locked_files_and_refuses_a_changed_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "models.lock.json"
    lock.write_text(json.dumps({"version": 1, "models": {}}), encoding="utf-8")
    for module in (update_lock, pack_models):
        monkeypatch.setattr(module, "LOCK", lock)
        monkeypatch.setattr(module, "MODELS", tmp_path / "models")
    folder = tmp_path / "models" / "command"
    folder.mkdir(parents=True)
    (folder / "command_ctc.espdl").write_bytes(b"graph")
    (folder / "command_ctc.norm.bin").write_bytes(b"\0" * 8)
    files = [
        update_lock.Deployed(folder / "command_ctc.espdl", "command_ctc", "espdl"),
        update_lock.Deployed(folder / "command_ctc.norm.bin", "command_ctc", "norm"),
    ]
    update_lock.record("command", run_dir(tmp_path), files, {"listen_hash": f"0x{listen.HASH:08x}"})
    entries, listen_hash, thresholds = pack_models.locked()
    assert [(e.name, e.kind, e.data) for e in entries] == [
        ("command_ctc", "espdl", b"graph"),
        ("command_ctc", "norm", b"\0" * 8),
    ]
    assert listen_hash == listen.HASH and thresholds == (0, 0)
    (folder / "command_ctc.espdl").write_bytes(b"other")
    with pytest.raises(ValueError, match="differs from the sha256"):
        pack_models.locked()


def test_the_packer_refuses_rows_of_two_listen_contracts_and_takes_0_from_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "models.lock.json"
    monkeypatch.setattr(pack_models, "LOCK", lock)
    rows = {"command": {"files": [], "listen_hash": "0x1"}, "wake": {"files": [], "listen_hash": "0x2"}}
    lock.write_text(json.dumps({"version": 1, "models": rows}), encoding="utf-8")
    with pytest.raises(ValueError, match=r"2 listen\.yaml"):
        pack_models.locked()
    lock.write_text(json.dumps({"version": 1, "models": {"wake": {"files": []}}}), encoding="utf-8")
    assert pack_models.locked() == ([], 0, (0, 0))
