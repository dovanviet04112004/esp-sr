"""GTCRN of Xiaobin-Rong/gtcrn (MIT) in the Python chain's ns slot (KEHOACH 3.9, E9-T15): the streaming model of the
pinned commit, with the official DNS3 checkpoint, run a hop at a time on the slot's complex bins; the magnitude ratio
of its output to them, at most 1, is the slot's gain. SRPIPE_NS=gtcrn puts it in every chain device.listen builds."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np
import torch

from srpipe.core.config import CONFIGS, NS_ENV, data_paths, load_yaml
from srpipe.scenes import refs

ENGINES = ("gtcrn",)
TINY = 1e-12  # under a bin's magnitude from int16 rounding noise


class GtcrnSlot:
    """One chain's GTCRN: the shared streaming model, this chain's caches zero at start; the aec's echo is not heard."""

    def __init__(self, model: torch.nn.Module, caches: dict[str, list[int]]) -> None:
        self.model = model
        self.conv, self.tra, self.inter = (torch.zeros(caches[k]) for k in ("conv", "tra", "inter"))

    def gains(self, bins: np.ndarray, echo_power: np.ndarray | None) -> np.ndarray:
        x = torch.view_as_real(torch.from_numpy(np.ascontiguousarray(bins, dtype=np.complex64)))[None, :, None, :]
        with torch.inference_mode():
            y, self.conv, self.tra, self.inter = self.model(x, self.conv, self.tra, self.inter)
        out = torch.view_as_complex(y[0, :, 0, :].contiguous()).numpy()
        return np.minimum(np.abs(out) / (np.abs(bins) + TINY), 1.0).astype(np.float32)


def stream_model(folder: Path, checkpoint: str) -> torch.nn.Module:
    """The pinned commit's StreamGTCRN with the offline GTCRN's weights at checkpoint, both imported from folder."""
    sys.path.insert(0, str(folder / "stream"))
    from gtcrn import GTCRN
    from gtcrn_stream import StreamGTCRN
    from modules.convert import convert_to_stream

    offline = GTCRN().eval()
    offline.load_state_dict(torch.load(folder / checkpoint, map_location="cpu")["model"])
    model = StreamGTCRN().eval()
    convert_to_stream(model, offline)
    return model


def slot_factory(name: str) -> Callable[[], GtcrnSlot]:
    """A maker of ns slots of engine name: its files fetched once into cache/afe_ref/gtcrn/ by URL and sha256 (ns.yaml's
    gtcrn), its model built once for every chain; an unknown name stops the process."""
    if name not in ENGINES:
        raise SystemExit(f"{NS_ENV}={name}: the Python chain's ns slot knows {list(ENGINES)}")
    spec = load_yaml(CONFIGS / "models" / "ns.yaml")["gtcrn"]
    folder = data_paths()["cache"] / "afe_ref" / "gtcrn"
    for path, pin in spec["files"].items():
        refs.fetch(pin, folder / path)
    model = stream_model(folder, spec["checkpoint"])
    return lambda: GtcrnSlot(model, spec["caches"])
