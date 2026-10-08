"""The data of the ns branch (KEHOACH 3.9, E9-T3, ADR-0014): split, decoded pools, the example mixer, held sets, pilot.

clean decodes every speech candidate once for its bandwidth; split writes data/splits/ns/<version>; pool decodes each
role's speech, tone and babble into interim/ns/<version>/<role>/ and indexes its noise, read in place from raw/; sets
mixes val and test once into processed/ns/<version>/; pilot writes examples to hear. Mixer draws, reads and filters
examples alike; gpu_mix filters training batches. Run: python -m srpipe.tasks.ns.data {clean,split,pool,sets,pilot}
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import multiprocessing
import re
import threading
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
import yaml
from scipy import signal

from srpipe.core import screen, splits
from srpipe.core.audio_io import INT16_SCALE, ItemReader, ramped, to_int16, write_wav
from srpipe.core.config import CONFIGS, data_paths, load_device, load_yaml
from srpipe.dsp.spec.stft import Istft
from srpipe.generated import grid
from srpipe.scenes import device, refs, room
from srpipe.tasks import ns

FS, HOP = grid.SAMPLE_RATE_HZ, grid.HOP_SAMPLES
ROLES = ("train", "val", "test")
HELD = ("val", "test")
BABBLE_STREAM, PACK_STREAM, KIND_STREAM, EXAMPLE_STREAM, DNSMOS_STREAM = 1, 2, 3, 4, 5
SPEECH = "speech/"
CUTOFF_FIELDS = ("item", "cutoff_hz", "seconds")
NOISE_FIELDS = ("item", "pool", "group", "seconds")
POWER_TINY = 1e-30
PILOT_PER_CLASS = 4
EPOCHS_HELD = 2  # a loader worker crosses one epoch boundary at a time


def configs() -> tuple[dict, dict]:
    """ns.yaml and the board simulation it points at."""
    cfg = load_yaml(ns.CONFIG)
    return cfg, load_device(cfg["device"])


def split_dir(paths: dict, cfg: dict) -> Path:
    return paths["splits"] / "ns" / cfg["version"]


def pool_dir(paths: dict, cfg: dict, role: str) -> Path:
    return paths["interim"] / "ns" / cfg["version"] / role


def set_dir(paths: dict, cfg: dict, role: str) -> Path:
    return paths["processed"] / "ns" / cfg["version"] / role


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 24), b""):
            digest.update(block)
    return digest.hexdigest()


def write_tsv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        out = csv.DictWriter(f, fields, delimiter="\t")
        out.writeheader()
        out.writerows(rows)


def read_tsv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def screened(paths: dict, kind: str) -> dict[str, dict]:
    """Screening's measures of every clip of the kind that the last judge run kept, by item."""
    rejected = screen.rejected(paths["interim"])
    rows = {}
    for f in sorted((paths["interim"] / "screen" / "measures" / kind).glob("*.tsv")):
        rows |= {r["item"]: r for r in screen.read_measures(f) if r["item"] not in rejected}
    return rows


def held_rows(cfg: dict, paths: dict) -> dict[str, list[splits.Row]]:
    """The val and test rows of the split ns takes its held sets from, whose speakers never train ns."""
    base = paths["splits"] / cfg["split"]["from"]
    return {role: splits.read_split(base / f"{role}.txt") for role in HELD}


def train_rule(cfg: dict, item: str) -> dict | None:
    return next((rule for prefix, rule in cfg["split"]["train"].items() if item.startswith(prefix)), None)


def clean_enough(m: dict, min_span_db: float, max_quiet_dbfs: float = math.inf) -> bool:
    """Loud minus quiet frames of screening over min_span_db and the quiet frames under max_quiet_dbfs: a target with
    noise under its speech teaches the net to keep noise."""
    if m.get("quiet_dbfs") is None or m.get("loud_dbfs") is None:
        return False
    return screen.span_db(m) >= min_span_db and m["quiet_dbfs"] <= max_quiet_dbfs


def speech_candidates(cfg: dict, paths: dict) -> dict[str, float]:
    """Seconds of every speech clip a role could take: train clips meeting their corpus's rule, held rows meeting the
    evaluation span; bandwidth is judged later, from clean."""
    measures = screened(paths, "speech")
    out = {}
    for item, m in measures.items():
        rule = train_rule(cfg, item)
        if rule and clean_enough(m, rule["min_span_db"], rule.get("max_quiet_dbfs", math.inf)):
            out[item] = m["seconds"]
    for rows in held_rows(cfg, paths).values():
        for row in rows:
            m = measures.get(row.item)
            if m and clean_enough(m, cfg["split"]["eval_min_span_db"]):
                out[row.item] = m["seconds"]
    return out


def cutoff_hz(x: np.ndarray, spec: dict) -> float:
    """The highest frequency whose smoothed Welch level is within cutoff_within_db of the speech band's median."""
    freqs, power = signal.welch(x, FS, nperseg=grid.FFT_SIZE)
    kernel = np.ones(spec["smooth_bins"]) / spec["smooth_bins"]
    level = 10.0 * np.log10(np.convolve(power, kernel, mode="same") + POWER_TINY)
    low, high = spec["speech_band_hz"]
    reference = float(np.median(level[(freqs >= low) & (freqs <= high)]))
    return float(freqs[np.nonzero(level >= reference - spec["cutoff_within_db"])[0].max()])


def source_of(item: str) -> str:
    """The file an item is read from: a parquet's row groups are read once for all its rows."""
    return item.split("#", 1)[0] if "#" in item else str(Path(item).parent)


def chunks(items: list[str], size: int) -> list[list[str]]:
    """Items in source order, cut into runs of at most size that never split a parquet file's rows needlessly."""
    by_source = defaultdict(list)
    for item in items:
        by_source[source_of(item)].append(item)
    out, run = [], []
    for source in sorted(by_source):
        for item in by_source[source]:
            run.append(item)
            if len(run) == size:
                out.append(run)
                run = []
    return out + ([run] if run else [])


