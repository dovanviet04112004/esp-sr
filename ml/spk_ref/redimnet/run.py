"""ReDimNet and ReDimNet2 for srpipe.scenes.speaker: `run.py <weights> <windows.npz> <out.npy> <checkpoint> <package>`
embeds each window, the npz's float32 16 kHz audio cut at bounds, as a row. weights holds a release checkpoint and the
package (redimnet of IDRnD/ReDimNet or redimnet2 of PalabraAI/redimnet2) at its pinned commit, imported from there; the
model, built from the checkpoint's model_config as each repo's loader builds it, makes its own features, on the GPU."""

import importlib
import sys
from itertools import pairwise
from pathlib import Path

import numpy as np
import torch

WRAPPERS = {"redimnet": ("redimnet.model", "ReDimNetWrap"), "redimnet2": ("redimnet2.redimnet2", "ReDimNet2Wrap")}


def main() -> int:
    weights, windows, out = (Path(a) for a in sys.argv[1:4])
    checkpoint, package = sys.argv[4:6]
    sys.path.insert(0, str(weights))
    module, wrapper = WRAPPERS[package]
    saved = torch.load(weights / checkpoint, map_location="cpu")
    model = getattr(importlib.import_module(module), wrapper)(**saved["model_config"])
    loaded = model.load_state_dict(saved["state_dict"])
    if loaded.missing_keys or loaded.unexpected_keys:
        raise ValueError(f"{checkpoint}: {loaded}")
    # ReDimNet2 runs ~14 times slower on the CPU than ReDimNet at the same size (measurements/speaker.md).
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.eval().to(device)
    with np.load(windows) as f:
        audio, bounds = f["audio"], f["bounds"]
    rows = []
    with torch.no_grad():
        for lo, hi in pairwise(bounds):
            rows.append(model(torch.from_numpy(audio[lo:hi])[None].to(device))[0].cpu().numpy())
    np.save(out, np.stack(rows).astype(np.float32))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
