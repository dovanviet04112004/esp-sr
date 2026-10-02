"""Record what a branch deploys (CLAUDE.md 4.3, KEHOACH 6.3): firmware/models/<branch>/meta.json and the branch's row
of contracts/models.lock.json, every file with the image entry it becomes and its sha256, the grid the model learned
on, the run it came from and the split files it learned from.
Run: python -m srpipe.export.update_lock <branch> <run> <entry>:<espdl|norm|units>:<file>... [--field key=value]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from srpipe.export import pack_models
from srpipe.generated import grid

LOCK, MODELS = pack_models.LOCK, pack_models.MODELS
META_FILE = "meta.json"


@dataclass(frozen=True)
class Deployed:
    """One file of a branch, deployed as the model image entry of that name and kind."""

    path: Path
    entry: str
    kind: str


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def learnt_from(run: Path) -> dict:
    """The run's split and the train files of its split.lock, each with its sha256."""
    cfg = yaml.safe_load((run / "config.resolved.yaml").read_text(encoding="utf-8"))
    rows = [line.split(maxsplit=1) for line in (run / "split.lock").read_text(encoding="utf-8").splitlines() if line]
    return {
        "split": cfg["split"]["version"],
        "train": [{"file": name, "sha256": digest} for digest, name in rows if name.startswith("train")],
    }


def record(branch: str, run: Path, files: list[Deployed], fields: dict) -> tuple[Path, Path]:
    """Write firmware/models/<branch>/meta.json and the branch's row of models.lock.json; files must sit in that
    folder already, and fields go into both."""
    folder = MODELS / branch
    for f in files:
        if f.path.parent.resolve() != folder.resolve() or f.kind not in pack_models.KINDS:
            raise ValueError(f"{f.path}: deploy into {folder} as one of {sorted(pack_models.KINDS)}")
    row = {
        "run": run.name,
        "grid_hash": f"0x{grid.GRID_HASH:08x}",
        "files": [{"file": f.path.name, "entry": f.entry, "kind": f.kind, "sha256": sha256(f.path)} for f in files],
        "learnt_from": learnt_from(run),
    } | fields
    meta = folder / META_FILE
    meta.write_text(json.dumps({"branch": branch} | row, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    lock["models"][branch] = row
    LOCK.write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return meta, LOCK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("branch")
    parser.add_argument("run", type=Path)
    parser.add_argument("files", nargs="+", help="<entry>:<espdl|norm|units>:<file under firmware/models/<branch>>")
    parser.add_argument("--field", action="append", default=[], metavar="KEY=VALUE")
    args = parser.parse_args(argv)
    files = [Deployed(Path(path), entry, kind) for entry, kind, path in (s.split(":", 2) for s in args.files)]
    fields = dict(item.split("=", 1) for item in args.field)
    for path in record(args.branch, args.run, files, fields):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
