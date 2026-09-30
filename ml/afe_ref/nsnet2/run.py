"""NSNet2, the Microsoft DNS Challenge baseline nsnet2-20ms-baseline.onnx, for srpipe.scenes.refs:
`run.py <model.onnx> <jobs.jsonl>` cleans each line {in, out}, mono 16 kHz, into a file of the same length.

The method of microsoft/DNS-Challenge NSNet2-baseline, enhance_onnx.py and featurelib.py (MIT): a square-root Hann
window of 20 ms at half overlap, a 320-point FFT, log10 power floored at 1e-12, the model's gains held within -80 dB
and 1, overlap-add back, the first frame of the analysis delay dropped."""

import json
import sys

import numpy as np
import onnxruntime as ort
import soundfile as sf

RATE_HZ = 16000
WINDOW = 320
HOP = 160
POWER_FLOOR = 1e-12
MIN_GAIN_DB = -80.0


def stft(x: np.ndarray, win: np.ndarray) -> np.ndarray:
    """Frames of WINDOW samples every HOP, each ending HOP after the last, less the first: (bins, frames)."""
    frames = int(np.ceil((len(x) + WINDOW - HOP) / HOP))
    x = np.concatenate([x, np.zeros(frames * HOP - len(x))])
    frame = np.zeros(WINDOW)
    out = np.zeros((WINDOW // 2 + 1, frames), dtype=complex)
    for n in range(frames):
        frame = np.concatenate([frame[HOP:], x[n * HOP : (n + 1) * HOP]])
        out[:, n] = np.fft.rfft(win * frame, WINDOW)
    return out[:, WINDOW // HOP - 1 :]


def istft(spec: np.ndarray, win: np.ndarray) -> np.ndarray:
    frames = spec.shape[1]
    x = np.zeros(HOP * (frames - 1) + WINDOW)
    for n in range(frames):
        x[n * HOP : n * HOP + WINDOW] += win * np.fft.irfft(spec[:, n], WINDOW)[:WINDOW]
    return x


def enhance(session: ort.InferenceSession, x: np.ndarray) -> np.ndarray:
    win = np.sqrt(np.hanning(WINDOW))
    spec = stft(x, win)
    feat = np.log10(np.maximum(np.abs(spec) ** 2, POWER_FLOOR))
    name = session.get_inputs()[0].name
    gain = session.run(None, {name: feat.T[np.newaxis].astype(np.float32)})[0][0].T
    y = istft(spec * np.clip(gain, 10 ** (MIN_GAIN_DB / 20), 1.0), win)
    return np.pad(y[: len(x)], (0, max(0, len(x) - len(y))))


def main() -> int:
    model, listing = sys.argv[1:3]
    session = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
    with open(listing, encoding="utf-8") as f:
        jobs = [json.loads(line) for line in f if line.strip()]
    for job in jobs:
        x, rate = sf.read(job["in"], dtype="float64")
        if rate != RATE_HZ or x.ndim != 1:
            raise ValueError(f"{job['in']}: {rate} Hz with shape {x.shape}; NSNet2 takes mono {RATE_HZ} Hz")
        sf.write(job["out"], np.clip(enhance(session, x), -1.0, 1.0), RATE_HZ, subtype="PCM_16")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
