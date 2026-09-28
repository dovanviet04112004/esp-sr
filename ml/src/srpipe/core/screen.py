"""Screening of every clip under raw/, ahead of any use (KEHOACH 1.2, E11-T16).

measure decodes each clip of configs/common/screen.yaml once into interim/screen/measures/<kind>/<name>.tsv, again
only when what measures it changes; judge rejects a clip by the first rule it breaks into interim/screen/rejects.tsv,
read back by rejected(); audit has the TTS checker hear clips on both sides of each speech threshold.
Run: python -m srpipe.core.screen {measure,judge,audit} [corpus ...]"""

from __future__ import annotations

import argparse
import csv
import hashlib
import inspect
import itertools
import json
import time
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from srpipe.core import audio_io, corpus
from srpipe.core.config import CONFIGS, data_paths, load_yaml
from srpipe.generated import lang_vi
from srpipe.lang import normalize
from srpipe.tts import CONFIG as TTS_CONFIG
from srpipe.tts import engines

CONFIG = CONFIGS / "common" / "screen.yaml"
FIELDS = (
    "item",
    "speaker",
    "rate_hz",
    "seconds",
    "rms_dbfs",
    "peak_dbfs",
    "clipped",
    "quiet_dbfs",
    "loud_dbfs",
    "active_seconds",
    "syllables",
    "refused",
    "pcm",
    "error",
)
TEXT_FIELDS = ("item", "speaker", "pcm", "error")
REJECT_FIELDS = ("item", "corpus", "reason", "seconds")
SECONDS_PER_HOUR = 3600.0


