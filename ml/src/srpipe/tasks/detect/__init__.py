"""Object detection on the chip: a server's YOLO26 package down to an esp-dl graph (KEHOACH 3.18, ADR-0030)."""

from srpipe.core.config import CONFIGS

CONFIG = CONFIGS / "models" / "detect.yaml"
