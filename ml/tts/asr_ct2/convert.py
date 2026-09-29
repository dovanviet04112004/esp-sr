"""Convert the checker's pinned Whisper to CTranslate2 for run.py: `convert.py <repo>@<revision> <quantization> <out>`.
Runs in ml/tts/asr, which holds transformers and torch, with ctranslate2 added; loads in float16 to stay in memory."""

import sys

from ctranslate2.converters import TransformersConverter

repo, revision = sys.argv[1].split("@")
converter = TransformersConverter(
    repo,
    revision=revision,
    copy_files=["tokenizer.json", "preprocessor_config.json"],
    load_as_float16=True,
    low_cpu_mem_usage=True,
)
converter.convert(sys.argv[3], quantization=sys.argv[2], force=True)
