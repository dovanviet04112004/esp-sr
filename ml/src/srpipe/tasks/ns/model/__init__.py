"""The two candidates of the ns slot, trained in one run (KEHOACH 3.9, ADR-0014)."""

from __future__ import annotations

from torch import nn

from srpipe.tasks.ns.model.nsnet import Nsnet
from srpipe.tasks.ns.model.rnnoise import Rnnoise

RNNOISE = "rnnoise16k"
NSNET = "nsnet16k_"


def names(cfg: dict) -> list[str]:
    """Every candidate of the run: RNNoise-16k, then NSNet-16k at each of nsnet.train_sizes."""
    return [RNNOISE] + [NSNET + size.lower() for size in cfg["nsnet"]["train_sizes"]]


def family(name: str) -> str:
    """rnnoise16k, or nsnet16k for any NSNet-16k size."""
    return name if name == RNNOISE else NSNET.rstrip("_")


def build(cfg: dict, name: str) -> nn.Module:
    if name == RNNOISE:
        return Rnnoise(cfg["rnnoise"], cfg["power_floor"])
    size = name.removeprefix(NSNET).upper()
    if not name.startswith(NSNET) or size not in cfg["nsnet"]["sizes"]:
        raise ValueError(f"no ns candidate {name!r}; ns.yaml gives {names(cfg)}")
    return Nsnet(cfg["nsnet"], cfg["nsnet"]["sizes"][size]["hidden"], cfg["power_floor"])