def measure(x: np.ndarray, rate: int, text: str | None, m: dict) -> dict:
    """Levels of one decoded mono clip in dB of full scale, the time within reach of its loudest frame, and the
    syllables of its text with the pieces normalize refused; a clip without text has neither."""
    frame = max(1, round(m["frame_seconds"] * rate))
    n = max(1, len(x) // frame)
    with np.errstate(divide="ignore"):
        frames = 10 * np.log10(np.mean(np.square(x[: n * frame] if len(x) >= frame else x).reshape(n, -1), axis=1))
        rms, peak = 10 * np.log10(np.mean(np.square(x))), 20 * np.log10(np.max(np.abs(x)))
    quiet, loud = np.percentile(frames, [m["quiet_percentile"], m["loud_percentile"]], method="nearest")
    active = np.count_nonzero(frames >= frames.max() - m["active_below_peak_db"]) * frame / rate
    syllables, refused = corpus.read_text(text) if text is not None else (None, None)
    return {
        "rate_hz": rate,
        "seconds": len(x) / rate,
        "rms_dbfs": float(rms),
        "peak_dbfs": float(peak),
        "clipped": float(np.mean(np.abs(x) >= m["full_scale"])),
        "quiet_dbfs": float(quiet),
        "loud_dbfs": float(loud),
        "active_seconds": min(active, len(x) / rate),
        "syllables": None if syllables is None else len(syllables),
        "refused": refused,
        "pcm": hashlib.blake2b(x.astype(np.float32).tobytes(), digest_size=16).hexdigest(),
    }


def measure_batch(task: tuple[Path, list[corpus.Clip], dict]) -> list[dict]:
    """One row per clip of a batch; a clip that does not decode, or decodes empty, keeps only its error."""
    raw, batch, m = task
    reader = audio_io.ItemReader(raw)
    rows = []
    for clip in batch:
        row = {"item": clip.item, "speaker": clip.speaker}
        # Whatever a decoder raises, the clip fails the decode rule; the class name says how.
        try:
            x, rate = reader.native(clip.item)
            row |= measure(x, rate, clip.text, m) if len(x) else {"error": "empty"}
        except Exception as error:
            row["error"] = type(error).__name__
        rows.append(row)
    return rows


# Read at import: the code this process runs, even when the files on disk change while it measures.
MEASURE_CODE = "".join(inspect.getsource(f) for f in (measure, measure_batch)).encode()
MEASURE_CODE += b"".join(Path(mod.__file__).read_bytes() for mod in (corpus, audio_io, normalize, lang_vi))


def batches(raw: Path, clips: list[corpus.Clip], size: int) -> list[list[corpus.Clip]]:
    """Runs of consecutive clips: one parquet row group each, so a group is read once, or size files."""
    starts: dict[str, np.ndarray] = {}

    def key(k: int, clip: corpus.Clip) -> tuple[str, int]:
        name, _, row = clip.item.partition("#")
        if not row:
            return "", k // size
        if name not in starts:
            meta = pq.ParquetFile(raw / name).metadata
            starts[name] = np.cumsum([0] + [meta.row_group(g).num_rows for g in range(meta.num_row_groups)])
        return name, int(np.searchsorted(starts[name], int(row), side="right")) - 1

    groups = itertools.groupby(enumerate(clips), key=lambda kc: key(*kc))
    return [[clip for _, clip in run] for _, run in groups]


def measured_by(spec: dict, m: dict) -> str:
    """What a corpus's measures come from: its layout, the measure settings and every piece of code on the way."""
    settings = json.dumps({"spec": spec, "measure": m}, sort_keys=True)
    return hashlib.sha256(settings.encode() + MEASURE_CODE).hexdigest()


def write_tsv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows({k: "" if row.get(k) is None else row[k] for k in fields} for row in rows)


def read_measures(path: Path) -> list[dict]:
    """Rows of a measures file, numbers parsed and blanks as None."""
    with path.open(encoding="utf-8", newline="") as f:
        return [
            {k: (v if k in TEXT_FIELDS else float(v)) if v != "" else None for k, v in row.items()}
            for row in csv.DictReader(f, delimiter="\t")
        ]


def measure_corpus(raw: Path, out: Path, kind: str, name: str, cfg: dict, pool) -> bool:
    """Measure raw/<kind>/<name> into out/<kind>/<name>.tsv unless it is already measured the same way; True when
    measured now."""
    spec, m = cfg["corpora"][kind][name], cfg["measure"]
    tsv, stamp = out / kind / f"{name}.tsv", out / kind / f"{name}.json"
    by = measured_by(spec, m)
    if tsv.exists() and stamp.exists() and json.loads(stamp.read_text())["measured_by"] == by:
        return False
    clips = corpus.clips(raw, kind, name, spec)
    tasks = [(raw, batch, m) for batch in batches(raw, clips, m["batch_clips"])]
    rows = [row for part in pool.imap(measure_batch, tasks) for row in part]
    write_tsv(tsv, FIELDS, rows)
    stamp.write_text(json.dumps({"measured_by": by, "clips": len(rows)}) + "\n")
    return True


def span_db(row: dict) -> float:
    return row["loud_dbfs"] - row["quiet_dbfs"]


def syllable_rate(row: dict) -> float:
    return row["syllables"] / row["active_seconds"]


def broken(row: dict, rules: dict) -> str | None:
    """The first rule of KEHOACH 1.2 a measured clip breaks, duplicate aside."""
    if row["error"] is not None:
        return "decode"
    if row["rms_dbfs"] < rules["silent_rms_dbfs"]:
        return "silent"
    if rules.get("text") and (not row["syllables"] or row["refused"]):
        return "text"
    if "clipped_fraction" in rules and row["clipped"] > rules["clipped_fraction"]:
        return "clipped"
    if "noisy_span_db" in rules and span_db(row) < rules["noisy_span_db"]:
        return "noisy"
    if "syllables_per_second" in rules:
        low, high = rules["syllables_per_second"]
        if not low <= syllable_rate(row) <= high:
            return "rate"
    return None


def judge(cfg: dict, out: Path) -> list[dict]:
    """Every rejected clip {item, corpus, reason, seconds}, corpora in config order; a clip whose PCM repeats one
    kept before it is a duplicate."""
    kept: set[str] = set()
    rejects = []
    for kind, corpora in cfg["corpora"].items():
        for name in corpora:
            where = f"{kind}/{name}"
            for row in read_measures(out / kind / f"{name}.tsv"):
                reason = broken(row, cfg["judge"][kind]) or ("duplicate" if row["pcm"] in kept else None)
                if reason is None:
                    kept.add(row["pcm"])
                    continue
                rejects.append({"item": row["item"], "corpus": where, "reason": reason, "seconds": row["seconds"]})
    return rejects


def summary(cfg: dict, out: Path, rejects: list[dict]) -> list[str]:
    """Markdown rows: per corpus the clips and hours measured, and the clips and hours each reason removed."""
    reasons = ("decode", "silent", "text", "clipped", "noisy", "rate", "duplicate")
    removed: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rejects:
        removed[r["corpus"]][r["reason"]].append(r["seconds"] or 0.0)
    lines = ["| Kho | Mẩu | Giờ | " + " | ".join(reasons) + " | Còn lại |", "|---" * (len(reasons) + 4) + "|"]
    for kind, corpora in cfg["corpora"].items():
        for name in corpora:
            rows = read_measures(out / kind / f"{name}.tsv")
            hours = sum(r["seconds"] or 0.0 for r in rows) / SECONDS_PER_HOUR
            gone = removed[f"{kind}/{name}"]
            cells = [f"{len(gone[k])} ({sum(gone[k]) / SECONDS_PER_HOUR:.2f} h)" if gone[k] else "" for k in reasons]
            left = hours - sum(map(sum, gone.values())) / SECONDS_PER_HOUR
            lines.append(f"| {kind}/{name} | {len(rows)} | {hours:.2f} | " + " | ".join(cells) + f" | {left:.2f} h |")
    return lines


MEASURES = {"rms_dbfs": lambda r: r["rms_dbfs"], "clipped": lambda r: r["clipped"], "span_db": span_db}
MEASURES |= {"syllable_rate": syllable_rate}


def edits(heard: list[str], said: list[str]) -> int:
    """Syllables substituted, dropped or added between two readings."""
    row = list(range(len(said) + 1))
    for i, h in enumerate(heard, 1):
        diagonal, row[0] = row[0], i
        for j, s in enumerate(said, 1):
            diagonal, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, diagonal + (h != s))
    return row[-1]


