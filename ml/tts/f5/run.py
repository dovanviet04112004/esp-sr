"""F5-TTS for srpipe.tts: `run.py <repo>@<revision> <requests.jsonl>` clones each line's voice,
{id, text, out, ref_audio, ref_text, seed, speed}, into out, a WAV at the vocoder's rate."""

import json
import sys
from pathlib import Path

from f5_tts.api import F5TTS
from huggingface_hub import snapshot_download


def main() -> int:
    repo, revision = sys.argv[1].split("@")
    checkpoint = Path(snapshot_download(repo, revision=revision))
    ckpt, vocab = checkpoint / "model_last.pt", checkpoint / "vocab.txt"
    f5 = F5TTS(model="F5TTS_Base", ckpt_file=str(ckpt), vocab_file=str(vocab))
    for line in Path(sys.argv[2]).read_text(encoding="utf-8").splitlines():
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
