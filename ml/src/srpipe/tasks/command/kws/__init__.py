"""The kws track: DS-CNN over one window of features a utterance, classes the learned commands, other and silence
(KEHOACH 3.12, ADR-0012)."""

from srpipe.core.config import CONFIGS, load_device
from srpipe.dsp.spec import pitch
from srpipe.tasks import command

CONFIG = CONFIGS / "models" / "command_kws.yaml"
OTHER, SILENCE = "other", "silence"


def classes(cfg: dict, command_cfg: dict) -> list[str]:
    """Class names in output order: a Speech Commands pilot's keywords, else the learned command ids as default_vi.json
    lists them, only those of commands when it names some; then other and silence."""
    if cfg["speech_commands"]:
        keywords = cfg["speech_commands"]["keywords"]
        if wrong := [w for w in keywords if not isinstance(w, str)]:
            raise ValueError(f"keywords {wrong} are no strings: YAML reads yes, no, on and off unquoted as booleans")
        return [*keywords, OTHER, SILENCE]
    ids, only = [c["id"] for c in command.learned(command_cfg)], cfg["commands"]
    if only is not None and (unknown := set(only) - set(ids)):
        raise ValueError(f"{sorted(unknown)} are no learned commands")
    return [i for i in ids if only is None or i in only] + [OTHER, SILENCE]


def n_dims(cfg: dict) -> int:
    """Features a hop: the mel bands of the feature config, then pitch's."""
    return load_device(cfg["features"])["features"]["n_bands"] + pitch.N_FEATURES
