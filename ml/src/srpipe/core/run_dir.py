"""One directory per run with everything needed to reproduce it (KEHOACH 4.4, CLAUDE.md 4.3)."""

from __future__ import annotations

import hashlib
import platform
import subprocess
from datetime import date
from importlib import metadata
from pathlib import Path
from typing import Any

import yaml

TRACKED_PACKAGES = ("numpy", "scipy", "soundfile", "pyroomacoustics", "torch", "esp-ppq")


def config_hash(cfg: dict[str, Any]) -> str:
    canonical = yaml.safe_dump(cfg, sort_keys=True, allow_unicode=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:6]


def git_sha(repo: Path) -> str:
    try:
        sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True)
        return sha + ("-dirty" if dirty.stdout.strip() else "")
    except (OSError, subprocess.CalledProcessError):
        return "nogit"


def environment_text() -> str:
    lines = [f"python {platform.python_version()}", f"platform {platform.platform()}"]
    for name in TRACKED_PACKAGES:
        try:
            lines.append(f"{name} {metadata.version(name)}")
        except metadata.PackageNotFoundError:
            continue
    return "\n".join(lines) + "\n"


def split_lock(split_files: list[Path]) -> str:
    return "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in sorted(split_files))


def create_run_dir(
    artifacts: Path,
    branch: str,
    cfg: dict[str, Any],
    split_files: list[Path] | None = None,
    today: date | None = None,
    repo: Path | None = None,
) -> Path:
    """Make artifacts/<branch>/runs/<date>_<gitsha>_<cfghash>/ holding the resolved config, split lock and env."""
    stamp = (today or date.today()).strftime("%Y%m%d")
    sha = git_sha(repo or Path(__file__).resolve().parents[4])
    run = artifacts / branch / "runs" / f"{stamp}_{sha}_{config_hash(cfg)}"
    run.mkdir(parents=True, exist_ok=False)
    (run / "config.resolved.yaml").write_text(yaml.safe_dump(cfg, sort_keys=True, allow_unicode=True), "utf-8")
    (run / "split.lock").write_text(split_lock(split_files or []), "utf-8")
    (run / "env.txt").write_text(environment_text(), "utf-8")
    return run
