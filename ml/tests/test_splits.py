"""Hold every committed split to KEHOACH 1.3, and make each rule fire on a planted violation."""

from __future__ import annotations

from pathlib import Path

import pytest

from srpipe.core import config, splits


def write_version(directory: Path, files: dict[str, list[str]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, rows in files.items():
        (directory / name).write_text("".join(row + "\n" for row in rows), encoding="utf-8")
    sums = "".join(f"- {name}: {splits.sha256_of(directory / name)}\n" for name in files)
    (directory / "SPLIT.md").write_text(f"Luật: theo người nói, seed 42\n\n{sums}", encoding="utf-8")
    return directory


def sound_files() -> dict[str, list[str]]:
    return {
        "train.txt": [
            "device/board_b/20261001_r1_001\tspk_001\tr1\tboard",
            "interim/wake/synth_pos/0001.wav\ttts_a\t-\tsynth",
        ],
        "val.txt": ["speech/vivos/train/VIVOSSPK02/a.wav\tVIVOSSPK02\t-\tpublic"],
        "test_pos.txt": ["device/board_b/20261002_r2_001\tspk_003\tr2\tboard"],
        "test_neg.txt": [
            "device/board_b/20261002_r2_002\tspk_003\tr2\tboard",
            "device/board_b/20261002_r2_003\t-\tr2\tboard",
        ],
        "calib_wake.txt": ["device/board_b/20261001_r1_004\tspk_002\tr1\tboard"],
        "calib_ns.txt": [
            "device/board_b/20261001_r1_005\tspk_002\tr1\tboard",
            "device/board_b/20261001_r1_006\t-\tr1\tboard",
        ],
    }


def problems_with(tmp_path: Path, **changes: list[str]) -> str:
    files = sound_files()
    files.update({f"{name}.txt": rows for name, rows in changes.items()})
    return " | ".join(splits.check_version(write_version(tmp_path / "v1", files)))


def test_every_committed_split_keeps_the_rules() -> None:
    root = config.data_paths()["splits"]
    broken = {str(v.relative_to(root)): splits.check_version(v) for v in splits.versions(root)}
    assert not any(broken.values()), broken


def test_a_sound_version_passes_with_shared_speakers_inside_one_role(tmp_path: Path) -> None:
    assert problems_with(tmp_path) == ""


def test_a_speaker_in_train_and_test_fails(tmp_path: Path) -> None:
    rows = [*sound_files()["test_pos.txt"], "device/board_b/20261002_r2_009\tspk_001\tr2\tboard"]
    assert "speaker spk_001 sits in ['test', 'train']" in problems_with(tmp_path, test_pos=rows)


def test_synthetic_speech_in_a_test_set_fails(tmp_path: Path) -> None:
    rows = [*sound_files()["test_pos.txt"], "interim/wake/synth_pos/0002.wav\ttts_b\t-\tsynth"]
    assert "test_pos.txt: 1 synthetic rows in a test set" in problems_with(tmp_path, test_pos=rows)


def test_an_item_in_calib_and_test_fails(tmp_path: Path) -> None:
    rows = [*sound_files()["calib_ns.txt"], "device/board_b/20261002_r2_003\t-\tr2\tboard"]
    assert "1 items in both calib and test" in problems_with(tmp_path, calib_ns=rows)


def test_a_test_set_without_an_unseen_room_fails(tmp_path: Path) -> None:
    rows = [*sound_files()["train.txt"], "device/board_b/20261003_r2_001\tspk_004\tr2\tboard"]
    assert "every board room of the test sets is also in train" in problems_with(tmp_path, train=rows)


def test_a_file_edited_after_signing_fails(tmp_path: Path) -> None:
    directory = write_version(tmp_path / "v1", sound_files())
    with (directory / "val.txt").open("a", encoding="utf-8") as handle:
        handle.write("speech/vivos/train/VIVOSSPK09/b.wav\tVIVOSSPK09\t-\tpublic\n")
    assert "val.txt no longer matches its sha256 in SPLIT.md" in splits.check_version(directory)


def test_a_file_missing_from_split_md_fails(tmp_path: Path) -> None:
    directory = write_version(tmp_path / "v1", sound_files())
    (directory / "val_extra.txt").write_text("speech/vivos/x.wav\tVIVOSSPK07\t-\tpublic\n", encoding="utf-8")
    assert "SPLIT.md has no sha256 for val_extra.txt" in splits.check_version(directory)


def test_an_unknown_role_fails(tmp_path: Path) -> None:
    assert "holdout.txt: role 'holdout'" in problems_with(tmp_path, holdout=["speech/vivos/y.wav\tV1\t-\tpublic"])


def test_a_row_without_four_fields_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "train.txt"
    path.write_text("device/board_b/20261001_r1_001\tspk_001\tboard\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"train\.txt:1"):
        splits.read_split(path)
