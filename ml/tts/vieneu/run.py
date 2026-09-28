"""VieNeu-TTS v3 Turbo, fp32 ONNX on the CPU, for srpipe.tts.

`run.py <root> <model>@<rev> <codec>@<rev> voices` prints the preset voices as JSON; with <requests.jsonl> instead of
`voices` it synthesises each line {id, text, out, voice | ref_audio} into out, a 48 kHz WAV. Both repos are fetched into
<root>/hub as plain files, since ONNX Runtime refuses external weights reached through symlinks, checked against their
pinned revisions, and read offline after that, so VieNeu cannot pull a newer model on its own.
"""

import json
import os
import sys
from pathlib import Path

os.environ["HF_HUB_CACHE"] = str(Path(sys.argv[1]) / "hub")
os.environ["HF_HUB_DISABLE_SYMLINKS"] = "1"

from huggingface_hub import constants, snapshot_download

MODEL_FILES = [
    "config.json",
    "denoiser.onnx",
    "speaker_encoder.onnx",
    "special_tokens_map.json",
    "tokenizer*.json",
    "voices*.json",
    "onnx_update/*",
]


def fetch(pinned: str, patterns: list[str] | None) -> str:
    """The repo id after fetching its main branch and checking main is the pinned revision."""
    repo, revision = pinned.split("@")
    got = Path(snapshot_download(repo, revision="main", allow_patterns=patterns)).name
    if got != revision:
        raise SystemExit(f"{repo}: main is {got}, pinned {revision}; update configs/common/tts.yaml deliberately")
    return repo


def main() -> int:
    model = fetch(sys.argv[2], MODEL_FILES)
    fetch(sys.argv[3], None)
    constants.HF_HUB_OFFLINE = True
    from vieneu import Vieneu

    tts = Vieneu(backbone_repo=model, precision="fp32")
    if sys.argv[4] == "voices":
        print(json.dumps([{"label": label, "id": voice_id} for label, voice_id in tts.list_preset_voices()]))
        return 0
    for line in Path(sys.argv[4]).read_text(encoding="utf-8").splitlines():
        req = json.loads(line)
        if "ref_audio" in req:
            audio = tts.infer(req["text"], ref_audio=req["ref_audio"], denoise=True)
        else:
            audio = tts.infer(req["text"], voice=req["voice"])
        Path(req["out"]).parent.mkdir(parents=True, exist_ok=True)
        tts.save(audio, req["out"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