def audit_picks(cfg: dict, rows: list[dict]) -> dict[tuple[str, int], tuple[int, list[dict]]]:
    """Per measure and bin of audit.bins, how many speech clips fall in it and per_bin of them drawn with the seed.
    The clips are those that decode and have text, and, for every measure but the level, are not silent, so a bin
    shows what its own measure does."""
    audit, rules = cfg["audit"], cfg["judge"]["speech"]
    rng = np.random.default_rng(audit["seed"])
    base = [r for r in rows if r["error"] is None and r["syllables"] and not r["refused"]]
    audible = [r for r in base if r["rms_dbfs"] >= rules["silent_rms_dbfs"]]
    picks = {}
    for name, edges in audit["bins"].items():
        pool = base if name == "rms_dbfs" else audible
        values = np.array([MEASURES[name](r) for r in pool])
        for b, (low, high) in enumerate(itertools.pairwise(edges)):
            inside = np.flatnonzero((values >= low) & (values < high))
            drawn = rng.choice(inside, min(audit["per_bin"], len(inside)), replace=False)
            picks[(name, b)] = (len(inside), [pool[k] for k in sorted(drawn)])
    return picks


def item_order(item: str) -> tuple[str, int]:
    """Files by name, parquet rows by number, so rows of one row group sit together."""
    name, _, row = item.partition("#")
    return name, int(row or 0)


def audit_wav(work: Path, item: str) -> Path:
    return work / f"{hashlib.blake2b(item.encode(), digest_size=12).hexdigest()}.wav"


def write_audit_waves(task: tuple[Path, Path, list[corpus.Clip]]) -> None:
    """Each clip of a batch at the grid rate, for the checker to hear."""
    raw, work, batch = task
    reader = audio_io.ItemReader(raw)
    for clip in batch:
        audio_io.write_wav(audit_wav(work, clip.item), np.clip(reader.read(clip.item), -1.0, 1.0))