def _cutoffs(task: tuple[Path, list[str], dict]) -> list[dict]:
    raw, items, spec = task
    reader = ItemReader(raw)
    rows = []
    for item in items:
        x = reader.read(item)
        rows.append({"item": item, "cutoff_hz": cutoff_hz(x, spec), "seconds": len(x) / FS})
    return rows


def clean(cfg: dict, paths: dict) -> Path:
    """interim/ns/clean/cutoff.tsv: the bandwidth of every speech candidate, measured once; a run that finds every
    candidate there does nothing."""
    out = paths["interim"] / "ns" / "clean" / "cutoff.tsv"
    wanted = speech_candidates(cfg, paths)
    have = {r["item"]: r for r in read_tsv(out)} if out.exists() else {}
    todo = sorted(set(wanted) - set(have))
    if todo:
        spec = cfg["clean"]
        tasks = [(paths["raw"], run, spec) for run in chunks(todo, spec["batch_items"])]
        with multiprocessing.get_context("spawn").Pool(spec["workers"]) as pool:
            for n, rows in enumerate(pool.imap_unordered(_cutoffs, tasks), 1):
                have |= {r["item"]: r for r in rows}
                print(f"clean: {n}/{len(tasks)} batches", flush=True)
                if n % spec["save_every"] == 0:
                    write_tsv(out, CUTOFF_FIELDS, [have[item] for item in sorted(have)])
        write_tsv(out, CUTOFF_FIELDS, [have[item] for item in sorted(have)])
    return out


@dataclass(frozen=True)
class NoiseFacts:
    """What the noise pools' rules read besides a file's path: MUSAN's annotations and OpenSLR's noise listing."""

    music: dict[str, tuple[str, str]]
    background: set[str]
    isotropic: dict[str, str]


def noise_facts(raw: Path) -> NoiseFacts:
    music = {}
    for f in (raw / "noise" / "musan" / "musan" / "music").glob("*/ANNOTATIONS"):
        for line in f.read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) >= 4:
                music[fields[0]] = (fields[2], " ".join(fields[3:]))
    background, inside = set(), False
    listing = raw / "noise" / "musan" / "musan" / "noise" / "free-sound" / "ANNOTATIONS"
    for line in listing.read_text(encoding="utf-8").splitlines():
        if line.endswith(":"):
            inside = line.lower().startswith("background")
        elif inside and line.strip():
            background.add(line.strip())
    isotropic = {}
    base = raw / "rir" / "openslr28"
    for line in (base / "RIRS_NOISES" / "real_rirs_isotropic_noises" / "noise_list").read_text().splitlines():
        fields = line.split()
        if "--room-linkage" in fields:
            isotropic[f"rir/openslr28/{fields[-1]}"] = fields[fields.index("--room-linkage") + 1]
    return NoiseFacts(music, background, isotropic)


def environment(item: str) -> str:
    """DEMAND's recording environment: the folder under noise/demand/."""
    return Path(item).parts[2]


def group_of(pool: dict, item: str, facts: NoiseFacts) -> str | None:
    """The group an item takes in a file pool, or None when the pool's rules leave it out; a group never sits in two
    roles, so one recording never reaches train and test."""
    if not item.startswith(pool["prefix"]):
        return None
    name, stem = Path(item).name, Path(item).stem
    if "file" in pool and name != pool["file"]:
        return None
    if "pattern" in pool and not re.match(pool["pattern"], name):
        return None
    if "vocals" in pool and facts.music.get(stem, ("",))[0] != pool["vocals"]:
        return None
    if "background" in pool and (stem in facts.background) != pool["background"]:
        return None
    if "listing" in pool and item not in facts.isotropic:
        return None
    if "envs" in pool and environment(item) not in pool["envs"]:
        return None
    kind = pool["group"]
    if kind == "freesound":
        return re.sub(r"_\d+\.wav$", "", name)
    if kind == "artist":
        return facts.music[stem][1]
    if kind == "environment":
        return environment(item)
    if kind == "room":
        return facts.isotropic[item]
    return item


def file_pools(cfg: dict) -> list[dict]:
    """The pools read from files: foreground noise and room tone; synthetic and babble pools have no files."""
    return [p for p in cfg["noise"]["pools"] + cfg["tone"]["pools"] if "prefix" in p]


def assign_noise(cfg: dict, paths: dict) -> dict[str, tuple[str, str, str]]:
    """Each screened noise or room-tone file some pool takes: (pool, group, role)."""
    facts = noise_facts(paths["raw"])
    items = sorted({**screened(paths, "noise"), **screened(paths, "rir")})
    pools = file_pools(cfg)
    taken: dict[str, tuple[str, str]] = {}
    for pool in pools:
        for item in items:
            group = group_of(pool, item, facts)
            if group is None:
                continue
            if item in taken:
                raise ValueError(f"{item} meets the rules of both {taken[item][0]} and {pool['name']}")
            taken[item] = (pool["name"], group)
    out = {}
    for pool in pools:
        mine = {item: group for item, (name, group) in taken.items() if name == pool["name"]}
        if not mine:
            raise FileNotFoundError(f"noise pool {pool['name']} has no screened file")
        if "envs" in pool:
            role_of = {group: pool["envs"][group] for group in set(mine.values())}
        else:
            role_of = splits.speaker_roles(set(mine.values()), pool["roles"], "train", cfg["split"]["seed"])
        out |= {item: (pool["name"], group, role_of[group]) for item, group in mine.items()}
    return out


