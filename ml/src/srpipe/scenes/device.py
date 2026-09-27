"""The board simulation of KEHOACH 1.2 (E4-T8); so far the playback that measures it against the board.

python -m srpipe.scenes.device playback writes interim/playback/vivos_test.wav: VIVOS test read out loud from a
loudspeaker at a known place (host/plans/playback.tsv) gives recordings the simulation must reproduce, and the doa of
E8-T1 a known angle. One utterance per speaker in turn, each at the same level with a pause after it, until the
length asked; a JSON beside the WAV says what plays when.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from srpipe.core.audio_io import read_wav, write_wav
from srpipe.core.config import data_paths
from srpipe.generated import grid

SPEECH = Path("speech") / "vivos" / "test"
OUT = Path("playback") / "vivos_test.wav"
LEVEL_DBFS = -23.0
PEAK_MAX = 0.9
PAUSE_S = 0.6


def playback(speech_root: Path, seconds: float) -> tuple[np.ndarray, list[dict]]:
    """VIVOS test utterances taken round-robin over the speakers in sorted order, each scaled to LEVEL_DBFS RMS
    (less when its peak would pass PEAK_MAX), a pause after each, until seconds; the list gives each start."""
    prompts = speech_root / "prompts.txt"
    texts = dict(line.split(" ", 1) for line in prompts.read_text(encoding="utf-8").splitlines() if " " in line)
    by_speaker = [sorted(d.glob("*.wav")) for d in sorted(p for p in (speech_root / "waves").iterdir() if p.is_dir())]
    order = [files[k] for k in range(max(map(len, by_speaker))) for files in by_speaker if k < len(files)]
    pause = np.zeros(round(PAUSE_S * grid.SAMPLE_RATE_HZ))
    pieces, items, at = [], [], 0
    for path in order:
        if at >= seconds * grid.SAMPLE_RATE_HZ:
            break
        x = read_wav(path)[0][:, 0].astype(np.float64)
        gain = min(10 ** (LEVEL_DBFS / 20) / np.sqrt(np.mean(x**2)), PEAK_MAX / np.max(np.abs(x)))
        items.append({"utterance": path.stem, "text": texts.get(path.stem, ""), "start_s": at / grid.SAMPLE_RATE_HZ})
        pieces += [x * gain, pause]
        at += len(x) + len(pause)
    return np.concatenate(pieces), items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("playback", help="write interim/playback/vivos_test.wav and its JSON")
    make.add_argument("--seconds", type=float, default=60.0)
    args = parser.parse_args(argv)
    paths = data_paths()
    signal, items = playback(paths["raw"] / SPEECH, args.seconds)
    out = paths["interim"] / OUT
    write_wav(out, signal)
    meta = {"source": str(SPEECH), "level_dbfs": LEVEL_DBFS, "pause_s": PAUSE_S, "items": items}
    out.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{out}: {len(signal) / grid.SAMPLE_RATE_HZ:.1f} s, {len(items)} utterances")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
