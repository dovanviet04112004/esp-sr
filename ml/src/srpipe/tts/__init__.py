"""Synthetic Vietnamese speech for every branch: the desktop TTS engines and the ASR checker of configs/common/tts.yaml,
each in its own pinned uv project under ml/tts/<name>/ (KEHOACH 1.2, 3.13, 4.4)."""

from srpipe.core.config import CONFIGS, ML_ROOT

CONFIG = CONFIGS / "common" / "tts.yaml"
PROJECTS = ML_ROOT / "tts"
