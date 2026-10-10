"""ECAPA-TDNN of SpeechBrain on VoxCeleb 1 and 2 (speechbrain/spkrec-ecapa-voxceleb) for srpipe.scenes.speaker:
`run.py <weights> <windows.npz> <out.npy>` embeds each window, the npz's float32 16 kHz audio cut at bounds, as a row.
weights holds the model's files at the pinned revision, read through pretrained_path so nothing is fetched; each window
goes through encode_batch alone, as verify_files does: fbank's sentence mean removed, the embedding unnormalised."""

import sys
from itertools import pairwise
from pathlib import Path

import numpy as np
import torch
from speechbrain.inference.speaker import EncoderClassifier


def main() -> int:
    weights, windows, out = (Path(a) for a in sys.argv[1:4])
    model = EncoderClassifier.from_hparams(
        source=str(weights),
        savedir=str(weights / "loaded"),
        overrides={"pretrained_path": str(weights)},
        run_opts={"device": "cpu"},
    )
    with np.load(windows) as f:
        audio, bounds = f["audio"], f["bounds"]
    rows = []
    with torch.no_grad():
        for lo, hi in pairwise(bounds):
            rows.append(model.encode_batch(torch.from_numpy(audio[lo:hi])[None])[0, 0].numpy())
    np.save(out, np.stack(rows).astype(np.float32))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
