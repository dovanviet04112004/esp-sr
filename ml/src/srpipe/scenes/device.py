"""The board simulation of KEHOACH 1.2 (E4-T8): clean speech becomes what the recogniser hears on board B.

build runs sessions of split items in rooms of a bank built once, on the microphones and through the chain, log-mel
and, when asked, pitch of configs/scenes/device.yaml, into processed/<out>/ with a manifest of sha256s. playback writes
interim/playback/vivos_test.wav, VIVOS test to play from a loudspeaker at a known place (host/plans/playback.tsv).
Run: python -m srpipe.scenes.device build <split file> <out> | playback
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import multiprocessing
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml
from scipy import fft as sfft
from scipy import signal

from srpipe.core import screen, splits
from srpipe.core.audio_io import ItemReader, ramped, read_wav, to_float, write_wav
from srpipe.core.config import CONFIGS, ML_ROOT, data_paths, load_yaml
from srpipe.dsp.afe import hpf
from srpipe.dsp.afe.chain import PCM_FULL_SCALE, PCM_MAX, PCM_MIN, Chain, ChainConfig
from srpipe.dsp.spec.mel import Mel, MelConfig
from srpipe.dsp.spec.pitch import PitchConfig, PitchTracker
from srpipe.dsp.spec.stft import Stft
from srpipe.dsp.spec.window import sqrt_hann
from srpipe.generated import array, grid
from srpipe.metrics import mic_pair
from srpipe.scenes import room

HOP = grid.HOP_SAMPLES
FS = grid.SAMPLE_RATE_HZ
# Datasheets state sensitivity for 1 Pa; pyroomacoustics scales an image by 1 / its distance in metres.
SENSITIVITY_SPL_DB = 94.0
TALKER_REFERENCE_M = 1.0
# drv_audio reads a 32-bit I2S slot holding the sample left aligned.
SLOT_FRACTION_BITS = 31
# The chain's iSTFT hands a hop out one hop after it came in.
CHAIN_LAG_HOPS = 1
A_WEIGHT_GRID_POINTS = 4097
ROOM_STREAM, SESSION_STREAM = 1, 2
TALKER, NOISE = 0, 1
CLEAN_ORIGINS = frozenset({"public", "synth"})
ROOT_OF = {"public": "raw", "synth": "interim"}  # where a split row's item lies, by origin (KEHOACH 4.4.1)

SPEECH = Path("speech") / "vivos" / "test"
OUT = Path("playback") / "vivos_test.wav"
LEVEL_DBFS = -23.0
PEAK_MAX = 0.9
PAUSE_S = 0.6


@dataclass(frozen=True)
class Microphones:
    """Board B's pair: ch0 hears what ch1 hears through calib/bal (ch1 x gains = ch0), whose frequency, level and
    unwrapped phase set that response between the bins; self noise is white, its RMS set by an A-weighted level."""

    gains: np.ndarray
    freqs_hz: np.ndarray
    level_db: np.ndarray
    phase_rad: np.ndarray
    sensitivity_dbfs: float
    self_noise_rms: float
    pcm_shift: int


def read_balance(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """calib/bal as srhost.calib saves it: gains per bin, and their frequency, level in dB and unwrapped phase."""
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
    rows = list(csv.DictReader(lines))
    if len(rows) != grid.N_BINS:
        raise ValueError(f"{path}: {len(rows)} bins, the grid has {grid.N_BINS}")
    col = {key: np.array([float(r[key]) for r in rows]) for key in ("freq_hz", "re", "im", "level_db", "phase_deg")}
    gains = (col["re"] + 1j * col["im"]).astype(np.complex64)
    return gains, col["freq_hz"], col["level_db"], np.unwrap(np.radians(col["phase_deg"]))


def white_rms(level_dbfs_a: float) -> float:
    """RMS of white noise over 0 .. FS/2 whose A-weighted level is level_dbfs_a."""
    freqs = np.linspace(0.0, FS / 2, A_WEIGHT_GRID_POINTS)
    weight = 10.0 ** (mic_pair.a_weighting_db(freqs) / 10.0)
    return math.sqrt(10.0 ** (level_dbfs_a / 10.0) / float(np.mean(weight)))


def load_microphones(cfg: dict) -> Microphones:
    """The microphone block of the config; the balance path is from the repo root."""
    gains, freqs, level, phase = read_balance(ML_ROOT.parent / cfg["balance"])
    return Microphones(
        gains, freqs, level, phase, cfg["sensitivity_dbfs"], white_rms(cfg["self_noise_dbfs_a"]), cfg["pcm_shift"]
    )


def respond(air: np.ndarray, mics: Microphones) -> np.ndarray:
    """The pair's outputs (2, samples) for the sound at each microphone's place, given in full scale of a microphone
    at the datasheet sensitivity, ahead of their own noise: ch0 through the calib/bal response, the louder microphone
    kept at that sensitivity."""
    n = air.shape[1]
    size = sfft.next_fast_len(n + 2 * grid.FFT_SIZE)
    freqs = np.fft.rfftfreq(size, 1.0 / FS)
    level = np.interp(freqs, mics.freqs_hz, mics.level_db)
    response = 10.0 ** (level / 20.0) * np.exp(1j * np.interp(freqs, mics.freqs_hz, mics.phase_rad))
    ch0 = sfft.irfft(sfft.rfft(air[0], size) * response, size)[:n]
    louder = max(1.0, float(np.median(np.abs(mics.gains))))
    return np.stack([ch0, air[1]]) / louder


def digitise(x: np.ndarray, mics: Microphones, rng: np.random.Generator) -> np.ndarray:
    """Interleaved int16 frames (samples, 2) from the pair's outputs (2, samples) of respond: self noise, then
    drv_audio's right shift, which floors, and its saturation."""
    x = x + mics.self_noise_rms * rng.standard_normal(x.shape)
    pcm = np.floor(x * 2.0 ** (SLOT_FRACTION_BITS - mics.pcm_shift))
    return np.clip(pcm, PCM_MIN, PCM_MAX).astype(np.int16).T.copy()


