"""Labelled spatial scenes (E4-T4, KEHOACH 1.2, 3.6-3.9): a shoebox room holds the board's two microphones, a talker
and maybe an interferer, each source's image kept apart; talker_dry is the clean source SDR and ns are scored against.

The angle label is the 3D angle between a source and the axis from ch0 to ch1 (contracts/array.yaml), the angle the
microphones' delay measures. python -m srpipe.scenes.room [config] writes interim/scenes/<name>/scene_NNNN/ and a
manifest of sha256s.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyroomacoustics as pra
import yaml

from srpipe.core.audio_io import read_wav, write_wav
from srpipe.core.config import CONFIGS, data_paths, load_yaml
from srpipe.generated import array, grid

HOP = grid.HOP_SAMPLES
FS = grid.SAMPLE_RATE_HZ
FAR_FLOOR_M = 0.1


@dataclass(frozen=True)
class Placement:
    """Positions in metres: the room's size, ch0 and ch1 as columns, the talker, the interferer if any."""

    room_m: np.ndarray
    mics_m: np.ndarray
    talker_m: np.ndarray
    interferer_m: np.ndarray | None


def angle_deg(source_m: np.ndarray, mics_m: np.ndarray) -> float:
    """Angle between the source and the axis pointing from ch0 to ch1, seen from the array's centre."""
    axis = (mics_m[:, 1] - mics_m[:, 0]) / np.linalg.norm(mics_m[:, 1] - mics_m[:, 0])
    towards = source_m - mics_m.mean(axis=1)
    return math.degrees(math.acos(float(np.clip(towards @ axis / np.linalg.norm(towards), -1.0, 1.0))))


def tdoa_s(source_m: np.ndarray, mics_m: np.ndarray) -> float:
    """t0 - t1, positive when the source is nearer ch1 (contracts/array.yaml)."""
    return float(np.linalg.norm(source_m - mics_m[:, 0]) - np.linalg.norm(source_m - mics_m[:, 1])) / (
        array.SPEED_OF_SOUND_M_S
    )


def combinations(cfg: dict) -> list[tuple[float, str | None, float | None]]:
    """(rt60, interferer kind, snr): each rt60 alone, then with each kind at each snr."""
    kinds, snrs = cfg["interferer"]["kinds"], cfg["snr_db"]
    alone = [(rt60, None, None) for rt60 in cfg["rt60_s"]]
    return alone + [(rt60, kind, snr) for rt60 in cfg["rt60_s"] for kind in kinds for snr in snrs]


def _inside(p: np.ndarray, room_m: np.ndarray, margin: float) -> bool:
    in_plan = np.all(p[:2] >= margin) and np.all(p[:2] <= room_m[:2] - margin)
    return bool(in_plan and FAR_FLOOR_M <= p[2] <= room_m[2] - FAR_FLOOR_M)


def _source(centre: np.ndarray, yaw: float, spec: dict, rng: np.random.Generator) -> np.ndarray:
    """A source on either side of the array axis at a random horizontal angle, distance and height."""
    bearing = yaw + rng.choice([-1.0, 1.0]) * rng.uniform(0.0, math.pi)
    r = rng.uniform(*spec["distance_m"])
    height = rng.uniform(*spec["height_m"])
    return np.array([centre[0] + r * math.cos(bearing), centre[1] + r * math.sin(bearing), height])


def place(cfg: dict, rng: np.random.Generator, with_interferer: bool) -> Placement:
    """A room, the array on a table in it at a random yaw, and sources at random angles, sides and distances; the
    interferer at least min_separation_deg away from the talker in label angle."""
    margin = cfg["wall_margin_m"]
    for _ in range(cfg["max_tries"]):
        room_m = np.array([rng.uniform(*cfg["room_m"][k]) for k in "xyz"])
        x, y = rng.uniform(margin, room_m[0] - margin), rng.uniform(margin, room_m[1] - margin)
        centre = np.array([x, y, rng.uniform(*cfg["array_height_m"])])
        yaw = rng.uniform(0.0, 2.0 * math.pi)
        half = array.SPACING_M / 2.0 * np.array([math.cos(yaw), math.sin(yaw), 0.0])
        mics_m = np.stack([centre - half, centre + half], axis=1)
        talker_m = _source(centre, yaw, cfg["talker"], rng)
        interferer_m = _source(centre, yaw, cfg["interferer"], rng) if with_interferer else None
        placed = [talker_m] + ([interferer_m] if interferer_m is not None else [])
        gap = None if interferer_m is None else abs(angle_deg(talker_m, mics_m) - angle_deg(interferer_m, mics_m))
        apart = gap is None or gap >= cfg["interferer"]["min_separation_deg"]
        if apart and all(_inside(q, room_m, margin) for q in placed):
            return Placement(room_m, mics_m, talker_m, interferer_m)
    raise RuntimeError(f"no placement fits after {cfg['max_tries']} tries")


