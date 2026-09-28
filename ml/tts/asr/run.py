"""PhoWhisper for srpipe.tts: `run.py <repo>@<revision> <batch> <clips.jsonl> <out.jsonl>` transcribes each line
{id, wav}, resampled to the rate of the model's feature extractor, batch clips per forward, and writes {id, text}
lines in the same order, the text as the model spells it."""

import json
import math
import sys
from pathlib import Path

import soundfile as sf
import torch
from scipy.signal import resample_poly
from transformers import pipeline


def main() -> int:
    repo, revision = sys.argv[1].split("@")
    cuda = torch.cuda.is_available()
    asr = pipeline(
        "automatic-speech-recognition",
        model=repo,
        revision=revision,
        device=0 if cuda else -1,
        dtype=torch.float16 if cuda else torch.float32,
    )
    rate_hz = asr.feature_extractor.sampling_rate
    clips = [json.loads(line) for line in Path(sys.argv[3]).read_text(encoding="utf-8").splitlines()]
    inputs = []
    for clip in clips:
        x, rate = sf.read(clip["wav"], dtype="float32", always_2d=True)
        step = math.gcd(rate, rate_hz)
        inputs.append(
            {
                "raw": resample_poly(x.mean(axis=1), rate_hz // step, rate // step).astype("float32"),
                "sampling_rate": rate_hz,
            }
        )
    texts = [r["text"] for r in asr(inputs, batch_size=int(sys.argv[2]))]
    lines = [json.dumps({"id": c["id"], "text": t}, ensure_ascii=False) for c, t in zip(clips, texts, strict=True)]
    Path(sys.argv[4]).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