def hear(air: np.ndarray, mics: Microphones, rng: np.random.Generator) -> np.ndarray:
    """Interleaved int16 frames (samples, 2) from the sound at each microphone's place: respond, then digitise."""
    return digitise(respond(air, mics), mics, rng)


def chain_scale(mics: Microphones) -> float:
    """The chain's float sample per unit of respond's output: drv_audio's shift into int16, then the chain's division
    by full scale, with neither the floor nor the saturation."""
    return 2.0 ** (SLOT_FRACTION_BITS - mics.pcm_shift) / float(PCM_FULL_SCALE)


def slot_bins(x: np.ndarray, balance_gains: np.ndarray | None) -> np.ndarray:
    """Per hop, the bins (hops, N_BINS) the ns slot of the product's chain takes for float samples (2, n) on the
    chain's scale, n a multiple of HOP: hpf, the STFT from silence, calib/bal on ch1, the mean of the pair. Filtered
    in float64, all at once, where the chain filters float32 hop by hop (tests/test_scenes_device.py)."""
    coef = hpf.coefficients().astype(np.float64)
    y = signal.lfilter(coef[:3], np.concatenate([[1.0], coef[3:]]), np.asarray(x, dtype=np.float64), axis=-1)
    padded = np.concatenate([np.zeros((y.shape[0], grid.FFT_SIZE - HOP)), y], axis=-1)
    frames = np.lib.stride_tricks.sliding_window_view(padded, grid.FFT_SIZE, axis=-1)[:, ::HOP]
    bins = np.fft.rfft(frames * sqrt_hann(grid.FFT_SIZE).astype(np.float64), axis=-1)
    if balance_gains is not None:
        bins[1] = bins[1] * np.asarray(balance_gains, dtype=np.complex128)
    return (0.5 * (bins[0] + bins[1])).astype(np.complex64)


