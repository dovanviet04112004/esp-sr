"""GTCRN of Xiaobin-Rong/gtcrn (MIT) in the Python chain's ns slot (KEHOACH 3.9, E9-T15): the streaming model of the
pinned commit, with the official DNS3 checkpoint, run a hop at a time on the slot's complex bins; the magnitude ratio
of its output to them, at most 1, is the slot's gain. SRPIPE_NS=gtcrn puts it in every chain device.listen builds.
Its deploy packs the checkpoint's weights for ai_engine/src/ns_gtcrn/ (ADR-0047).
Run: python -m srpipe.tasks.ns.gtcrn deploy
"""

from __future__ import annotations

import argparse
import functools
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np
import torch

from srpipe.core import run_dir
from srpipe.core.config import CONFIGS, ML_ROOT, NS_ENV, data_paths, load_yaml
from srpipe.export import update_lock
from srpipe.scenes import refs
from srpipe.tasks.ns import gtcrn_mirror

ENGINES = ("gtcrn",)
TINY = 1e-12  # under a bin's magnitude from int16 rounding noise
BRANCH, ENTRY, BACKEND = "ns", "ns_gtcrn", "gtcrn"


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


@functools.cache
def slot_factory(name: str) -> Callable[[], GtcrnSlot]:
    """The maker of ns slots of engine name, one per process: its files fetched once into cache/afe_ref/gtcrn/ by URL
    and sha256 (ns.yaml's gtcrn), its model built once and shared by every chain, torch on one thread (ns.md 10); an
    unknown name stops the process."""
    if name not in ENGINES:
        raise SystemExit(f"{NS_ENV}={name}: the Python chain's ns slot knows {list(ENGINES)}")
    torch.set_num_threads(1)
    spec = load_yaml(CONFIGS / "models" / "ns.yaml")["gtcrn"]
    model = stream_model(fetched(spec), spec["checkpoint"])
    return lambda: GtcrnSlot(model, spec["caches"])


def fetched(spec: dict) -> Path:
    """cache/afe_ref/gtcrn/ holding every file of spec at its pin, fetched by URL when absent."""
    folder = data_paths()["cache"] / "afe_ref" / "gtcrn"
    for path, pin in spec["files"].items():
        refs.fetch(pin, folder / path)
    return folder


def mirror_gap(model: torch.nn.Module, w: gtcrn_mirror.Weights, spec: dict) -> float:
    """The largest gap between a gain of the mirror and of StreamGTCRN over a stream of random bins, each hop at a
    level drawn from spec's deploy.level_db, both from zero state."""
    check = spec["deploy"]
    rng = np.random.default_rng(check["seed"])
    slot, st = GtcrnSlot(model, spec["caches"]), gtcrn_mirror.State()
    gap = 0.0
    for _ in range(check["hops"]):
        scale = 10.0 ** (rng.uniform(*check["level_db"]) / 20.0)
        bins = (rng.standard_normal(gtcrn_mirror.BINS) + 1j * rng.standard_normal(gtcrn_mirror.BINS)) * scale
        bins = bins.astype(np.complex64)
        gap = max(gap, float(np.abs(gtcrn_mirror.step(w, st, bins) - slot.gains(bins, None)).max()))
    return gap


def deploy(spec: dict) -> tuple[Path, Path]:
    """artifacts/ns/runs/<run>/ holding the pinned checkpoint, its source every file's pin, then the mirror's pack of
    its weights as firmware/models/ns/ns_gtcrn.bin, a WEIGHTS entry, with the branch's meta.json and lock row
    (KEHOACH 6.3); refused when the mirror strays from StreamGTCRN by over deploy.gain_atol on any gain."""
    torch.set_num_threads(1)
    folder = fetched(spec)
    model = stream_model(folder, spec["checkpoint"])
    w = gtcrn_mirror.weights({k: v.detach().numpy() for k, v in model.state_dict().items()})
    if (gap := mirror_gap(model, w, spec)) > spec["deploy"]["gain_atol"]:
        raise ValueError(f"the mirror strays {gap:.3g} from StreamGTCRN, over {spec['deploy']['gain_atol']}")
    source = {"extractor": BACKEND, "pins": {"args": [spec["checkpoint"]], "files": spec["files"]}}
    run = run_dir.create_run_dir(ML_ROOT / "artifacts", BRANCH, {"source": source, "deploy": spec["deploy"]})
    shutil.copyfile(folder / spec["checkpoint"], run / Path(spec["checkpoint"]).name)
    target = update_lock.MODELS / BRANCH
    target.mkdir(parents=True, exist_ok=True)
    blob = target / f"{ENTRY}.bin"
    blob.write_bytes(gtcrn_mirror.pack(w))
    print(f"{run.name}: mirror within {gap:.3g} of StreamGTCRN, {blob.stat().st_size} bytes of weights")
    return update_lock.record(BRANCH, run, [update_lock.Deployed(blob, ENTRY, "weights")], {"backend": BACKEND})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("deploy", help="the checkpoint's weights into firmware/models/ns and models.lock.json")
    parser.parse_args(argv)
    for path in deploy(load_yaml(CONFIGS / "models" / "ns.yaml")["gtcrn"]):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
