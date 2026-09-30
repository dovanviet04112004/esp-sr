"""F5-TTS for srpipe.tts: `run.py <checkpoint>@<revision> <vocoder>@<revision> <timing.json> <requests.jsonl>` clones
each line's voice, {id, text, out, ref_audio, ref_text, seed, speed}, into out, a 16-bit WAV at the vocoder's rate.
The spoken length follows the reference's syllable rate, at least timing's min_syllable_s per syllable, divided by
speed, plus tail_s for the last syllable to fade; a clip louder than peak_dbfs is scaled down to it."""

import ctypes.util
import json
import re
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from f5_tts.api import F5TTS
from f5_tts.infer.utils_infer import preprocess_ref_audio_text
from huggingface_hub import snapshot_download


def fetch(pinned: str) -> Path:
    repo, revision = pinned.split("@")
    return Path(snapshot_download(repo, revision=revision))


def syllables(text: str) -> int:
    """Written Vietnamese separates every syllable by a space or punctuation."""
    return len(re.findall(r"\w+", text))


def main() -> int:
    if not ctypes.util.find_library("avutil"):
        raise SystemExit("F5 reads references through torchcodec, which needs FFmpeg: sudo apt install ffmpeg")
    checkpoint, vocoder = fetch(sys.argv[1]), fetch(sys.argv[2])
    timing = json.loads(sys.argv[3])
    peak = 10.0 ** (timing["peak_dbfs"] / 20.0)
    # The ViVoice repo ships its vocab as config.json; its README renames it to vocab.txt.
    vocab = checkpoint / "config.json"
    f5 = F5TTS(
        model="F5TTS_Base",
        ckpt_file=str(checkpoint / "model_last.pt"),
        vocab_file=str(vocab),
        vocoder_local_path=str(vocoder),
    )
    for line in Path(sys.argv[4]).read_text(encoding="utf-8").splitlines():
        req = json.loads(line)
        ref_file, ref_text = preprocess_ref_audio_text(req["ref_audio"], req["ref_text"])
        ref_seconds = sf.info(ref_file).duration
        # F5 sizes speech by UTF-8 bytes, which cuts short a text with fewer diacritics than its reference.
        at_reference_rate = ref_seconds * syllables(req["text"]) / syllables(ref_text)
        # A command said alone is slower than the same syllables inside a read sentence.
        spoken = max(at_reference_rate, syllables(req["text"]) * timing["min_syllable_s"]) / req["speed"]
        Path(req["out"]).parent.mkdir(parents=True, exist_ok=True)
        wav, rate, _ = f5.infer(
            ref_file=req["ref_audio"],
            ref_text=req["ref_text"],
            gen_text=req["text"],
            seed=req["seed"],
            speed=req["speed"],
            fix_duration=ref_seconds + spoken + timing["tail_s"],
        )
        wav = np.asarray(wav, dtype=np.float32)
        loudest = float(np.max(np.abs(wav))) if len(wav) else 0.0
        if loudest > peak:
            wav = wav * np.float32(peak / loudest)
        sf.write(req["out"], wav, rate, subtype="PCM_16")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