def audit(cfg: dict, paths: dict) -> list[str]:
    """Clips drawn from bins of each speech measure, heard by the TTS checker: markdown rows of how many clips each
    bin holds and the share of the drawn ones the checker gets unusable_errors or more of the syllables wrong, for
    docs/measurements/data_screen.md. Each clip's wave is kept under its item's hash, so a rerun reads none again."""
    measures, work = paths["interim"] / "screen" / "measures", paths["interim"] / "screen" / "audit"
    rows = [r for name in cfg["corpora"]["speech"] for r in read_measures(measures / "speech" / f"{name}.tsv")]
    picks = audit_picks(cfg, rows)
    wanted = sorted({r["item"] for _, group in picks.values() for r in group}, key=item_order)
    texts = {
        c.item: c.text
        for name, spec in cfg["corpora"]["speech"].items()
        for c in corpus.clips(paths["raw"], "speech", name, spec)
        if c.item in wanted
    }
    missing = [corpus.Clip(item) for item in wanted if not audit_wav(work, item).exists()]
    tasks = [(paths["raw"], work, batch) for batch in batches(paths["raw"], missing, cfg["measure"]["batch_clips"])]
    with Pool(cfg["processes"]) as pool:
        pool.map(write_audit_waves, tasks)
    asked = [{"id": i, "wav": str(audit_wav(work, i)), "targets": [" ".join(corpus.words(texts[i]))]} for i in wanted]
    heard = engines.hear(asked, load_yaml(TTS_CONFIG), work, paths["cache"])
    said = {item: corpus.words(texts[item]) for item in wanted}
    wrong = {item: edits(corpus.words(heard[item]["text"]), said[item]) / len(said[item]) for item in wanted}
    unusable = cfg["audit"]["unusable_errors"]
    lines = ["| Thước | Ô | Mẩu trong ô | Nghe | Âm tiết sai, trung vị | Không dùng được |", "|---" * 6 + "|"]
    for (name, b), (count, group) in picks.items():
        low, high = cfg["audit"]["bins"][name][b : b + 2]
        errors = np.array([wrong[r["item"]] for r in group])
        middle = f"{np.median(errors):.0%} | {np.mean(errors >= unusable):.0%}" if len(group) else " | "
        lines.append(f"| {name} | [{low:g}, {high:g}) | {count} | {len(group)} | {middle} |")
    return lines


def kept(raw: Path, files: list[Path], rejected: set[str] | dict[str, str]) -> list[Path]:
    """The files under raw whose item screening did not reject, in their order."""
    return [f for f in files if str(f.relative_to(raw)) not in rejected]


def kept_clips(cfg: dict, paths: dict[str, Path], kind: str) -> list[corpus.Clip]:
    """Every clip of the kind's corpora that the last judge run kept, corpora in config order."""
    rejects = rejected(paths["interim"])
    listed = (corpus.clips(paths["raw"], kind, name, spec) for name, spec in cfg["corpora"][kind].items())
    return [c for clips in listed for c in clips if c.item not in rejects]


def lengths(cfg: dict, paths: dict[str, Path], kind: str) -> dict[str, float]:
    """Seconds of every measured clip of the kind's corpora, by item."""
    measures = paths["interim"] / "screen" / "measures" / kind
    return {r["item"]: r["seconds"] for name in cfg["corpora"][kind] for r in read_measures(measures / f"{name}.tsv")}


def rejected(interim: Path) -> dict[str, str]:
    """Every rejected item and its reason, from the last judge run."""
    listing = interim / "screen" / "rejects.tsv"
    if not listing.exists():
        raise FileNotFoundError(f"{listing} is missing: run make screen first")
    with listing.open(encoding="utf-8", newline="") as f:
        return {row["item"]: row["reason"] for row in csv.DictReader(f, delimiter="\t")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["measure", "judge", "audit"])
    parser.add_argument("names", nargs="*", help="corpora to measure; all of screen.yaml when none")
    args = parser.parse_args(argv)
    cfg, paths = load_yaml(CONFIG), data_paths()
    out = paths["interim"] / "screen" / "measures"
    if args.step == "measure":
        with Pool(cfg["processes"]) as pool:
            for kind, corpora in cfg["corpora"].items():
                for name in corpora:
                    if args.names and name not in args.names:
                        continue
                    start = time.monotonic()
                    fresh = measure_corpus(paths["raw"], out, kind, name, cfg, pool)
                    took = time.monotonic() - start
                    print(f"{kind}/{name}: {'measured' if fresh else 'already measured'} in {took:.0f} s", flush=True)
        return 0
    if args.step == "audit":
        print("\n".join(audit(cfg, paths)))
        return 0
    rejects = judge(cfg, out)
    write_tsv(paths["interim"] / "screen" / "rejects.tsv", REJECT_FIELDS, rejects)
    print("\n".join(summary(cfg, out, rejects)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
