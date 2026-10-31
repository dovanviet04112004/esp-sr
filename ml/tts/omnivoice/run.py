"""KhanhTTS-OmniVoice for srpipe.tts, PyTorch on the GPU.

`run.py <model>@<rev> <dtype> voices` prints the designed voices as JSON; with <requests.jsonl> instead of `voices` it
reads each line {id, text, seed, out, speed?, voice | ref_audio, ref_text} into out, a 24 kHz 16-bit WAV. A voice is a
design: gender, age and pitch in OmniVoice's own words. A clone's prompt is built once per reference. The repo is
fetched at its pinned revision, without its training pickle, and read offline after that.
"""

import itertools
import json
import os
import sys
from pathlib import Path

import soundfile as sf
import torch
from huggingface_hub import constants, snapshot_download

BATCH = 4  # clips a call: 4 fill an RTX 3050 (tts_engines.md §6)
RATE = 24000
LANGUAGE = "vi"
GENDERS = ("male", "female")
AGES = ("teenager", "young adult", "middle-aged", "elderly")
PITCHES = ("low pitch", "moderate pitch", "high pitch")


def fetch(pinned: str) -> str:
    repo, revision = pinned.split("@")
    return snapshot_download(repo, revision=revision, ignore_patterns=["*.pkl"])


def designs() -> list[dict]:
    return [{"label": ", ".join(v), "id": ", ".join(v)} for v in itertools.product(GENDERS, AGES, PITCHES)]


def batches(requests: list[dict]):
    """Runs of BATCH requests of one kind, a design or a clone, as generate takes one kind a call."""
    for _, run in itertools.groupby(requests, key=lambda r: "ref_audio" in r):
        run = list(run)
        for k in range(0, len(run), BATCH):
            yield run[k : k + BATCH]


def main() -> int:
    path = fetch(sys.argv[1])
    if sys.argv[3] == "voices":
        print(json.dumps(designs()))
        return 0
    os.environ["HF_HUB_OFFLINE"] = "1"
    constants.HF_HUB_OFFLINE = True
    from omnivoice import OmniVoice

    model = OmniVoice.from_pretrained(path, device_map="cuda:0", dtype=getattr(torch, sys.argv[2]))
    requests = [json.loads(line) for line in Path(sys.argv[3]).read_text(encoding="utf-8").splitlines()]
    prompts = {}
    for batch in batches(requests):
        voices = {}
        if "ref_audio" in batch[0]:
            for r in batch:
                if r["ref_audio"] not in prompts:
                    prompts[r["ref_audio"]] = model.create_voice_clone_prompt(r["ref_audio"], r["ref_text"])
            voices["voice_clone_prompt"] = [prompts[r["ref_audio"]] for r in batch]
        else:
            voices["instruct"] = [r["voice"] for r in batch]
        # One draw a batch: a clip depends on the batch it is made in, as its fingerprint does on its request.
        torch.manual_seed(batch[0]["seed"])
        audio = model.generate(
            text=[r["text"] for r in batch],
            language=LANGUAGE,
            speed=[r.get("speed", 1.0) for r in batch],
            **voices,
        )
        for r, wav in zip(batch, audio, strict=True):
            Path(r["out"]).parent.mkdir(parents=True, exist_ok=True)
            sf.write(r["out"], wav, RATE, subtype="PCM_16")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
