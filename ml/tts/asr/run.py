"""PhoWhisper for srpipe.tts: `run.py <repo>@<revision> <batch> <clips.jsonl> <out.jsonl>` transcribes each line
{id, wav} as Vietnamese, batch clips per forward, and writes {id, text} lines in the same order, the text as the model
spells it. The model is called directly: the transformers pipeline holds about 1 GB more VRAM, past what a 4 GB card
shared with Windows leaves, and then spills into system memory at a quarter of the speed."""

import json
import math
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from scipy.signal import resample_poly
from transformers import AutoProcessor, WhisperForConditionalGeneration


def load(wav: str, rate_hz: int) -> np.ndarray:
    x, rate = sf.read(wav, dtype="float32", always_2d=True)
    step = math.gcd(rate, rate_hz)
    return resample_poly(x.mean(axis=1), rate_hz // step, rate // step).astype("float32")


def main() -> int:
    repo, revision = sys.argv[1].split("@")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    processor = AutoProcessor.from_pretrained(repo, revision=revision)
    model = WhisperForConditionalGeneration.from_pretrained(
        repo, revision=revision, dtype=dtype, attn_implementation="sdpa"
    )
    model = model.to(device).eval()
    rate_hz = processor.feature_extractor.sampling_rate
    batch = int(sys.argv[2])
    clips = [json.loads(line) for line in Path(sys.argv[3]).read_text(encoding="utf-8").splitlines()]
    texts: list[str] = []
    for start in range(0, len(clips), batch):
        audio = [load(c["wav"], rate_hz) for c in clips[start : start + batch]]
        features = processor.feature_extractor(audio, sampling_rate=rate_hz, return_tensors="pt").input_features
        with torch.inference_mode():
            ids = model.generate(input_features=features.to(device, dtype), language="vi", task="transcribe")
        texts += processor.batch_decode(ids, skip_special_tokens=True)
    lines = [json.dumps({"id": c["id"], "text": t}, ensure_ascii=False) for c, t in zip(clips, texts, strict=True)]
    Path(sys.argv[4]).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
