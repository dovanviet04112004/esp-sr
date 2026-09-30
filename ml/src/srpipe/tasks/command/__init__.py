"""The command branch: two tracks behind one contract, kws and ctc, and what they share (KEHOACH 3.12, ADR-0012)."""

import json

from srpipe.core.config import CONFIGS, ML_ROOT

CONFIG = CONFIGS / "models" / "command.yaml"
COMMANDS = ML_ROOT.parent / "contracts" / "commands" / "default_vi.json"


def learned(cfg: dict) -> list[dict]:
    """The commands of default_vi.json in file order, without the unseen ones of command.yaml."""
    commands = json.loads(COMMANDS.read_text(encoding="utf-8"))["commands"]
    unseen = set(cfg["unseen"])
    if missing := unseen - {c["id"] for c in commands}:
        raise ValueError(f"unseen commands {sorted(missing)} are not in {COMMANDS.name}")
    return [c for c in commands if c["id"] not in unseen]