def split_notes(cfg: dict, files: dict[str, list[splits.Row]], seconds: dict[str, float]) -> str:
    rule = cfg["split"]
    wide, span, source, seed = rule["min_cutoff_hz"], rule["eval_min_span_db"], rule["from"], rule["seed"]
    lines = [
        f"# Split `ns/{cfg['version']}`",
        "",
        "Dựng bằng `python -m srpipe.tasks.ns.data split` từ `configs/models/ns.yaml` (KẾ HOẠCH §3.9, ADR-0014).",
        "",
        f"- Tiếng sạch `train`: mọi mẩu qua luật sạch của `split.train` và dải tần ≥ {wide:.0f} Hz đo ở bước `clean`,"
        " không trần giờ; người nói của `val` và `test` không vào; Common Voice không vào.",
        f"- `val`, `test`: các dòng của `{source}` qua khoảng động ≥ {span:.0f} dB và cùng luật dải tần.",
        f"- Nhiễu và nền phòng: mỗi bể một luật nhóm; nhóm chia vai theo seed {seed}, DEMAND theo môi trường; không"
        " nhóm nào ở hai vai.",
        "",
        "| File | Loại | Dòng | Giờ | Người nói |",
        "|---|---|---|---|---|",
    ]
    for name, rows in files.items():
        for kind in ("speech", "noise", "rir"):
            mine = [r for r in rows if r.item.startswith(kind + "/")]
            if mine:
                hours = sum(seconds.get(r.item, 0.0) for r in mine) / splits.SECONDS_PER_HOUR
                speakers = len({r.spk for r in mine if r.spk != splits.ABSENT})
                lines.append(f"| {name} | {kind} | {len(mine)} | {hours:.2f} | {speakers} |")
    return "\n".join(lines) + "\n"


def split(cfg: dict, paths: dict) -> Path:
    """data/splits/ns/<version>: speech, noise and room-tone rows of each role, SPLIT.md, then every check."""
    cutoff = {r["item"]: float(r["cutoff_hz"]) for r in read_tsv(paths["interim"] / "ns" / "clean" / "cutoff.tsv")}
    wide = cfg["split"]["min_cutoff_hz"]
    measures = screened(paths, "speech")
    held = held_rows(cfg, paths)
    held_speakers = {r.spk for rows in held.values() for r in rows if r.spk != splits.ABSENT}
    candidates = speech_candidates(cfg, paths)
    files: dict[str, list[splits.Row]] = {f"{role}.txt": [] for role in ROLES}
    for item in sorted(candidates):
        if train_rule(cfg, item) is None or cutoff.get(item, 0.0) < wide:
            continue
        spk = measures[item].get("speaker") or splits.ABSENT
        if spk in held_speakers:
            continue
        files["train.txt"].append(splits.Row(item, spk, splits.ABSENT, "public"))
    for role, rows in held.items():
        files[f"{role}.txt"] += [r for r in rows if r.item in candidates and cutoff.get(r.item, 0.0) >= wide]
    noise = assign_noise(cfg, paths)
    for item, (_, _, role) in sorted(noise.items()):
        files[f"{role}.txt"].append(splits.Row(item, splits.ABSENT, splits.ABSENT, "public"))
    out = split_dir(paths, cfg)
    seconds = {**{r["item"]: float(r["seconds"]) for r in read_tsv(paths["interim"] / "ns" / "clean" / "cutoff.tsv")}}
    seconds |= {item: m["seconds"] for kind in ("noise", "rir") for item, m in screened(paths, kind).items()}
    splits.write_version(out, files, split_notes(cfg, files, seconds))
    problems = splits.check_version(out) + groups_disjoint(noise)
    if problems:
        raise ValueError(f"{out}: " + "; ".join(problems))
    return out


def groups_disjoint(noise: dict[str, tuple[str, str, str]]) -> list[str]:
    """A group of a noise pool found in two roles, as a readable line each."""
    roles = defaultdict(set)
    for pool, group, role in noise.values():
        roles[(pool, group)].add(role)
    return [f"{pool} group {group} sits in {sorted(r)}" for (pool, group), r in sorted(roles.items()) if len(r) > 1]


def role_rows(cfg: dict, paths: dict, role: str) -> list[splits.Row]:
    return splits.read_split(split_dir(paths, cfg) / f"{role}.txt")


def _decode_speech(task: tuple[Path, list[str], Path, float]) -> tuple[list[str], list[int], list[float]]:
    raw, items, out, below_peak_db = task
    reader = ItemReader(raw)
    kept, lengths, rms, pieces = [], [], [], []
    for item in items:
        x = reader.read(item)
        try:
            level = device.active_rms(x, below_peak_db)
        except ValueError:
            continue
        kept.append(item)
        lengths.append(len(x))
        rms.append(level)
        pieces.append(to_int16(x))
    np.save(out, np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.int16))
    return kept, lengths, rms


def pool_speech(cfg: dict, dev: dict, paths: dict, role: str, rows: list[splits.Row]) -> None:
    """speech_<k>.npy runs of int16 at the clip's own level and speech_index.npz: run, offset, length, active RMS."""
    out = pool_dir(paths, cfg, role)
    items = [r.item for r in rows if r.item.startswith(SPEECH)]
    runs = chunks(items, cfg["pool"]["batch_items"])
    below = dev["talker"]["active_below_peak_db"]
    tasks = [(paths["raw"], run, out / f"speech_{k:04d}.npy", below) for k, run in enumerate(runs)]
    index = defaultdict(list)
    with multiprocessing.get_context("spawn").Pool(cfg["pool"]["workers"]) as pool:
        for k, (kept, lengths, rms) in enumerate(pool.imap(_decode_speech, tasks)):
            offsets = np.concatenate([[0], np.cumsum(lengths)[:-1]]).astype(np.int64) if lengths else []
            index["run"] += [k] * len(kept)
            index["offset"] += list(offsets)
            index["length"] += lengths
            index["active_rms"] += rms
            index["item"] += kept
            print(f"pool {role}: speech {k + 1}/{len(tasks)}", flush=True)
    np.savez(
        out / "speech_index.npz",
        run=np.array(index["run"], dtype=np.int32),
        offset=np.array(index["offset"], dtype=np.int64),
        length=np.array(index["length"], dtype=np.int64),
        active_rms=np.array(index["active_rms"], dtype=np.float32),
    )
    (out / "speech_items.txt").write_text("".join(i + "\n" for i in index["item"]), encoding="utf-8")


