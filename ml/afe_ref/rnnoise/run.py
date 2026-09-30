"""RNNoise of xiph through pyrnnoise for srpipe.scenes.refs: `run.py <jobs.jsonl>` cleans each line {in, out}, mono
16 kHz int16, into a file of the same length. pyrnnoise resamples to RNNoise's 48 kHz and back around the model."""

import json
import sys

import numpy as np
import soundfile as sf
from pyrnnoise import RNNoise

RATE_HZ = 16000


def enhance(x: np.ndarray) -> np.ndarray:
    denoiser = RNNoise(sample_rate=RATE_HZ)
    frames = [frame for _, frame in denoiser.denoise_chunk(x[np.newaxis, :], partial=True)]
    y = np.concatenate(frames, axis=1)[0]
    return np.pad(y[: len(x)], (0, max(0, len(x) - len(y))))


def main() -> int:
    (listing,) = sys.argv[1:2]
    with open(listing, encoding="utf-8") as f:
        jobs = [json.loads(line) for line in f if line.strip()]
    for job in jobs:
        x, rate = sf.read(job["in"], dtype="int16")
        if rate != RATE_HZ or x.ndim != 1:
            raise ValueError(f"{job['in']}: {rate} Hz with shape {x.shape}; this run takes mono {RATE_HZ} Hz")
        sf.write(job["out"], enhance(x).astype(np.int16), RATE_HZ, subtype="PCM_16")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
