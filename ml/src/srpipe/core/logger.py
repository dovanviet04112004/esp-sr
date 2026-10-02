"""One logging format for every script, to the console and optionally into a run directory."""

from __future__ import annotations

import logging
from numbers import Integral
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def row_line(row: dict[str, float]) -> str:
    """A row of a training history as key value pairs: counts as they are, measures to four significant digits."""
    return " ".join(f"{k} {v}" if isinstance(v, Integral) else f"{k} {v:.4g}" for k, v in row.items())


def get_logger(name: str, run_dir: Path | None = None) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(FORMAT))
    logger.addHandler(console)
    if run_dir is not None:
        file_handler = logging.FileHandler(run_dir / "log.txt", encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(FORMAT))
        logger.addHandler(file_handler)
    return logger
