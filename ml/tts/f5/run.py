"""F5-TTS for srpipe.tts: `run.py <checkpoint>@<revision> <vocoder>@<revision> <requests.jsonl>` clones each line's
voice, {id, text, out, ref_audio, ref_text, seed, speed}, into out, a WAV at the vocoder's rate."""

import ctypes.util
import json
import sys
from pathlib import Path

from f5_tts.api import F5TTS
from huggingface_hub import snapshot_download


def fetch(pinned: str) -> Path:
    repo, revision = pinned.split("@")
    return Path(snapshot_download(repo, revision=revision))


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
        Path(req["out"]).parent.mkdir(parents=True, exist_ok=True)
        f5.infer(
            ref_file=req["ref_audio"],
            ref_text=req["ref_text"],
            gen_text=req["text"],
            seed=req["seed"],
            speed=req["speed"],
            file_wave=req["out"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
