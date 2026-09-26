"""Frame-level scores of a voice activity detector against per-hop labels (KEHOACH 3.15, TONG QUAN V5.1.4)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class VadScores:
    """f1 of the speech class; miss is the share of speech hops called silence, false_alarm of silent hops called
    speech; the counts they come from."""

    f1: float
    miss: float
    false_alarm: float
    speech_hops: int
    silent_hops: int


def scores(decisions: np.ndarray, labels: np.ndarray) -> VadScores:
    """Score boolean decisions against boolean labels of the same hops."""
    d = np.asarray(decisions, dtype=bool)
    y = np.asarray(labels, dtype=bool)
    if d.shape != y.shape:
        raise ValueError(f"{d.shape} decisions against {y.shape} labels")
    hits = int(np.sum(d & y))
    speech, silent = int(np.sum(y)), int(np.sum(~y))
    called = int(np.sum(d))
    f1 = 2.0 * hits / (called + speech) if called + speech else 1.0
    miss = (speech - hits) / speech if speech else 0.0
    false_alarm = (called - hits) / silent if silent else 0.0
    return VadScores(f1, miss, false_alarm, speech, silent)


def with_hangover(raw: np.ndarray, hangover_hops: int) -> np.ndarray:
    """raw decisions held on for hangover_hops after each speech hop, as dsp_afe holds its vad."""
    r = np.asarray(raw, dtype=np.int64)
    return np.convolve(r, np.ones(hangover_hops + 1, dtype=np.int64))[: len(r)] > 0
