"""DNSMOS P.835 for srpipe.scenes.refs: `run.py <sig_bak_ovr.onnx> <clips.jsonl> <out.jsonl>` scores each line
{id, wav} and writes {id, sig, bak, ovrl} lines in the same order (KEHOACH 3.16).

The method of microsoft/DNS-Challenge DNSMOS/dnsmos_local.py (MIT): 16 kHz audio in windows of 9.01 s every 1 s, a clip
shorter than one window repeated until it fills one, the model's raw scores through the published non-personalised
polynomials, then averaged over the windows."""

import json
import sys

import numpy as np
import onnxruntime as ort
import soundfile as sf

RATE_HZ = 16000
WINDOW_S = 9.01
HOP_S = 1.0
INPUT_NAME = "input_1"
# np.poly1d coefficients, highest power first, of the non-personalised P.835 fit.
POLY = {
    "sig": [-0.08397278, 1.22083953, 0.0052439],
    "bak": [-0.13166888, 1.60915514, -0.39604546],
    "ovrl": [-0.06766283, 1.11546468, 0.04602535],
}


def score(session: ort.InferenceSession, wav: str) -> dict[str, float]:
    x, rate = sf.read(wav, dtype="float64")
    if rate != RATE_HZ or x.ndim != 1:
        raise ValueError(f"{wav}: {rate} Hz with shape {x.shape}; DNSMOS takes mono {RATE_HZ} Hz")
    window = int(WINDOW_S * RATE_HZ)
    while len(x) < window:
        x = np.append(x, x)
    hops = int(np.floor(len(x) / RATE_HZ) - WINDOW_S) + 1
    raw = []
    for k in range(hops):
        segment = x[int(k * HOP_S * RATE_HZ) : int((k * HOP_S + WINDOW_S) * RATE_HZ)]
        if len(segment) < window:
            continue
        raw.append(session.run(None, {INPUT_NAME: segment.astype("float32")[np.newaxis, :]})[0][0])
    raw = np.array(raw)
    return {name: float(np.mean(np.poly1d(POLY[name])(raw[:, k]))) for k, name in enumerate(("sig", "bak", "ovrl"))}


def main() -> int:
    model, listing, out = sys.argv[1:4]
    session = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
    with open(listing, encoding="utf-8") as f:
        clips = [json.loads(line) for line in f if line.strip()]
    with open(out, "w", encoding="utf-8") as f:
        for clip in clips:
            f.write(json.dumps({"id": clip["id"], **score(session, clip["wav"])}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