def to_pool_level(x: np.ndarray, spec: dict) -> np.ndarray:
    """x at the pool's RMS, lowered further when its peak would pass the pool's peak."""
    rms = math.sqrt(float(np.mean(x**2)))
    if not rms > 0:
        raise ValueError("a silent file")
    y = x * 10.0 ** (spec["level_dbfs"] / 20.0) / rms
    peak = float(np.max(np.abs(y)))
    ceiling = 10.0 ** (spec["peak_dbfs"] / 20.0)
    return y * ceiling / peak if peak > ceiling else y


def write_run(out: Path, name: str, pieces: list[np.ndarray], labels: list[int]) -> None:
    """<name>.npy of int16 pieces end to end and <name>_index.npz of their offsets, lengths and labels."""
    lengths = np.array([len(p) for p in pieces], dtype=np.int64)
    np.save(out / f"{name}.npy", np.concatenate([to_int16(p) for p in pieces]) if pieces else np.zeros(0, np.int16))
    offsets = np.concatenate([[0], np.cumsum(lengths)[:-1]]).astype(np.int64) if len(lengths) else lengths
    np.savez(out / f"{name}_index.npz", offset=offsets, length=lengths, label=np.array(labels, dtype=np.int32))


def pool_tone(cfg: dict, paths: dict, role: str, noise: dict[str, tuple[str, str, str]]) -> None:
    names = [p["name"] for p in cfg["tone"]["pools"]]
    reader = ItemReader(paths["raw"])
    pieces, labels, items = [], [], []
    for item, (pool, _, r) in sorted(noise.items()):
        if r == role and pool in names:
            pieces.append(to_pool_level(reader.read(item), cfg["pool"]))
            labels.append(names.index(pool))
            items.append(item)
    out = pool_dir(paths, cfg, role)
    write_run(out, "tone", pieces, labels)
    (out / "tone_items.txt").write_text("".join(i + "\n" for i in items), encoding="utf-8")


class SpeechPool:
    """A role's decoded speech: utterance k as float64 at its own level, read from the int16 runs."""

    def __init__(self, folder: Path) -> None:
        index = np.load(folder / "speech_index.npz")
        self.run, self.offset, self.length = index["run"], index["offset"], index["length"]
        self.active_rms = index["active_rms"]
        count = int(self.run.max()) + 1 if len(self.run) else 0
        self.runs = [np.load(folder / f"speech_{k:04d}.npy", mmap_mode="r") for k in range(count)]

    def __len__(self) -> int:
        return len(self.length)

    def utterance(self, k: int) -> np.ndarray:
        start = int(self.offset[k])
        return np.asarray(self.runs[int(self.run[k])][start : start + int(self.length[k])], dtype=np.float64) / (
            INT16_SCALE
        )


def pool_babble(cfg: dict, dev: dict, paths: dict, role: str) -> None:
    """babble.npy: tracks of several voices of the role's own speech, each a chain of utterances at one level; the
    voices come in over the first half, the earliest at the track's start, so no stretch of a track is silent."""
    spec, folder = cfg["babble"], pool_dir(paths, cfg, role)
    speech = SpeechPool(folder)
    n = round(spec["track_s"] * FS)
    tracks = []
    ramp = dev["talker"]["edge_ramp_s"]
    for k in range(round(spec["hours"][role] * splits.SECONDS_PER_HOUR / spec["track_s"])):
        rng = np.random.default_rng([spec["seed"], BABBLE_STREAM, ROLES.index(role), k])
        track = np.zeros(n)
        starts = rng.integers(n // 2, size=int(rng.integers(spec["talkers"][0], spec["talkers"][1] + 1)))
        for at in (starts - starts.min()).tolist():
            while at < n:
                u = int(rng.integers(len(speech)))
                x = ramped(speech.utterance(u), ramp) / float(speech.active_rms[u])
                x *= 10.0 ** (float(rng.uniform(-spec["jitter_db"], spec["jitter_db"])) / 20.0)
                stop = min(n, at + len(x))
                track[at:stop] += x[: stop - at]
                at = stop + round(float(rng.uniform(*spec["gap_s"])) * FS)
        tracks.append(to_pool_level(track, cfg["pool"]))
    write_run(folder, "babble", tracks, [0] * len(tracks))


def pool(cfg: dict, dev: dict, paths: dict) -> None:
    """Each role's pools; a role whose manifest records this config and this split is left as it is."""
    noise = {item: v for item, v in assign_noise(cfg, paths).items()}
    measures = {item: m for kind in ("noise", "rir") for item, m in screened(paths, kind).items()}
    wanted_cfg = {key: cfg[key] for key in ("split", "noise", "tone", "babble", "pool")}
    for role in ROLES:
        out = pool_dir(paths, cfg, role)
        split_file = split_dir(paths, cfg) / f"{role}.txt"
        wanted = {"config": wanted_cfg, "split_sha256": sha256_of(split_file)}
        manifest = out / "manifest.yaml"
        if manifest.exists():
            have = yaml.safe_load(manifest.read_text(encoding="utf-8"))
            if {key: have.get(key) for key in wanted} == wanted:
                print(f"pool {role}: up to date", flush=True)
                continue
        out.mkdir(parents=True, exist_ok=True)
        rows = role_rows(cfg, paths, role)
        pool_speech(cfg, dev, paths, role, rows)
        in_role = {r.item for r in rows}
        foreground = {p["name"] for p in cfg["noise"]["pools"]}
        noise_rows = [
            {"item": item, "pool": pool_name, "group": group, "seconds": measures[item]["seconds"]}
            for item, (pool_name, group, _) in sorted(noise.items())
            if item in in_role and pool_name in foreground
        ]
        write_tsv(out / "noise_index.tsv", NOISE_FIELDS, noise_rows)
        pool_tone(cfg, paths, role, noise)
        pool_babble(cfg, dev, paths, role)
        files = sorted(p for p in out.iterdir() if p.name != "manifest.yaml")
        body = wanted | {"sha256": {p.name: sha256_of(p) for p in files}}
        manifest.write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=False), encoding="utf-8")
        print(f"pool {role}: {len(files)} files", flush=True)


