"""The kws track: DS-CNN over one window of features a utterance, classes the learned commands, other and silence
(KEHOACH 3.12, ADR-0012)."""

from srpipe.core.config import CONFIGS, load_yaml
from srpipe.dsp.spec import pitch
from srpipe.tasks import command

CONFIG = CONFIGS / "models" / "command_kws.yaml"
OTHER, SILENCE = "other", "silence"


def classes(command_cfg: dict, only: list[str] | None = None) -> list[str]:
    """Class names in output order: the learned command ids as default_vi.json lists them, only those of only when
    given, then other and silence."""
    ids = [c["id"] for c in command.learned(command_cfg)]
    if only is not None and (unknown := set(only) - set(ids)):
        raise ValueError(f"{sorted(unknown)} are no learned commands")
    return [i for i in ids if only is None or i in only] + [OTHER, SILENCE]


def n_dims(cfg: dict) -> int:
    """Features a hop: the mel bands of the feature config, then pitch's."""
    return load_yaml(CONFIGS / cfg["features"])["features"]["n_bands"] + pitch.N_FEATURES
