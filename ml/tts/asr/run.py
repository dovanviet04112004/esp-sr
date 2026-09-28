"""PhoWhisper for srpipe.tts: `run.py <repo>@<revision> <batch> <clips.jsonl> <out.jsonl>` transcribes each line
{id, wav, targets?} as Vietnamese, batch clips per forward, and writes {id, text, logp, targets} lines in the same
order: the text as the model spells it, and the log-probability of that text and of each target given the clip. The
model is called directly: the transformers pipeline holds about 1 GB more VRAM, past what a 4 GB card shared with
Windows leaves, and then spills into system memory at a quarter of the speed."""

import json
import math
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from scipy.signal import resample_poly
from transformers import AutoProcessor, WhisperForConditionalGeneration
from transformers.modeling_outputs import BaseModelOutput

LANGUAGE = "vi"


def load(wav: str, rate_hz: int) -> np.ndarray:
    x, rate = sf.read(wav, dtype="float32", always_2d=True)
    step = math.gcd(rate, rate_hz)
    return resample_poly(x.mean(axis=1), rate_hz // step, rate // step).astype("float32")


def styled(text: str) -> str:
    """A text as PhoWhisper writes a sentence: lower case, closed by a full stop."""
    return text.lower().rstrip(".!? ") + "."


def logp(model, tokenizer, encoded: torch.Tensor, text: str) -> float:
    """Summed log-probability of text and its end token after the task prefix, given one clip's encoder output."""
    ids = torch.tensor([tokenizer(text).input_ids], device=encoded.device)
    with torch.inference_mode():
        logits = model(encoder_outputs=BaseModelOutput(last_hidden_state=encoded), decoder_input_ids=ids[:, :-1]).logits
    scores = torch.log_softmax(logits.float(), dim=-1).gather(-1, ids[:, 1:, None])[0, :, 0]
    return float(scores[len(tokenizer.prefix_tokens) - 1 :].sum())


def main() -> int:
    repo, revision = sys.argv[1].split("@")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    processor = AutoProcessor.from_pretrained(repo, revision=revision)
    tokenizer = processor.tokenizer
    tokenizer.set_prefix_tokens(language=LANGUAGE, task="transcribe", predict_timestamps=False)
    model = WhisperForConditionalGeneration.from_pretrained(
        repo, revision=revision, dtype=dtype, attn_implementation="sdpa"
    )
    model = model.to(device).eval()
    rate_hz = processor.feature_extractor.sampling_rate
    batch = int(sys.argv[2])
    clips = [json.loads(line) for line in Path(sys.argv[3]).read_text(encoding="utf-8").splitlines()]
    lines = []
    for start in range(0, len(clips), batch):
        part = clips[start : start + batch]
        audio = [load(c["wav"], rate_hz) for c in part]
        features = processor.feature_extractor(audio, sampling_rate=rate_hz, return_tensors="pt").input_features
        features = features.to(device, dtype)
        with torch.inference_mode():
            encoded = model.model.encoder(features).last_hidden_state
            ids = model.generate(
                encoder_outputs=BaseModelOutput(last_hidden_state=encoded), language=LANGUAGE, task="transcribe"
            )
        for k, (clip, text) in enumerate(zip(part, processor.batch_decode(ids, skip_special_tokens=True), strict=True)):
            one = encoded[k : k + 1]
            targets = {t: logp(model, tokenizer, one, styled(t)) for t in clip.get("targets", [])}
            row = {"id": clip["id"], "text": text, "logp": logp(model, tokenizer, one, text), "targets": targets}
            lines.append(json.dumps(row, ensure_ascii=False))
    Path(sys.argv[4]).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