def images(p: Placement, rt60_s: float, signals: list[np.ndarray]) -> tuple[np.ndarray, float | None]:
    """Each source's image at ch0 and ch1, shaped (sources, 2, samples of the first signal), and the RT60 measured on
    the talker's RIR at ch0; rt60_s 0 is an anechoic room."""
    if rt60_s > 0:
        absorption, max_order = pra.inverse_sabine(rt60_s, p.room_m, c=array.SPEED_OF_SOUND_M_S)
        room = pra.ShoeBox(p.room_m, fs=FS, materials=pra.Material(absorption), max_order=max_order)
    else:
        room = pra.ShoeBox(p.room_m, fs=FS, max_order=0)
    room.set_sound_speed(array.SPEED_OF_SOUND_M_S)
    sources = [p.talker_m] + ([p.interferer_m] if p.interferer_m is not None else [])
    for position, signal in zip(sources, signals, strict=True):
        room.add_source(position, signal=signal)
    room.add_microphone_array(p.mics_m)
    premix = room.simulate(return_premix=True)[:, :, : len(signals[0])]
    measured = pra.experimental.measure_rt60(room.rir[0][0], fs=FS) if rt60_s > 0 else None
    return premix, measured


def active_hops(dry: np.ndarray, below_peak_db: float) -> np.ndarray:
    energy = np.mean(dry[: len(dry) // HOP * HOP].reshape(-1, HOP) ** 2, axis=1) + 1e-20
    return 10 * np.log10(energy) > 10 * np.log10(energy.max()) - below_peak_db


def talk(files: list[Path], cfg: dict, rng: np.random.Generator, n: int) -> tuple[np.ndarray, list[str]]:
    """Utterances of one speaker in a random order after lead_s of silence, gap_s apart, cut at n samples."""
    pieces, used, at = [np.zeros(round(cfg["lead_s"] * FS))], [], round(cfg["lead_s"] * FS)
    for i in rng.permutation(len(files)):
        if at >= n:
            break
        x = read_wav(files[i])[0][:, 0].astype(np.float64)
        pieces += [x, np.zeros(round(cfg["gap_s"] * FS))]
        used.append(files[i].stem)
        at += len(x) + round(cfg["gap_s"] * FS)
    return np.concatenate(pieces)[:n], used


def noise_piece(files: list[Path], rng: np.random.Generator, n: int) -> tuple[np.ndarray, str]:
    path = files[int(rng.integers(len(files)))]
    x = read_wav(path)[0][:, 0].astype(np.float64)
    start = int(rng.integers(max(1, len(x) - n)))
    return np.resize(x[start:], n), f"{path.parent.name}/{path.name}"


def build_scene(cfg: dict, raw_root: Path, index: int) -> dict:
    """Scene index of the set: its signals and its labels, the same for the same seed and index."""
    rt60_s, kind, snr_db = combinations(cfg)[index % len(combinations(cfg))]
    rng = np.random.default_rng([cfg["seed"], index])
    n = round(cfg["duration_s"] * FS)
    speakers = sorted(p for p in (raw_root / cfg["speech"]).iterdir() if p.is_dir())
    who = rng.permutation(len(speakers))
    talker_dry, utterances = talk(sorted(speakers[who[0]].glob("*.wav")), cfg, rng, n)
    p = place(cfg, rng, kind is not None)
    labels: dict = {"scene": index, "rt60_target_s": rt60_s, "room_m": p.room_m.tolist(), "mics_m": p.mics_m.T.tolist()}
    signals = [talker_dry]
    interferer: dict | None = None
    if kind == "talker":
        other, other_used = talk(sorted(speakers[who[1]].glob("*.wav")), {**cfg, "lead_s": 0.0}, rng, n)
        signals.append(other)
        interferer = {"kind": kind, "speaker": speakers[who[1]].name, "utterances": other_used}
    elif kind == "noise":
        noise, source = noise_piece(sorted((raw_root / cfg["noise"]).glob("*/ch01.wav")), rng, n)
        signals.append(noise)
        interferer = {"kind": kind, "source": source}
    per_source, measured = images(p, rt60_s, signals)
    active = active_hops(talker_dry, cfg["active_below_peak_db"])
    talker_power = np.mean(per_source[0, 0, : len(active) * HOP].reshape(-1, HOP)[active] ** 2)
    per_source *= 10 ** (cfg["speech_level_dbfs"] / 20) / math.sqrt(talker_power)
    if interferer is not None:
        per_source[1] *= math.sqrt(10 ** (cfg["speech_level_dbfs"] / 10 - snr_db / 10) / np.mean(per_source[1, 0] ** 2))
        interferer |= {
            "position_m": p.interferer_m.tolist(),
            "angle_deg": angle_deg(p.interferer_m, p.mics_m),
            "tdoa_s": tdoa_s(p.interferer_m, p.mics_m),
        }
    labels |= {
        "rt60_measured_s": measured,
        "talker": {
            "speaker": speakers[who[0]].name,
            "utterances": utterances,
            "position_m": p.talker_m.tolist(),
            "angle_deg": angle_deg(p.talker_m, p.mics_m),
            "tdoa_s": tdoa_s(p.talker_m, p.mics_m),
            "distance_m": float(np.linalg.norm(p.talker_m - p.mics_m.mean(axis=1))),
        },
        "interferer": interferer,
        "snr_db": snr_db,
        "speech_level_dbfs": cfg["speech_level_dbfs"],
        "lead_s": cfg["lead_s"],
    }
    images_by_source = {"talker": per_source[0].T, "interferer": per_source[1].T if interferer else None}
    return {**images_by_source, "talker_dry": talker_dry, "labels": labels}


def write_scene(out_dir: Path, scene: dict) -> list[Path]:
    """mix.wav, talker.wav, interferer.wav when there is one (two channels), talker_dry.wav (one), labels.json."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = [out_dir / "talker.wav", out_dir / "mix.wav", out_dir / "talker_dry.wav"]
    mix = scene["talker"] if scene["interferer"] is None else scene["talker"] + scene["interferer"]
    write_wav(written[0], scene["talker"])
    write_wav(written[1], mix)
    write_wav(written[2], scene["talker_dry"])
    if scene["interferer"] is not None:
        written.append(out_dir / "interferer.wav")
        write_wav(written[-1], scene["interferer"])
    written.append(out_dir / "labels.json")
    written[-1].write_text(json.dumps(scene["labels"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return written


def _one(job: tuple[dict, Path, Path, int]) -> list[Path]:
    cfg, raw_root, out_root, index = job
    return write_scene(out_root / f"scene_{index:04d}", build_scene(cfg, raw_root, index))


def build_set(cfg: dict, raw_root: Path, out_root: Path, workers: int = 1) -> Path:
    """Every scene of the set into out_root, then manifest.yaml: the config and each file's sha256."""
    count = cfg["scenes_per_combination"] * len(combinations(cfg))
    jobs = [(cfg, raw_root, out_root, i) for i in range(count)]
    with multiprocessing.get_context("spawn").Pool(workers) as pool:
        written = [p for paths in pool.map(_one, jobs) for p in paths]
    sums = {str(p.relative_to(out_root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(written)}
    manifest = out_root / "manifest.yaml"
    body = {"config": cfg, "scenes": count, "sha256": sums}
    manifest.write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", nargs="?", default=str(CONFIGS / "scenes" / "standard.yaml"))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    cfg = load_yaml(Path(args.config))
    paths = data_paths()
    manifest = build_set(cfg, paths["raw"], paths["interim"] / "scenes" / cfg["name"], args.workers)
    print(f"{manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
