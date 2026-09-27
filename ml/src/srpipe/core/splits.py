"""Read split files and check the rules of KEHOACH 1.3 on each split version (KEHOACH 4.4.1)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

ORIGINS = frozenset({"public", "board", "synth", "scene"})
ROLES = frozenset({"train", "val", "calib", "test"})
LEARNING_ROLES = frozenset({"train", "val", "calib"})
ABSENT = "-"
CHECKSUM_LINE = re.compile(r"^- (\S+\.txt): ([0-9a-f]{64})\s*$")


@dataclass(frozen=True)
class Row:
    item: str
    spk: str
    room: str
    origin: str


def role_of(name: str) -> str:
    """The role a split file plays: its stem up to the first underscore, so test_neg.txt is a test set."""
    return Path(name).stem.split("_", 1)[0]


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_split(path: Path) -> list[Row]:
    """Rows of one split file: item, spk, room and origin separated by tabs, blank lines skipped."""
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 4 or fields[3] not in ORIGINS:
            raise ValueError(f"{path.name}:{number}: want item, spk, room, origin separated by tabs")
        rows.append(Row(*fields))
    return rows


def versions(splits_root: Path) -> list[Path]:
    """Every directory under splits_root that holds split files, e.g. splits/wake/v1."""
    return sorted({path.parent for path in splits_root.rglob("*.txt")})


def _files_in(files: dict[str, list[Row]], role: str) -> list[Row]:
    return [row for name, rows in files.items() if role_of(name) == role for row in rows]


def _unknown_roles(files: dict[str, list[Row]]) -> list[str]:
    unknown = [name for name in files if role_of(name) not in ROLES]
    return [f"{name}: role {role_of(name)!r} is none of {sorted(ROLES)}" for name in unknown]


def _speakers_across_roles(files: dict[str, list[Row]]) -> list[str]:
    roles_of: dict[str, set[str]] = {}
    for name, rows in files.items():
        # int8 calibration draws from the learning material, so it shares speakers with train.
        role = "train" if role_of(name) == "calib" else role_of(name)
        for row in rows:
            if row.spk != ABSENT:
                roles_of.setdefault(row.spk, set()).add(role)
    return [f"speaker {spk} sits in {sorted(roles)}" for spk, roles in sorted(roles_of.items()) if len(roles) > 1]


def _synth_in_test(files: dict[str, list[Row]]) -> list[str]:
    problems = []
    for name, rows in files.items():
        count = sum(row.origin == "synth" for row in rows)
        if role_of(name) == "test" and count:
            problems.append(f"{name}: {count} synthetic rows in a test set")
    return problems


def _calib_meets_test(files: dict[str, list[Row]]) -> list[str]:
    shared = {row.item for row in _files_in(files, "calib")} & {row.item for row in _files_in(files, "test")}
    return [f"{len(shared)} items in both calib and test, e.g. {min(shared)}"] if shared else []


def _no_unseen_room(files: dict[str, list[Row]]) -> list[str]:
    test_rooms = {row.room for row in _files_in(files, "test") if row.origin == "board"}
    learning = [row for role in sorted(LEARNING_ROLES) for row in _files_in(files, role)]
    learning_rooms = {row.room for row in learning if row.origin == "board"}
    if test_rooms and test_rooms <= learning_rooms:
        return [f"every board room of the test sets also feeds learning: {sorted(test_rooms)}"]
    return []


def _checksums(directory: Path, names: list[str]) -> list[str]:
    split_md = directory / "SPLIT.md"
    if not split_md.exists():
        return ["SPLIT.md is missing"]
    recorded = {}
    for line in split_md.read_text(encoding="utf-8").splitlines():
        match = CHECKSUM_LINE.match(line)
        if match:
            recorded[match.group(1)] = match.group(2)
    problems = [f"SPLIT.md names {name}, which is not here" for name in sorted(set(recorded) - set(names))]
    for name in names:
        if name not in recorded:
            problems.append(f"SPLIT.md has no sha256 for {name}")
        elif recorded[name] != sha256_of(directory / name):
            problems.append(f"{name} no longer matches its sha256 in SPLIT.md")
    return problems


def check_version(directory: Path) -> list[str]:
    """Every broken rule in one split version as a readable line; empty when the version is sound."""
    files = {path.name: read_split(path) for path in sorted(directory.glob("*.txt"))}
    return (
        _unknown_roles(files)
        + _speakers_across_roles(files)
        + _synth_in_test(files)
        + _calib_meets_test(files)
        + _no_unseen_room(files)
        + _checksums(directory, list(files))
    )
