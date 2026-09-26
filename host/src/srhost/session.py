"""Record one labelled session into raw/device/<board>/<session>/ and a row of the device manifest (KEHOACH 4.4.1).

The session directory holds one WAV per channel, session.json and gaps.txt; the manifest row repeats
the labels plus duration_s and the sha256 of the first WAV. seq_gaps counts the lines of gaps.txt.
Run: uv run python -m srhost.session --kind cmd --spk spk_001 --consent C001 --room lab --fw 0.1.0+87b0337
--pcm-shift 16 --prompt "bật đèn" --duration-s 30
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import re
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from srhost.config import ConfigError, RecordConfig, record_config
from srhost.generated import grid
from srhost.stream_rx import Recorder, StreamServer

KINDS = ("wake", "cmd", "neg", "noise", "probe")
SPEAKER_KINDS = ("wake", "cmd", "neg")
SPK_PATTERN = re.compile(r"^spk_\d{3}$")
ROOM_PATTERN = re.compile(r"^[a-z0-9]+$")
PCM_SHIFT_MIN, PCM_SHIFT_MAX = 8, 16
DOA_MIN_DEG, DOA_MAX_DEG = 0, 180
SESSION_DIGITS = 3
MANIFEST_COLUMNS = (
    "session",
    "board",
    "fw",
    "grid_hash",
    "pcm_shift",
    "kind",
    "spk",
    "consent",
    "room",
    "distance_cm",
    "doa_deg",
    "prompt",
    "start_utc",
    "seq_gaps",
    "duration_s",
    "sha256",
)

log = logging.getLogger(__name__)


class LabelError(ValueError):
    """The labels of a session break a rule of KEHOACH 4.4.1 or 1.4."""


@dataclass(frozen=True)
class Labels:
    """What the operator declares about a session; the board and the stream give the rest."""

    kind: str
    room: str
    fw: str
    pcm_shift: int
    spk: str | None = None
    consent: str | None = None
    distance_cm: int | None = None
    doa_deg: int | None = None
    prompt: str | None = None

    def check(self) -> None:
        if self.kind not in KINDS:
            raise LabelError(f"kind {self.kind!r} is not one of {KINDS}")
        if not ROOM_PATTERN.match(self.room):
            raise LabelError(f"room {self.room!r} must be lower case letters and digits, it names the session")
        if not self.fw:
            raise LabelError("fw is PROJECT_VER plus commit, as the board logs it at boot")
        if not PCM_SHIFT_MIN <= self.pcm_shift <= PCM_SHIFT_MAX:
            raise LabelError(f"pcm_shift {self.pcm_shift} is outside {PCM_SHIFT_MIN}..{PCM_SHIFT_MAX} (KEHOACH 6.2)")
        if self.kind in SPEAKER_KINDS and (self.spk is None or self.consent is None):
            raise LabelError(f"a {self.kind} session needs --spk and --consent: a person speaks (KEHOACH 1.4)")
        if self.kind == "noise" and (self.spk is not None or self.consent is not None):
            raise LabelError("a noise session has no speaker, so no --spk and no --consent")
        if self.spk is not None and not SPK_PATTERN.match(self.spk):
            raise LabelError(f"spk {self.spk!r} is not spk_NNN; the real name stays outside the repo")
        if self.doa_deg is not None and not DOA_MIN_DEG <= self.doa_deg <= DOA_MAX_DEG:
            raise LabelError(f"doa_deg {self.doa_deg} is outside {DOA_MIN_DEG}..{DOA_MAX_DEG}")
        if self.distance_cm is not None and self.distance_cm <= 0:
            raise LabelError(f"distance_cm {self.distance_cm} must be positive")


def next_session_name(board_dir: Path, day: str, room: str) -> str:
    """<yyyymmdd>_<room>_<nnn> with the first nnn not taken in board_dir."""
    pattern = re.compile(rf"^{day}_{re.escape(room)}_(\d{{{SESSION_DIGITS}}})$")
    taken = [int(m.group(1)) for p in board_dir.glob(f"{day}_{room}_*") if (m := pattern.match(p.name))]
    return f"{day}_{room}_{max(taken, default=0) + 1:0{SESSION_DIGITS}d}"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def append_manifest(manifest: Path, row: dict) -> None:
    fresh = not manifest.exists()
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("a", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=MANIFEST_COLUMNS, lineterminator="\n")
        if fresh:
            writer.writeheader()
        writer.writerow({k: "" if row[k] is None else row[k] for k in MANIFEST_COLUMNS})


def record(
    labels: Labels,
    cfg: RecordConfig,
    duration_s: float | None,
    stop: threading.Event | None = None,
    server: StreamServer | None = None,
    manifest: Path | None = None,
    day: str | None = None,
) -> Path:
    """Record until duration_s of audio or stop; write the session and its manifest row, return its directory."""
    labels.check()
    board_dir = cfg.data_root / "raw" / "device" / cfg.board
    board_dir.mkdir(parents=True, exist_ok=True)
    day = day or datetime.now(UTC).strftime("%Y%m%d")
    server = server or StreamServer(cfg.stream_bind, cfg.stream_port)
    out = board_dir / next_session_name(board_dir, day, labels.room)
    out.mkdir()
    recorder = Recorder(out)
    log.info("recording %s on port %d", out.name, server.port)
    try:
        server.run(recorder, stop or threading.Event(), duration_s)
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
        summary = recorder.close()
    if summary.frames == 0:
        out.rmdir()
        raise RuntimeError("no frame arrived; is the board streaming to this port?")
    meta = {
        "session": out.name,
        "board": cfg.board,
        "fw": labels.fw,
        "grid_hash": f"0x{grid.GRID_HASH:08x}",
        "pcm_shift": labels.pcm_shift,
        "kind": labels.kind,
        "spk": labels.spk,
        "consent": labels.consent,
        "room": labels.room,
        "distance_cm": labels.distance_cm,
        "doa_deg": labels.doa_deg,
        "prompt": labels.prompt,
        "start_utc": summary.start_utc,
        "seq_gaps": len(summary.gaps),
    }
    (out / "session.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    first_wav = out / f"{summary.channels[0]}.wav"
    row = {**meta, "duration_s": round(summary.duration_s, 3), "sha256": sha256_of(first_wav)}
    append_manifest(manifest or cfg.manifest, row)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kind", required=True, choices=KINDS)
    parser.add_argument("--room", required=True)
    parser.add_argument("--fw", required=True, help="PROJECT_VER plus commit, from the board's boot log")
    parser.add_argument("--pcm-shift", type=int, required=True, help="calib/pcm_shift, from the board's boot log")
    parser.add_argument("--spk")
    parser.add_argument("--consent")
    parser.add_argument("--distance-cm", type=int)
    parser.add_argument("--doa-deg", type=int)
    parser.add_argument("--prompt")
    parser.add_argument("--duration-s", type=float, help="stop after this much audio; Ctrl-C otherwise")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    labels = Labels(
        kind=args.kind,
        room=args.room,
        fw=args.fw,
        pcm_shift=args.pcm_shift,
        spk=args.spk,
        consent=args.consent,
        distance_cm=args.distance_cm,
        doa_deg=args.doa_deg,
        prompt=args.prompt,
    )
    try:
        out = record(labels, record_config(), args.duration_s)
    except (ConfigError, LabelError, RuntimeError, OSError) as err:
        print(f"session: {err}", file=sys.stderr)
        return 2
    print(f"session written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
