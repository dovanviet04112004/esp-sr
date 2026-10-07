"""Load YAML configs with dotted command-line overrides, and resolve the data paths of paths.yaml."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

from srpipe.generated import listen

ML_ROOT = Path(__file__).resolve().parents[3]
CONFIGS = ML_ROOT / "configs"
DATA_ROOT_ENV = "SRPIPE_DATA_ROOT"
PITCH_SHARED = ("min_f0_hz", "max_f0_hz", "normalization_left_s", "pov_scale", "pitch_scale", "delta_pitch_scale")


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


def contract_front() -> dict[str, Any]:
    """The recogniser's front end of contracts/listen.yaml: the features and the pitch tracker (KEHOACH 3.11)."""
    return {"features": dict(listen.FEATURES), "pitch": dict(listen.PITCH)}


def front_of(cfg: dict[str, Any]) -> dict[str, Any]:
    """The front end a config learns on: the one a run recorded under listen, else the contract's; a config naming a
    pitch_source tracks pitch by it, with the contract's F0 range, left context and scales (KEHOACH 3.11)."""
    if cfg.get("listen"):
        return cfg["listen"]
    front = contract_front()
    if source := cfg.get("pitch_source"):
        front["pitch"] = {k: front["pitch"][k] for k in PITCH_SHARED} | dict(source)
    return front


def load_device(path: Path, front: dict[str, Any] | None = None) -> dict[str, Any]:
    """A board simulation config with a front end, the contract's unless given, that every build records as its own;
    path is under configs/ when relative."""
    return load_yaml(CONFIGS / path) | (front or contract_front())


def device_of(cfg: dict[str, Any]) -> dict[str, Any]:
    """The board simulation of a model config or a run, with the front end it learns on."""
    return load_device(cfg["features"], front_of(cfg))


def load_run_config(run: Path) -> dict[str, Any]:
    """A run's resolved config with the front end it learnt on; a run without one learnt on 40 bands (ADR-0017), one
    from before listen.yaml v5 with Kaldi's pitch ballast over the whole stream (ballast_window_s 0, KEHOACH 3.11), a
    pitch_source run on its tracker's pitch as saved, and one that gave SpecAugment's band masks in bands with their
    share of its mel bands."""
    cfg = load_yaml(run / "config.resolved.yaml")
    front = contract_front()
    before = front | {"features": front["features"] | {"n_bands": 40}, "pitch": {}}
    learnt = cfg.get("listen", before)
    pitch = learnt["pitch"] or front["pitch"] | {"ballast_window_s": 0.0}
    if "source" not in pitch:
        pitch = {"ballast_window_s": 0.0} | pitch
    cfg = cfg | {"listen": learnt | {"pitch": pitch}}
    masks = cfg.get("train", {}).get("masks", {})
    if "band_width" in masks:
        share = masks["band_width"] / learnt["features"]["n_bands"]
        cfg["train"] = cfg["train"] | {
            "masks": {k: v for k, v in masks.items() if k != "band_width"} | {"band_share": share}
        }
    return cfg


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
