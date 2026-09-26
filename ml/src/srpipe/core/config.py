"""Load YAML configs with dotted command-line overrides, and resolve the data paths of paths.yaml."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

ML_ROOT = Path(__file__).resolve().parents[3]
CONFIGS = ML_ROOT / "configs"
DATA_ROOT_ENV = "SRPIPE_DATA_ROOT"


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def apply_overrides(cfg: dict[str, Any], overrides: list[str]) -> dict[str, Any]:
    """Apply key.sub=value strings; values are parsed as YAML so numbers and lists keep their type."""
    out = copy.deepcopy(cfg)
    for item in overrides:
        key, sep, raw = item.partition("=")
        if not sep or not key:
            raise ValueError(f"override {item!r} is not key=value")
        node = out
        *parents, leaf = key.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = yaml.safe_load(raw)
    return out


def load_config(*names: str, overrides: list[str] | None = None) -> dict[str, Any]:
    """Merge configs/<name>.yaml in order, later files winning, then the overrides."""
    cfg: dict[str, Any] = {}
    for name in names:
        cfg = deep_merge(cfg, load_yaml(CONFIGS / f"{name}.yaml"))
    return apply_overrides(cfg, overrides or [])


def read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    pairs = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            pairs[key.strip()] = value.strip()
    return pairs


def data_paths(env: dict[str, str] | None = None) -> dict[str, Path]:
    """Every path of paths.yaml as an absolute Path; SRPIPE_DATA_ROOT from the environment or .env wins."""
    raw = load_yaml(CONFIGS / "common" / "paths.yaml")
    env = env if env is not None else {**read_dotenv(ML_ROOT / ".env"), **os.environ}
    data_root = Path(env.get(DATA_ROOT_ENV) or raw["data_root"])
    data_root = data_root if data_root.is_absolute() else ML_ROOT / data_root
    out = {"data_root": data_root}
    for key, value in raw.items():
        if key == "data_root":
            continue
        path = Path(str(value).replace("{data_root}", str(data_root)))
        out[key] = path if path.is_absolute() else ML_ROOT / path
    return out
