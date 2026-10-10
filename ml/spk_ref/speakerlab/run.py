"""CAM++ of 3D-Speaker on 200k Mandarin speakers (iic/speech_campplus_sv_zh-cn_16k-common) for srpipe.scenes.speaker:
`run.py <weights> <windows.npz> <out.npy>` embeds each window, the npz's float32 16 kHz audio cut at bounds, as a row.
weights holds campplus_cn_common.bin and speakerlab/models/campplus at the pinned commit of modelscope/3D-Speaker,
imported from there. Features as that repo's FBank: Kaldi fbank of 80 bins, no dither, the window's mean removed."""

import sys
from itertools import pairwise
from pathlib import Path

import numpy as np
import torch
import torchaudio.compliance.kaldi as kaldi

N_MELS = 80
EMBEDDING = 192
RATE_HZ = 16000


def main() -> int:
    weights, windows, out = (Path(a) for a in sys.argv[1:4])
    sys.path.insert(0, str(weights))
    from speakerlab.models.campplus.DTDNN import CAMPPlus

    model = CAMPPlus(feat_dim=N_MELS, embedding_size=EMBEDDING)
    model.load_state_dict(torch.load(weights / "campplus_cn_common.bin", map_location="cpu"))
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
