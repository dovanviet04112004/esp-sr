"""3D-Speaker's extractors for srpipe.scenes.speaker: `run.py <weights> <windows.npz> <out.npy> <arch> <checkpoint>`
embeds each window, the npz's float32 16 kHz audio cut at bounds, as a row. weights holds the checkpoint and the
speakerlab model files of arch at the pinned commit of modelscope/3D-Speaker, imported from there, with the arguments
that repo's infer_sv.py builds them with. Features as its FBank: Kaldi fbank of 80 bins, no dither, mean removed."""

import importlib
import sys
from itertools import pairwise
from pathlib import Path

import numpy as np
import torch
import torchaudio.compliance.kaldi as kaldi

N_MELS = 80
RATE_HZ = 16000
ARCHS = {
    "campplus": ("speakerlab.models.campplus.DTDNN", "CAMPPlus", {"feat_dim": N_MELS, "embedding_size": 192}),
    "eres2netv2": (
        "speakerlab.models.eres2net.ERes2NetV2",
        "ERes2NetV2",
        {"feat_dim": N_MELS, "embedding_size": 192, "baseWidth": 26, "scale": 2, "expansion": 2},
    ),
}


def main() -> int:
    weights, windows, out = (Path(a) for a in sys.argv[1:4])
    arch, checkpoint = sys.argv[4:6]
    sys.path.insert(0, str(weights))
    module, name, args = ARCHS[arch]
    model = getattr(importlib.import_module(module), name)(**args)
    model.load_state_dict(torch.load(weights / checkpoint, map_location="cpu"))
    model.eval()
    with np.load(windows) as f:
        audio, bounds = f["audio"], f["bounds"]
    rows = []
    with torch.no_grad():
        for lo, hi in pairwise(bounds):
            feat = kaldi.fbank(
                torch.from_numpy(audio[lo:hi])[None], num_mel_bins=N_MELS, sample_frequency=RATE_HZ, dither=0.0
            )
            rows.append(model((feat - feat.mean(0, keepdim=True))[None])[0].numpy())
    np.save(out, np.stack(rows).astype(np.float32))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