@dataclass(frozen=True)
class Example:
    """One mixture: board B's int16 capture (n, 2), the talker alone on the chain's scale with no noise, no floor
    and no saturation (2, n), per hop whether speech is in the STFT frame, and what was drawn."""

    capture: np.ndarray
    talker: np.ndarray
    vad: np.ndarray
    draws: dict


@dataclass(frozen=True)
class Recipe:
    """Example (epoch, j) before any filter, what the Mixer drew and read: the room's RIRs (source, mic, taps); the
    talker's dry track at its level from lead samples ahead of the window, None with no active hop, and its active hops;
    a foreground source the noise RIRs carry (n + taps), or an int16 pair for a diffuse field, at snr_db over the
    talker or at fg_dbfs with none; the room tone's int16 pair; the stream's state at the self noise, its last draw."""

    rirs: np.ndarray
    lead: int
    dry: np.ndarray | None
    active: np.ndarray
    source: np.ndarray | None
    pair: np.ndarray | None
    snr_db: float | None
    fg_dbfs: float | None
    tone: np.ndarray
    tone_dbfs: float
    gain_db: float
    draws: dict
    noise_state: dict


def stream_at(state: dict) -> np.random.Generator:
    """A generator that goes on from a bit generator's saved state."""
    rng = np.random.default_rng()
    rng.bit_generator.state = state
    return rng


@dataclass(frozen=True)
class Stream:
    """An epoch's speech: utterances in a shuffled order, each at its start in samples, with its level, then the
    silence after it."""

    order: np.ndarray
    start: np.ndarray
    scale: np.ndarray
    total: int


def forget_old(cache: dict[int, object]) -> None:
    """Keep the EPOCHS_HELD epochs a per-epoch cache took last."""
    while len(cache) > EPOCHS_HELD:
        del cache[next(iter(cache))]


