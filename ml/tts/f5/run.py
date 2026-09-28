"""F5-TTS for srpipe.tts: `run.py <checkpoint>@<revision> <vocoder>@<revision> <requests.jsonl>` clones each line's
voice, {id, text, out, ref_audio, ref_text, seed, speed}, into out, a WAV at the vocoder's rate. The spoken length
follows the reference's syllable rate divided by speed."""

import ctypes.util
import json
import re
import sys
from pathlib import Path

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
    # The ViVoice repo ships its vocab as config.json; its README renames it to vocab.txt.
    vocab = checkpoint / "config.json"
    f5 = F5TTS(
        model="F5TTS_Base",
        ckpt_file=str(checkpoint / "model_last.pt"),
        vocab_file=str(vocab),
        vocoder_local_path=str(vocoder),
    )
    for line in Path(sys.argv[3]).read_text(encoding="utf-8").splitlines():
        req = json.loads(line)
        ref_file, ref_text = preprocess_ref_audio_text(req["ref_audio"], req["ref_text"])
        ref_seconds = sf.info(ref_file).duration
        # F5 sizes speech by UTF-8 bytes, which cuts short a text with fewer diacritics than its reference.
        gen_seconds = ref_seconds * syllables(req["text"]) / syllables(ref_text) / req["speed"]
        Path(req["out"]).parent.mkdir(parents=True, exist_ok=True)
        f5.infer(
            ref_file=req["ref_audio"],
            ref_text=req["ref_text"],
            gen_text=req["text"],
            seed=req["seed"],
            speed=req["speed"],
            fix_duration=ref_seconds + gen_seconds,
            file_wave=req["out"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