def diffuse_pair(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Two channels (2, n) from two independent signals of one kind, with the coherence of a spherically isotropic
    field across the pair's spacing, sinc(2 f d / c) (contracts/array.yaml); ch0 is u."""
    n = len(u)
    size = sfft.next_fast_len(n)
    freqs = np.fft.rfftfreq(size, 1.0 / FS)
    coherence = np.sinc(2.0 * freqs * array.SPACING_M / array.SPEED_OF_SOUND_M_S)
    mixed = coherence * sfft.rfft(u, size) + np.sqrt(1.0 - coherence**2) * sfft.rfft(v, size)
    return np.stack([np.asarray(u, dtype=np.float64), sfft.irfft(mixed, size)[:n]])


def listen(pcm: np.ndarray, chain_cfg: ChainConfig, mel: Mel) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The chain over interleaved int16 frames: clean int16 samples, per hop its vad, level_dbfs and gain_db as int8,
    and per hop the log-mel of the clean samples (KEHOACH 3.11)."""
    chain, stft = Chain("MM", chain_cfg), Stft()
    hops = pcm.reshape(-1, HOP * array.N_MICS)
    clean = np.empty((len(hops), HOP), dtype=np.int16)
    figures = np.empty((len(hops), 3), dtype=np.int8)
    features = np.empty((len(hops), mel.cfg.n_bands), dtype=np.float32)
    for i, hop in enumerate(hops):
        frame = chain.process(hop)
        clean[i] = frame.pcm
        figures[i] = (frame.vad, frame.level_dbfs, frame.gain_db)
        features[i] = mel.log(stft.analyze(to_float(frame.pcm)))
    return clean.reshape(-1), figures, features


def build_room(cfg: dict, index: int) -> tuple[np.ndarray, dict]:
    """Room index of the bank: RIRs float32 shaped (talker and noise, ch0 and ch1, taps), and its labels."""
    rooms = cfg["rooms"]
    rng = np.random.default_rng([cfg["seed"], ROOM_STREAM, index])
    p = room.place(rooms, rng, with_interferer=True)
    rt60_s = float(rng.uniform(*rooms["rt60_s"]))
    built, measured = room.fitted_room(p, rt60_s, rooms)
    taps = max(len(h) for mic in built.rir for h in mic)
    rirs = np.zeros((2, array.N_MICS, taps), dtype=np.float32)
    for m, mic in enumerate(built.rir):
        for s, h in enumerate(mic):
            rirs[s, m, : len(h)] = h
    centre = p.mics_m.mean(axis=1)
    where = {
        name: {"angle_deg": room.angle_deg(at, p.mics_m), "distance_m": float(np.linalg.norm(at - centre))}
        for name, at in (("talker", p.talker_m), ("noise", p.interferer_m))
    }
    labels = {"room": index, "room_m": p.room_m.tolist(), "rt60_target_s": rt60_s, "rt60_measured_s": float(measured)}
    return rirs, labels | where


def _write_room(job: tuple[dict, Path, int]) -> dict:
    cfg, out, index = job
    rirs, labels = build_room(cfg, index)
    np.save(out / f"room_{index:04d}.npy", rirs)
    return labels


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def room_bank(cfg: dict, interim: Path, workers: int) -> Path:
    """The bank under interim/scenes/<name>/rooms/, built unless rooms.yaml there has the same seed and rooms."""
    out = interim / "scenes" / cfg["name"] / "rooms"
    index_file = out / "rooms.yaml"
    wanted = {"seed": cfg["seed"], "rooms": cfg["rooms"]}
    if index_file.exists():
        have = yaml.safe_load(index_file.read_text(encoding="utf-8"))
        if {key: have.get(key) for key in wanted} == wanted:
            return out
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(cfg, out, index) for index in range(cfg["rooms"]["count"])]
    with multiprocessing.get_context("spawn").Pool(workers) as pool:
        labels = pool.map(_write_room, jobs)
    sums = {f"room_{i:04d}.npy": sha256_of(out / f"room_{i:04d}.npy") for i in range(len(jobs))}
    body = {**wanted, "labels": labels, "sha256": sums}
    index_file.write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return out


def active_rms(x: np.ndarray, below_peak_db: float) -> float:
    """RMS over the hops within below_peak_db of the loudest one."""
    if len(x) < HOP or not np.any(x):
        raise ValueError(f"an utterance of {len(x)} samples carries no speech")
    hops = x[: len(x) // HOP * HOP].reshape(-1, HOP)
    return math.sqrt(float(np.mean(hops[room.active_hops(x, below_peak_db)] ** 2)))


def noise_files(cfg: dict, raw_root: Path, rejected: set[str]) -> list[list[str]]:
    """Per pool, its files as names under raw/, sorted, without those screening rejected (KEHOACH 1.2)."""
    pools = cfg["noise"]["pools"]
    listed = [sorted(str(f.relative_to(raw_root)) for f in (raw_root / p["dir"]).glob(p["glob"])) for p in pools]
    found = [[name for name in names if name not in rejected] for names in listed]
    if empty := [pool["dir"] for pool, files in zip(pools, found, strict=True) if not files]:
        raise FileNotFoundError(f"no noise under raw/ for {', '.join(empty)}")
    return found


def add_noise(
    talker: np.ndarray,
    dry: np.ndarray,
    rirs: np.ndarray,
    cfg: dict,
    pools: list[list[str]],
    raw: ItemReader,
    rng: np.random.Generator,
) -> tuple[np.ndarray, dict | None]:
    """The talker's sound plus, with the configured probability, a noise file from the room's noise source at an SNR
    drawn over the talker's active hops at ch0."""
    if rng.random() >= cfg["noise"]["probability"]:
        return talker, None
    weights = np.array([pool["weight"] for pool in cfg["noise"]["pools"]], dtype=np.float64)
    pool = int(rng.choice(len(weights), p=weights / weights.sum()))
    name = pools[pool][int(rng.integers(len(pools[pool])))]
    y = raw.read(name)
    n = talker.shape[1]
    start = int(rng.integers(max(1, len(y) - n)))
    y = np.resize(y[start:], n)
    noise = np.stack([signal.fftconvolve(y, rirs[NOISE, m])[:n] for m in range(array.N_MICS)])
    snr_db = float(rng.uniform(*cfg["noise"]["snr_db"]))
    active = room.active_hops(dry, cfg["talker"]["active_below_peak_db"])
    talker_power = float(np.mean(talker[0, : len(active) * HOP].reshape(-1, HOP)[active] ** 2))
    noise_power = float(np.mean(noise[0] ** 2))
    if not noise_power > 0:
        raise ValueError(f"{name} is silent")
    noise *= math.sqrt(talker_power / 10.0 ** (snr_db / 10.0) / noise_power)
    return talker + noise, {"pool": cfg["noise"]["pools"][pool]["dir"], "file": name, "snr_db": snr_db}


def simulate_session(
    cfg: dict,
    k: int,
    rows: list[splits.Row],
    bank: Path,
    mics: Microphones,
    pools: list[list[str]],
    readers: dict[str, ItemReader],
) -> tuple[np.ndarray, list[tuple[int, int]], dict]:
    """Session k: the interleaved int16 frames board B would capture, each utterance's [start, end) in samples, and
    the session's draws; bank_room indexes the labels of rooms.yaml, readers read raw/ and interim/ by root name."""
    rng = np.random.default_rng([cfg["seed"], SESSION_STREAM, k])
    s, t = cfg["session"], cfg["talker"]
    entry = int(rng.integers(cfg["rooms"]["count"]))
    rirs = np.load(bank / f"room_{entry:04d}.npy")
    spl_db = float(rng.uniform(*t["spl_1m_db"]))
    at = round(s["lead_s"] * FS)
    pieces, spans = [np.zeros(at)], []
    for row in rows:
        x = ramped(readers[ROOT_OF[row.origin]].read(row.item), t["edge_ramp_s"])
        jitter_db = float(rng.uniform(-t["jitter_db"], t["jitter_db"]))
        pieces.append(x * 10.0 ** (jitter_db / 20.0) / active_rms(x, t["active_below_peak_db"]))
        spans.append((at, at + len(x)))
        gap = round(float(rng.uniform(*s["gap_s"])) * FS)
        pieces.append(np.zeros(gap))
        at += len(x) + gap
    total = -(-(at + round(s["pad_s"] * FS)) // HOP) * HOP
    level = mics.sensitivity_dbfs + spl_db - SENSITIVITY_SPL_DB
    dry = np.concatenate([*pieces, np.zeros(total - at)]) * TALKER_REFERENCE_M * 10.0 ** (level / 20.0)
    talker = np.stack([signal.fftconvolve(dry, rirs[TALKER, m])[:total] for m in range(array.N_MICS)])
    air, noise = add_noise(talker, dry, rirs, cfg, pools, readers["raw"], rng)
    draws = {"session": k, "bank_room": entry, "spl_1m_db": spl_db, "noise": noise}
    return hear(air, mics, rng), spans, draws


def item_frames(span: tuple[int, int], pads_s: tuple[float, float], n_hops: int) -> tuple[int, int, int, int]:
    """Output hops [first, stop) of an utterance with its padding before and after, and the utterance's own hops
    within them."""
    before, after = (round(pad_s * FS) for pad_s in pads_s)
    first = max(0, (span[0] - before) // HOP + CHAIN_LAG_HOPS)
    stop = min(n_hops, -(-(span[1] + after) // HOP) + CHAIN_LAG_HOPS)
    return first, stop, span[0] // HOP + CHAIN_LAG_HOPS - first, -(-span[1] // HOP) + CHAIN_LAG_HOPS - first


def item_pitch(tracker: PitchTracker, clean: np.ndarray) -> np.ndarray:
    """Pitch features (hops, 3) of an item's clean int16 samples, the tracker reset at its first hop as svc_listen
    resets it on entering LENH (KEHOACH 3.12)."""
    tracker.reset()
    return np.stack([tracker.step(to_float(hop)) for hop in clean.reshape(-1, HOP)])


def _shard(job: tuple) -> list[Path]:
    cfg, roots, bank, out, shard, sessions, pools, pads_s, with_pitch = job
    mics = load_microphones(cfg["microphone"])
    chain_cfg = ChainConfig(balance_gains=mics.gains)
    mel = Mel(MelConfig(**cfg["features"]))
    tracker = PitchTracker(PitchConfig(**cfg["pitch"])) if with_pitch else None
    readers = {name: ItemReader(root) for name, root in roots.items()}
    features, figures, pcm, pitches, items, offset = [], [], [], [], [], 0
    for k, rows in sessions:
        captured, spans, draws = simulate_session(cfg, k, rows, bank, mics, pools, readers)
        clean, figs, feats = listen(captured, chain_cfg, mel)
        for row, span in zip(rows, spans, strict=True):
            first, stop, speech_first, speech_stop = item_frames(span, pads_s, len(feats))
            features.append(feats[first:stop])
            figures.append(figs[first:stop])
            pcm.append(clean[first * HOP : stop * HOP])
            if tracker is not None:
                pitches.append(item_pitch(tracker, pcm[-1]))
            where = {"frame_offset": offset, "n_frames": stop - first, "speech_frames": [speech_first, speech_stop]}
            items.append({"item": row.item, "spk": row.spk, "room": row.room, "origin": row.origin, **where, **draws})
            offset += stop - first
    stem = out / f"shard_{shard:05d}"
    written = [stem.with_suffix(s) for s in (".features.npy", ".figures.npy", ".pcm.npy", ".items.jsonl")]
    np.save(written[0], np.concatenate(features))
    np.save(written[1], np.concatenate(figures))
    np.save(written[2], np.concatenate(pcm))
    written[3].write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")
    if tracker is not None:
        written.append(stem.with_suffix(".pitch.npy"))
        np.save(written[4], np.concatenate(pitches))
    return written


def build(
    cfg: dict,
    split_file: Path,
    raw_root: Path,
    interim: Path,
    out: Path,
    workers: int = 1,
    repeats: int = 1,
    pads_s: tuple[float, float] | None = None,
    pitch: bool = False,
) -> Path:
    """Every item of split_file through the simulation into out, repeats times over, each pass in sessions of their
    own rooms, levels and noise, each item kept with pads_s before and after it (session.pad_s both sides unless
    given) and, with pitch, its pitch features from a reset at its first hop; then manifest.yaml: the config, the
    split's sha256, the repeats, the pads and pitch when asked, the room bank's sha256 and each file's."""
    rows = splits.read_split(split_file)
    if foreign := sorted({row.origin for row in rows} - CLEAN_ORIGINS):
        raise ValueError(f"{split_file}: the simulation takes clean speech, not origin {', '.join(foreign)}")
    rejected = screen.rejected(interim)
    if unscreened := [row.item for row in rows if row.item in rejected]:
        raise ValueError(f"{split_file}: {len(unscreened)} items screening rejected, e.g. {unscreened[0]}")
    bank = room_bank(cfg, interim, workers)
    rows = rows * repeats
    per = cfg["session"]["items"]
    sessions = [(k, rows[i : i + per]) for k, i in enumerate(range(0, len(rows), per))]
    per_shard = cfg["sessions_per_shard"]
    pools = noise_files(cfg, raw_root, set(rejected))
    out.mkdir(parents=True, exist_ok=True)
    pads = pads_s or (cfg["session"]["pad_s"], cfg["session"]["pad_s"])
    jobs = [
        (cfg, {"raw": raw_root, "interim": interim}, bank, out, j, sessions[i : i + per_shard], pools, pads, pitch)
        for j, i in enumerate(range(0, len(sessions), per_shard))
    ]
    with multiprocessing.get_context("spawn").Pool(workers) as pool:
        written = [p for paths in pool.map(_shard, jobs) for p in paths]
    frames = sum(len(np.load(p, mmap_mode="r")) for p in written if p.name.endswith(".features.npy"))
    body = {
        "config": cfg,
        "split": {"file": split_file.name, "sha256": sha256_of(split_file)},
        "repeats": repeats,
        **({"pads_s": list(pads)} if pads_s else {}),
        **({"pitch": True} if pitch else {}),
        "rooms_sha256": sha256_of(bank / "rooms.yaml"),
        "items": len(rows),
        "hours": round(frames * HOP / FS / 3600, 3),
        "sha256": {p.name: sha256_of(p) for p in sorted(written)},
    }
    manifest = out / "manifest.yaml"
    manifest.write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return manifest


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
    run = sub.add_parser("build", help="simulate every item of a split file into processed/<out>/")
    run.add_argument("split", type=Path, help="a split file: item, spk, room, origin per line")
    run.add_argument("out", help="under processed/, e.g. command/train_v1")
    run.add_argument("--config", type=Path, default=CONFIGS / "scenes" / "device.yaml")
    run.add_argument("--workers", type=int, default=4)
    run.add_argument("--repeats", type=int, default=1, help="passes over the split, each in other sessions")
    make = sub.add_parser("playback", help="write interim/playback/vivos_test.wav and its JSON")
    make.add_argument("--seconds", type=float, default=60.0)
    args = parser.parse_args(argv)
    paths = data_paths()
    if args.command == "build":
        cfg = load_yaml(args.config)
        out = paths["processed"] / args.out
        print(build(cfg, args.split, paths["raw"], paths["interim"], out, args.workers, args.repeats))
        return 0
    signal_, items = playback(paths["raw"] / SPEECH, args.seconds)
    out = paths["interim"] / OUT
    write_wav(out, signal_)
    meta = {"source": str(SPEECH), "level_dbfs": LEVEL_DBFS, "pause_s": PAUSE_S, "items": items}
    out.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{out}: {len(signal_) / grid.SAMPLE_RATE_HZ:.1f} s, {len(items)} utterances")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
