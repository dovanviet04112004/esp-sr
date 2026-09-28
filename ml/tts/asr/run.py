"""PhoWhisper for srpipe.tts: `run.py <repo>@<revision> <clips.jsonl> <out.jsonl>` transcribes each line {id, wav},
resampled to the rate of the model's feature extractor, and writes {id, text} lines, the text as the model spells it."""

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
    lines = []
    for line in Path(sys.argv[2]).read_text(encoding="utf-8").splitlines():
        clip = json.loads(line)
        x, rate = sf.read(clip["wav"], dtype="float32", always_2d=True)
        step = math.gcd(rate, rate_hz)
        x = resample_poly(x.mean(axis=1), rate_hz // step, rate // step).astype("float32")
        text = asr({"raw": x, "sampling_rate": rate_hz})["text"]
        lines.append(json.dumps({"id": clip["id"], "text": text}, ensure_ascii=False))
    Path(sys.argv[3]).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