class Mixer:
    """Examples of one role (KEHOACH 3.9): every speech sample once an epoch in windows of mix.example_s, plus
    windows without a talker, each in a room of the role, with a foreground source, the room's tone, board B's
    microphones and a global gain; example (epoch, j) is a pure function of its seed, its recipe through mixed.
    Several threads may take recipes of an epoch at once once kinds(epoch) has filled that epoch's caches."""

    def __init__(self, cfg: dict, dev: dict, paths: dict, role: str, seed: int) -> None:
        self.cfg, self.dev, self.role, self.seed = cfg, dev, role, seed
        folder = pool_dir(paths, cfg, role)
        self.speech = SpeechPool(folder)
        tone, babble = np.load(folder / "tone_index.npz"), np.load(folder / "babble_index.npz")
        self.tone = (np.load(folder / "tone.npy", mmap_mode="r"), tone["offset"], tone["length"], tone["label"])
        self.babble = (np.load(folder / "babble.npy", mmap_mode="r"), babble["offset"], babble["length"])
        self.noise = defaultdict(list)
        for r in read_tsv(folder / "noise_index.tsv"):
            self.noise[r["pool"]].append((r["item"], float(r["seconds"])))
        self.reader = ItemReader(paths["raw"])
        # ItemReader streams parquet rows from where it stopped: one caller at a time.
        self._reader_lock = threading.Lock()
        self._frames: dict[str, tuple[int, int]] = {}
        self.bank = device.room_bank(dev, paths["interim"], 1)
        self.mics = device.load_microphones(dev["microphone"])
        self.n = round(cfg["mix"]["example_s"] * FS) // HOP * HOP
        self._streams: dict[int, Stream] = {}
        self._kinds: dict[int, np.ndarray] = {}

    def stream(self, epoch: int) -> Stream:
        if epoch not in self._streams:
            mix, t = self.cfg["mix"], self.dev["talker"]
            rng = np.random.default_rng([self.seed, PACK_STREAM, epoch])
            order = rng.permutation(len(self.speech))
            lengths = self.speech.length[order]
            gaps = rng.uniform(*mix["gap_s"], len(order))
            long = rng.random(len(order)) < mix["long_pause"]["probability"]
            gaps[long] = rng.uniform(*mix["long_pause"]["seconds"], int(long.sum()))
            steps = -(-(lengths + np.round(gaps * FS).astype(np.int64)) // HOP) * HOP
            start = np.concatenate([[0], np.cumsum(steps)[:-1]]).astype(np.int64)
            jitter = rng.uniform(-t["jitter_db"], t["jitter_db"], len(order))
            scale = 10.0 ** (jitter / 20.0) / self.speech.active_rms[order]
            self._streams[epoch] = Stream(order, start, scale, int(start[-1] + steps[-1]) if len(order) else 0)
            forget_old(self._streams)
        return self._streams[epoch]

    def kinds(self, epoch: int) -> np.ndarray:
        """The epoch's examples in order: k >= 0 is speech window k, -1 a window with no talker."""
        if epoch not in self._kinds:
            windows = -(-self.stream(epoch).total // self.n)
            share = self.cfg["mix"]["no_speech"]
            empty = round(windows * share / (1.0 - share))
            kinds = np.concatenate([np.arange(windows), -np.ones(empty, dtype=np.int64)])
            self._kinds[epoch] = kinds[np.random.default_rng([self.seed, KIND_STREAM, epoch]).permutation(len(kinds))]
            forget_old(self._kinds)
        return self._kinds[epoch]

    def dry(self, epoch: int, begin: int, end: int) -> tuple[np.ndarray, np.ndarray]:
        """The stream's samples [begin, end) at unit active level, zeros outside it, and per hop of that span whether
        it holds an utterance's active hop; begin is a multiple of HOP."""
        s, ramp = self.stream(epoch), self.dev["talker"]["edge_ramp_s"]
        below = self.dev["talker"]["active_below_peak_db"]
        x, active = np.zeros(end - begin), np.zeros((end - begin) // HOP, dtype=bool)
        first = max(0, int(np.searchsorted(s.start, begin, side="right")) - 1)
        for u in range(first, len(s.order)):
            at = int(s.start[u])
            if at >= end:
                break
            y = ramped(self.speech.utterance(int(s.order[u])), ramp) * s.scale[u]
            lo, hi = max(at, begin), min(at + len(y), end)
            if lo >= hi:
                continue
            x[lo - begin : hi - begin] = y[lo - at : hi - at]
            hops = room.active_hops(y, below)
            for h in np.nonzero(hops)[0]:
                t = (at + h * HOP - begin) // HOP
                if 0 <= t < len(active):
                    active[t] = True
        return x, active

    def respond(self, air: np.ndarray) -> np.ndarray:
        return device.respond(air, self.mics)

    def level_of(self, responded: np.ndarray, channel: int) -> float:
        """RMS of a channel of respond's output on the chain's scale."""
        return math.sqrt(float(np.mean(responded[channel] ** 2))) * device.chain_scale(self.mics)

    def foreground(self, rng: np.random.Generator, taps: int) -> tuple[np.ndarray | None, np.ndarray | None, dict]:
        """A foreground's source of n + taps samples for the noise RIRs, or an int16 pair of babble, and its draws."""
        classes = self.cfg["noise"]["classes"]
        names = list(classes)
        shares = np.array([classes[c]["share"] for c in names])
        cls = names[int(rng.choice(len(names), p=shares / shares.sum()))]
        pools = [p for p in self.cfg["noise"]["pools"] if p["class"] == cls and self.available(p)]
        weights = np.array([p["weight"] for p in pools])
        pool = pools[int(rng.choice(len(pools), p=weights / weights.sum()))]
        n = self.n
        if pool.get("babble"):
            track, offset, length = self.babble
            picks = rng.choice(len(length), 2, replace=len(length) < 2)
            segs = []
            for k in picks:
                start = int(offset[k]) + int(rng.integers(max(1, int(length[k]) - n)))
                segs.append(np.resize(np.asarray(track[start : start + n]), n))
            return None, np.stack(segs), {"class": cls, "pool": pool["name"]}
        if "synthetic" in pool:
            slope = float(rng.uniform(*pool["synthetic"]["slope_db_per_octave"]))
            spectrum = np.fft.rfft(rng.standard_normal(n + taps))
            freqs = np.fft.rfftfreq(n + taps, 1.0 / FS)
            spectrum[1:] *= (freqs[1:] / 1000.0) ** (slope / (20.0 * math.log10(2.0)))
            spectrum[0] = 0.0
            source, draws = np.fft.irfft(spectrum, n + taps), {"slope_db_per_octave": slope}
        else:
            files = self.noise[pool["name"]]
            seconds = np.array([s for _, s in files])
            item = files[int(rng.choice(len(files), p=seconds / seconds.sum()))][0]
            source, draws = self.noise_stretch(rng, item, n + taps), {"file": item}
        return source, None, {"class": cls, "pool": pool["name"], **draws}

    def noise_stretch(self, rng: np.random.Generator, item: str, count: int) -> np.ndarray:
        """count samples of a noise file from a start drawn over its length, tiled when the file is shorter; a file at
        the grid's rate is read over that stretch alone, the same samples as the whole file's."""
        path = self.reader.raw_root / item
        if "#" not in item and "@" not in item:
            if item not in self._frames:
                info = sf.info(str(path))
                self._frames[item] = (info.frames, info.samplerate)
            frames, rate = self._frames[item]
            if rate == FS:
                start = int(rng.integers(max(1, frames - count)))
                x = sf.read(str(path), start=start, stop=min(frames, start + count), dtype="float64", always_2d=True)[0]
                return np.resize(x.mean(axis=1), count)
        with self._reader_lock:
            y = self.reader.read(item)
        start = int(rng.integers(max(1, len(y) - count)))
        return np.resize(y[start:], count)

    def available(self, pool: dict) -> bool:
        if pool.get("babble"):
            return len(self.babble[2]) > 0
        return "synthetic" in pool or bool(self.noise.get(pool["name"]))

    def room_tone(self, rng: np.random.Generator) -> tuple[np.ndarray, dict]:
        """Two int16 stretches of the room tone for a diffuse field, and the pool drawn."""
        tone, offset, length, label = self.tone
        names = [p["name"] for p in self.cfg["tone"]["pools"]]
        have = sorted(set(label.tolist()))
        weights = np.array([self.cfg["tone"]["pools"][k]["weight"] for k in have])
        pick = have[int(rng.choice(len(have), p=weights / weights.sum()))]
        files = np.nonzero(label == pick)[0]
        n = self.n
        k = int(files[int(rng.integers(len(files)))])
        # Two stretches of one file, or of two files when one is too short to hold both apart.
        if length[k] >= 2 * n:
            half = int(length[k]) // 2
            starts = [int(offset[k]) + int(rng.integers(half - n + 1)), int(offset[k]) + half]
            starts[1] += int(rng.integers(int(length[k]) - half - n + 1))
            spans = [(s, n) for s in starts]
        else:
            other = int(files[int(rng.integers(len(files)))])
            spans = [(int(offset[f]) + int(rng.integers(max(1, int(length[f]) - n))), n) for f in (k, other)]
        return np.stack([np.resize(np.asarray(tone[s : s + m]), n) for s, m in spans]), {"tone_pool": names[pick]}

    def recipe(self, epoch: int, j: int) -> Recipe:
        """Every draw and every read of example (epoch, j), from its one stream in a fixed order, self noise last."""
        mix, t = self.cfg["mix"], self.dev["talker"]
        rng = np.random.default_rng([self.seed, EXAMPLE_STREAM, epoch, j])
        low, high = mix["rooms"][self.role]
        entry = int(rng.integers(low, high))
        rirs = np.load(self.bank / f"room_{entry:04d}.npy")
        taps, n = rirs.shape[-1], self.n
        window = int(self.kinds(epoch)[j])
        draws: dict = {"epoch": epoch, "example": j, "window": window, "room": entry}
        dry, lead, active = None, 0, np.zeros(n // HOP, dtype=bool)
        if window >= 0:
            # A lead-in of whole hops ahead of the window carries the reverberant tail of earlier speech.
            lead = -(-taps // HOP) * HOP
            dry, active = self.dry(epoch, window * n - lead, (window + 1) * n)
            spl_db = float(rng.uniform(*t["spl_1m_db"]))
            level = self.mics.sensitivity_dbfs + spl_db - device.SENSITIVITY_SPL_DB
            dry *= device.TALKER_REFERENCE_M * 10.0 ** (level / 20.0)
            active = active[lead // HOP :]
            draws["spl_1m_db"] = spl_db
        talking = bool(active.any())
        source = pair = snr_db = fg_dbfs = None
        if rng.random() < mix["foreground"]:
            source, pair, fg_draws = self.foreground(rng, taps)
            if talking:
                snr_db = float(rng.uniform(*self.cfg["noise"]["classes"][fg_draws["class"]]["snr_db"]))
                fg_draws["snr_db"] = snr_db
            else:
                fg_dbfs = float(rng.uniform(*mix["noise_only_dbfs"]))
                fg_draws["level_dbfs"] = fg_dbfs
            draws |= fg_draws
        tone, tone_draws = self.room_tone(rng)
        tone_dbfs = float(rng.uniform(*self.cfg["tone"]["level_dbfs"]))
        gain_db = float(rng.uniform(*mix["global_gain_db"]))
        draws |= tone_draws | {"tone_dbfs": tone_dbfs, "gain_db": gain_db, "talker": talking}
        talker = dry if talking else None
        return Recipe(
            rirs,
            lead,
            talker,
            active,
            source,
            pair,
            snr_db,
            fg_dbfs,
            tone,
            tone_dbfs,
            gain_db,
            draws,
            rng.bit_generator.state,
        )

    def mixed(self, r: Recipe) -> Example:
        """The example a recipe makes through the board simulation's filters, numpy in float64."""
        n, taps = self.n, r.rirs.shape[-1]
        talker = np.zeros((2, n))
        if r.dry is not None:
            talker = np.stack(
                [signal.fftconvolve(r.dry, r.rirs[device.TALKER, m])[r.lead : r.lead + n] for m in range(2)]
            )
        responded = self.respond(talker) if r.dry is not None else np.zeros((2, n))
        total = responded.copy()
        if r.source is not None or r.pair is not None:
            if r.source is not None:
                fg = np.stack(
                    [signal.fftconvolve(r.source, r.rirs[device.NOISE, m])[taps : taps + n] for m in range(2)]
                )
            else:
                fg = device.diffuse_pair(*(r.pair / INT16_SCALE))
            fg_resp = self.respond(fg)
            if not self.level_of(fg_resp, 0) > 0:
                raise ValueError(f"a silent foreground: {r.draws}")
            if r.snr_db is not None:
                power = float(np.mean(talker[0].reshape(-1, HOP)[r.active] ** 2))
                gain = math.sqrt(power / 10.0 ** (r.snr_db / 10.0) / float(np.mean(fg[0] ** 2)))
            else:
                gain = 10.0 ** (r.fg_dbfs / 20.0) / self.level_of(fg_resp, 0)
            total += gain * fg_resp
        tone_resp = self.respond(device.diffuse_pair(*(r.tone / INT16_SCALE)))
        total += tone_resp * 10.0 ** (r.tone_dbfs / 20.0) / self.level_of(tone_resp, 1)
        g = 10.0 ** (r.gain_db / 20.0)
        capture = device.digitise(g * total, self.mics, stream_at(r.noise_state))
        linear = (g * device.chain_scale(self.mics) * responded).astype(np.float32)
        vad = r.active | np.concatenate([[False], r.active[:-1]])
        return Example(capture, linear, vad.astype(np.uint8), r.draws)

    def example(self, epoch: int, j: int) -> Example:
        return self.mixed(self.recipe(epoch, j))


def slot_powers(example: Example, gains: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per hop and bin, float32 powers at the ns slot: of the capture, of the talker alone, and of everything else."""
    x = device.slot_bins(example.capture.T.astype(np.float64) / INT16_SCALE, gains)
    s = device.slot_bins(example.talker, gains)
    return (
        (np.abs(x) ** 2).astype(np.float32),
        (np.abs(s) ** 2).astype(np.float32),
        (np.abs(x - s) ** 2).astype(np.float32),
    )


def _write_shard(task: tuple) -> Path:
    cfg, dev, paths, role, seed, shard, js, out = task
    mixer = Mixer(cfg, dev, paths, role, seed)
    examples = [mixer.example(0, j) for j in js]
    scale = np.array([max(float(np.max(np.abs(e.talker))), 1e-9) / 32767.0 for e in examples], dtype=np.float32)
    stem = out / f"shard_{shard:05d}"
    np.save(stem.with_suffix(".capture.npy"), np.stack([e.capture for e in examples]))
    talker = [np.round(e.talker.T / s).astype(np.int16) for e, s in zip(examples, scale, strict=True)]
    np.save(stem.with_suffix(".talker.npy"), np.stack(talker))
    np.save(stem.with_suffix(".scale.npy"), scale)
    np.save(stem.with_suffix(".vad.npy"), np.stack([e.vad for e in examples]))
    lines = "".join(json.dumps(e.draws, ensure_ascii=False) + "\n" for e in examples)
    stem.with_suffix(".items.jsonl").write_text(lines, encoding="utf-8")
    return stem


def sets(cfg: dict, dev: dict, paths: dict) -> None:
    """processed/ns/<version>/{val,test}: epoch 0 of each held role's stream, mixed once with sets.seed."""
    spec = cfg["sets"]
    for role in HELD:
        out = set_dir(paths, cfg, role)
        out.mkdir(parents=True, exist_ok=True)
        count = len(Mixer(cfg, dev, paths, role, spec["seed"]).kinds(0))
        per = spec["examples_per_shard"]
        tasks = [
            (cfg, dev, paths, role, spec["seed"], k, list(range(i, min(i + per, count))), out)
            for k, i in enumerate(range(0, count, per))
        ]
        with multiprocessing.get_context("spawn").Pool(cfg["pool"]["workers"]) as pool:
            stems = pool.map(_write_shard, tasks)
        files = sorted(p for p in out.iterdir() if p.name != "manifest.yaml")
        body = {
            "config": {key: cfg[key] for key in ("mix", "sets")},
            "pools": sha256_of(pool_dir(paths, cfg, role) / "manifest.yaml"),
            "examples": count,
            "shards": len(stems),
            "sha256": {p.name: sha256_of(p) for p in files},
        }
        (out / "manifest.yaml").write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=False), "utf-8")
        print(f"sets {role}: {count} examples in {len(stems)} shards", flush=True)


def istft(bins: np.ndarray) -> np.ndarray:
    synthesis = Istft()
    return np.concatenate([synthesis.synthesize(frame) for frame in bins.astype(np.complex64)])


def pilot(cfg: dict, dev: dict, paths: dict) -> Path:
    """cache/listen/ns/pilot/: PILOT_PER_CLASS train examples of each foreground class and with no talker, as the
    capture's ch0, the talker at the slot and everything else at the slot, with pilot.tsv of their draws; then the
    DNSMOS report of the targets."""
    out = paths["cache"] / "listen" / "ns" / "pilot"
    out.mkdir(parents=True, exist_ok=True)
    mixer = Mixer(cfg, dev, paths, "train", cfg["mix"]["seed"])
    gains = mixer.mics.gains
    wanted = {c: PILOT_PER_CLASS for c in cfg["noise"]["classes"]} | {"no_talker": PILOT_PER_CLASS}
    kinds = mixer.kinds(0)
    rows, j = [], 0
    while any(wanted.values()) and j < len(kinds):
        e = mixer.example(0, j)
        j += 1
        key = "no_talker" if not e.draws["talker"] else e.draws.get("class")
        if key is None or not wanted.get(key):
            continue
        wanted[key] -= 1
        xs = device.slot_bins(e.capture.T.astype(np.float64) / INT16_SCALE, gains)
        ss = device.slot_bins(e.talker, gains)
        power_s, power_n = float(np.mean(np.abs(ss) ** 2)), float(np.mean(np.abs(xs - ss) ** 2))
        snr = e.draws.get("snr_db")
        name = f"{len(rows):02d}_{key}" + (f"_snr{snr:+.0f}" if snr is not None else "")
        write_wav(out / f"{name}_mix.wav", e.capture[:, 0].astype(np.float64) / INT16_SCALE)
        write_wav(out / f"{name}_target.wav", istft(ss))
        write_wav(out / f"{name}_noise.wav", istft(xs - ss))
        slot_snr = 10.0 * math.log10((power_s + POWER_TINY) / (power_n + POWER_TINY))
        rows.append({"wav": name, "slot_snr_db": round(slot_snr, 1), **e.draws})
    fields = sorted({k for r in rows for k in r}, key=lambda k: (k != "wav", k))
    write_tsv(out / "pilot.tsv", tuple(fields), rows)
    if cfg["clean"]["dnsmos_sample"]:
        dnsmos_report(cfg, paths, out)
    return out


def dnsmos_report(cfg: dict, paths: dict, out: Path) -> list[dict]:
    """DNSMOS P.835 of seeded train utterances as the pool holds them, how clean the targets are: dnsmos.tsv and
    dnsmos_by_corpus.tsv in out, the latter returned."""
    folder = pool_dir(paths, cfg, "train")
    speech = SpeechPool(folder)
    items = (folder / "speech_items.txt").read_text(encoding="utf-8").splitlines()
    rng = np.random.default_rng([cfg["split"]["seed"], DNSMOS_STREAM])
    picks = sorted(rng.choice(len(speech), size=min(cfg["clean"]["dnsmos_sample"], len(speech)), replace=False))
    clips = {}
    for k in picks:
        clips[items[k]] = out / "dnsmos" / f"{k:06d}.wav"
        write_wav(clips[items[k]], speech.utterance(int(k)))
    spec = load_yaml(CONFIGS / cfg["clean"]["dnsmos_refs"])["refs"]["dnsmos"]
    scores = refs.dnsmos(clips, spec, paths["cache"], paths["cache"] / "afe_ref" / "work")
    rows = [{"item": item, **scores[item]} for item in clips]
    write_tsv(out / "dnsmos.tsv", ("item", "sig", "bak", "ovrl"), rows)
    summary = []
    for corpus in sorted({corpus_name(r["item"]) for r in rows}):
        mine = np.array([[r[k] for k in ("sig", "bak", "ovrl")] for r in rows if corpus_name(r["item"]) == corpus])
        means, lows = mine.mean(axis=0), np.percentile(mine, 10, axis=0)
        figures = {
            f"{k}_{what}": round(float(v[i]), 2)
            for what, v in (("mean", means), ("p10", lows))
            for i, k in enumerate(("sig", "bak", "ovrl"))
        }
        summary.append({"corpus": corpus, "clips": len(mine), **figures})
    write_tsv(out / "dnsmos_by_corpus.tsv", tuple(summary[0]), summary)
    return summary


def corpus_name(item: str) -> str:
    return item.split("/")[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["clean", "split", "pool", "sets", "pilot"])
    args = parser.parse_args(argv)
    cfg, dev = configs()
    paths = data_paths()
    if args.step == "clean":
        print(clean(cfg, paths))
    elif args.step == "split":
        print(split(cfg, paths))
    elif args.step == "pool":
        pool(cfg, dev, paths)
    elif args.step == "sets":
        sets(cfg, dev, paths)
    else:
        print(pilot(cfg, dev, paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
