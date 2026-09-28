"""The command branch: a streaming acoustic model with CTC over the units E11-T3 picks (KEHOACH 3.12)."""

from srpipe.core.config import CONFIGS, ML_ROOT

CONFIG = CONFIGS / "models" / "command.yaml"
COMMANDS = ML_ROOT.parent / "contracts" / "commands" / "default_vi.json"
