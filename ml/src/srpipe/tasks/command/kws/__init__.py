"""The kws track: DS-CNN over one window of features a utterance, classes the learned commands, other and silence
(KEHOACH 3.12, ADR-0012)."""

from srpipe.core.config import CONFIGS, load_yaml
from srpipe.dsp.spec import pitch
from srpipe.tasks import command

CONFIG = CONFIGS / "models" / "command_kws.yaml"
OTHER, SILENCE = "other", "silence"


def classes(command_cfg: dict) -> list[str]:
    """Class names in output order: the learned command ids as default_vi.json lists them, then other and silence."""
    return [c["id"] for c in command.learned(command_cfg)] + [OTHER, SILENCE]


def n_dims(cfg: dict) -> int:
    """Features a hop: the mel bands of the feature config, then pitch's."""
    return load_yaml(CONFIGS / cfg["features"])["features"]["n_bands"] + pitch.N_FEATURES
