"""UL-UNAS of Xiaobin-Rong/ul-unas (MIT) in the Python chain's ns slot (KEHOACH 3.9, ADR-0048): the streaming model of
the pinned commit with the official DNS3 checkpoint, run a hop at a time on the slot's complex bins. It learnt on
Hann-windowed bins and the slot hands sqrt-Hann ones, so they are scaled by the two windows' sums first; its real mask
is the slot's gain. SRPIPE_NS=ulunas puts it in every chain device.listen builds. Its deploy packs the checkpoint's
weights for ai_engine/src/ns_ulunas/ (ADR-0048).
Run: python -m srpipe.tasks.ns.ulunas deploy
"""

from __future__ import annotations

import argparse
import functools
import importlib.util
import sys
import types
from collections.abc import Callable
from pathlib import Path

import numpy as np
import torch

from srpipe.core.config import CONFIGS, data_paths, load_yaml
from srpipe.scenes import refs
from srpipe.tasks.ns import gtcrn, ulunas_mirror

TINY = 1e-12  # under a bin's magnitude from int16 rounding noise
STREAM_FILE = "ulunas_onnx/stream/ulunas_stream.py"
EXPORT_ONLY = ("onnx", "onnxruntime", "tqdm")  # STREAM_FILE imports these; only its __main__ uses them


class UlunasSlot:
    """One chain's UL-UNAS: the shared streaming model, this chain's caches zero at start; the aec's echo is not
    heard."""

    def __init__(self, model: torch.nn.Module, caches: tuple[torch.Tensor, ...]) -> None:
        self.model, self.scale = model, ulunas_mirror.window_scale()
        self.caches = tuple(c.clone() for c in caches)

    def gains(self, bins: np.ndarray, echo_power: np.ndarray | None) -> np.ndarray:
        scaled = np.ascontiguousarray(bins * self.scale, dtype=np.complex64)
        x = torch.view_as_real(torch.from_numpy(scaled))[None, :, None, :]
        with torch.inference_mode():
            y, *caches = self.model(x, *self.caches)
        self.caches = tuple(caches)
        out = torch.view_as_complex(y[0, :, 0, :].contiguous()).numpy()
        return np.minimum(np.abs(out) / (np.abs(scaled) + TINY), 1.0).astype(np.float32)


def fetched(spec: dict) -> Path:
    """cache/afe_ref/ulunas/ holding every file of spec at its pin, fetched by URL when absent."""
    folder = data_paths()["cache"] / "afe_ref" / "ulunas"
    for path, pin in spec["files"].items():
        refs.fetch(pin, folder / path)
    return folder


def stream_module(folder: Path) -> types.ModuleType:
    """The pinned STREAM_FILE as a module, importing folder's ulunas.py; the export tools it imports but never calls
    stand in as empty modules when absent."""
    missing = [name for name in EXPORT_ONLY if importlib.util.find_spec(name) is None]
    for name in missing:
        sys.modules[name] = types.ModuleType(name)
        sys.modules[name].tqdm = None
    sys.path.insert(0, str(folder))
    try:
        spec = importlib.util.spec_from_file_location("ulunas_stream", folder / STREAM_FILE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(folder))
        for name in missing:
            del sys.modules[name]
    return module


def stream_model(folder: Path, checkpoint: str) -> tuple[torch.nn.Module, tuple[torch.Tensor, ...]]:
    """The pinned commit's StreamULUNAS with the offline ULUNAS's weights at checkpoint, and its zero caches."""
    module = stream_module(folder)
    offline = module.ULUNAS().eval()
    offline.load_state_dict(torch.load(folder / checkpoint, map_location="cpu", weights_only=True)["model"])
    model = module.StreamULUNAS().eval()
    model.load_state_dict(offline.state_dict(), strict=True)
    return model, module.StreamULUNAS.init_caches(1)


@functools.cache
def slot_factory() -> Callable[[], UlunasSlot]:
    """The maker of UL-UNAS's ns slots, one per process: its files fetched once into cache/afe_ref/ulunas/ by URL and
    sha256 (ns.yaml's ulunas), its model built once and shared by every chain, torch on one thread."""
    torch.set_num_threads(1)
    spec = load_yaml(CONFIGS / "models" / "ns.yaml")["ulunas"]
    model, caches = stream_model(fetched(spec), spec["checkpoint"])
    return lambda: UlunasSlot(model, caches)


def deploy(spec: dict) -> tuple[Path, Path]:
    """UL-UNAS's pinned checkpoint as the ns_ulunas entry, held to StreamULUNAS by its mirror (gtcrn.deploy_entry)."""
    torch.set_num_threads(1)
    folder = fetched(spec)
    model, caches = stream_model(folder, spec["checkpoint"])
    w = ulunas_mirror.weights({k: v.detach().numpy() for k, v in model.state_dict().items()})
    slot, st = UlunasSlot(model, caches), ulunas_mirror.State()
    gap = gtcrn.mirror_gap(lambda b: slot.gains(b, None), lambda b: ulunas_mirror.step(w, st, b), spec["deploy"])
    return gtcrn.deploy_entry(spec, "ulunas", folder, ulunas_mirror.pack(w), gap)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("deploy", help="the checkpoint's weights into firmware/models/ns and models.lock.json")
    parser.parse_args(argv)
    for path in deploy(load_yaml(CONFIGS / "models" / "ns.yaml")["ulunas"]):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
