"""PhoWhisper-large on CTranslate2 for srpipe.core.extract: `run.py <model dir> <batch> <clips.jsonl> <out.jsonl>`
hears each line {id, wav} as Vietnamese, batch clips per forward, greedy, and writes {id, text} lines in the same
order. int8 weights hold half the memory of the float16 checker, so a 4 GB card takes clips in batches."""

import ctypes
import glob
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

# CTranslate2 opens the CUDA 12 libraries by name; the wheels keep them under site-packages/nvidia.
for pattern in ("nvidia/cublas/lib/libcublas*.so.12", "nvidia/cudnn/lib/libcudnn*.so.9"):
    for lib in sorted(glob.glob(os.path.join(os.path.dirname(np.__file__), "..", pattern))):
        ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)

import ctranslate2  # noqa: E402
from faster_whisper.feature_extractor import FeatureExtractor  # noqa: E402
from faster_whisper.tokenizer import Tokenizer  # noqa: E402
from tokenizers import Tokenizer as HfTokenizer  # noqa: E402

LANGUAGE = "vi"


def load(wav: str, rate_hz: int) -> np.ndarray:
    x, rate = sf.read(wav, dtype="float32", always_2d=True)
    step = math.gcd(rate, rate_hz)
    return resample_poly(x.mean(axis=1), rate_hz // step, rate // step).astype("float32")


def window(x: np.ndarray, extractor: FeatureExtractor) -> np.ndarray:
    """The clip padded with silence to the model's 30 s window before its log-mel, as the float16 checker pads it."""
    return np.pad(x, (0, max(0, extractor.n_samples - len(x))))[: extractor.n_samples]


def main() -> int:
    folder, batch = Path(sys.argv[1]), int(sys.argv[2])
    model = ctranslate2.models.Whisper(str(folder), device="cuda", compute_type="int8_float16")
    extractor = FeatureExtractor(feature_size=model.n_mels)
    tokenizer = Tokenizer(HfTokenizer.from_file(str(folder / "tokenizer.json")), True, "transcribe", LANGUAGE)
    prompt = [*tokenizer.sot_sequence, tokenizer.no_timestamps]
    frames = extractor.nb_max_frames
    clips = [json.loads(line) for line in Path(sys.argv[3]).read_text(encoding="utf-8").splitlines()]
    lines = []
    for start in range(0, len(clips), batch):
        part = clips[start : start + batch]
        audio = [window(load(c["wav"], extractor.sampling_rate), extractor) for c in part]
        mels = np.stack([extractor(x)[:, :frames] for x in audio])
        found = model.generate(ctranslate2.StorageView.from_array(mels), [prompt] * len(part), beam_size=1)
        for clip, result in zip(part, found, strict=True):
            text = tokenizer.decode([t for t in result.sequences_ids[0] if t < tokenizer.eot]).strip()
            lines.append(json.dumps({"id": clip["id"], "text": text}, ensure_ascii=False))
    Path(sys.argv[4]).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
