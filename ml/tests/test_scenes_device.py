"""The playback of srpipe.scenes.device: speakers in turn, one level, starts that match the samples."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from srpipe.core.audio_io import write_wav
from srpipe.generated import grid
from srpipe.scenes import device


def fake_vivos(root: Path) -> Path:
    rng = np.random.default_rng(3)
    lines = []
    for spk in ("SPK02", "SPK01"):
        for n in (2, 1):
            utterance = f"{spk}_R00{n}"
            write_wav(root / "waves" / spk / f"{utterance}.wav", 0.01 * n * rng.standard_normal(grid.SAMPLE_RATE_HZ))
            lines.append(f"{utterance} CÂU {n}")
    (root / "prompts.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def test_playback_takes_speakers_in_turn_at_one_level(tmp_path: Path) -> None:
    signal, items = device.playback(fake_vivos(tmp_path), seconds=10.0)
    assert [i["utterance"] for i in items] == ["SPK01_R001", "SPK02_R001", "SPK01_R002", "SPK02_R002"]
    assert items[0]["text"] == "CÂU 1"
    for item in items:
        start = round(item["start_s"] * grid.SAMPLE_RATE_HZ)
        x = signal[start : start + grid.SAMPLE_RATE_HZ]
        assert abs(10 * np.log10(np.mean(x**2)) - device.LEVEL_DBFS) < 0.1


def test_playback_stops_once_long_enough(tmp_path: Path) -> None:
    _, items = device.playback(fake_vivos(tmp_path), seconds=1.0)
    assert len(items) == 1
