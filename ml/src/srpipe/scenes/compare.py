"""The comparison set of KEHOACH 3.16, prepared from configs/afe/compare.yaml into interim/scenes/<name>/<item>/.

prepare writes each item's input.wav (the array's channels, int16, the grid's rate), raw_ch0.wav, the variants made
outside this repo, and item.json with the item's source, text and known segments; a mixture adds its clean and noise
parts, the only draw, seeded by the item's name. render writes the pc_* variants with the Python chain and board B's
calib/bal; score measures every variant written. Run: python -m srpipe.scenes.compare {prepare,render,score}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing
import zlib
from pathlib import Path

import numpy as np
import soundfile as sf
import yaml

from srpipe.core.audio_io import INT16_SCALE
from srpipe.core.config import CONFIGS, ML_ROOT, data_paths, load_yaml
from srpipe.dsp.afe import chain
from srpipe.generated import afe, array, grid
from srpipe.metrics import sisdr, stoi
from srpipe.scenes import device, refs

CONFIG = CONFIGS / "afe" / "compare.yaml"
MEASUREMENTS = ML_ROOT.parent / "docs" / "measurements" / "afe"
HOP = grid.HOP_SAMPLES
FS = grid.SAMPLE_RATE_HZ
BOARD = Path("device") / "board_b"
SOURCES = ("session", "raw", "mix")
PREPARE, OUTSIDE = "prepare", "outside"
RAW_CH0 = "raw_ch0"
TEXT_FROM_PROMPT = "prompt"
PROMPT_SEPARATOR = ":"
TEXT_SCOPES = ("item", "segment")
AUTO = "auto"
PCM_MAX = np.iinfo(np.int16).max
PCM_MIN = np.iinfo(np.int16).min
ENERGY_FLOOR = 1e-12


def validate(cfg: dict) -> None:
    """Refuse a config whose items or variants the builders could read two ways."""
    names = [item["name"] for item in cfg["items"]]
    if len(set(names)) != len(names):
        raise ValueError(f"item names repeat: {sorted(n for n in set(names) if names.count(n) > 1)}")
    variants = cfg["variants"]
    if variants.get(RAW_CH0, {}).get("by") != PREPARE:
        raise ValueError(f"variant {RAW_CH0} must be made by {PREPARE}")
    for item in cfg["items"]:
        given = [key for key in SOURCES if key in item]
        if len(given) != 1:
            raise ValueError(f"{item['name']}: needs exactly one of {SOURCES}, has {given}")
        if item.get("text_scope", "item") not in TEXT_SCOPES:
            raise ValueError(f"{item['name']}: text_scope is one of {TEXT_SCOPES}")
        for variant in item.get(OUTSIDE, {}):
            if variants.get(variant, {}).get("by") != OUTSIDE:
                raise ValueError(f"{item['name']}: {variant} is not a variant made {OUTSIDE}")


def read_pcm(path: Path, channels: int) -> np.ndarray:
    """int16 samples shaped (frames, channels); refuse another rate or channel count."""
    pcm, rate = sf.read(str(path), dtype="int16", always_2d=True)
    if rate != FS or pcm.shape[1] != channels:
        raise ValueError(f"{path}: {rate} Hz, {pcm.shape[1]} channel(s); expected {FS} Hz, {channels}")
    return pcm


def write_pcm(path: Path, pcm: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), pcm, FS, subtype="PCM_16")


def session_pcm(raw: Path, session: str) -> np.ndarray:
    """The session's microphone files side by side, in the array's channel order."""
    parts = [read_pcm(raw / BOARD / session / f"{ch}.wav", 1)[:, 0] for ch in array.CHANNELS]
    if len({len(p) for p in parts}) != 1:
        raise ValueError(f"{session}: channels differ in length")
    return np.stack(parts, axis=1)


def prompt_text(raw: Path, session: str) -> str:
    """The session's prompt after its first colon, which separates the recording note from what was read."""
    prompt = json.loads((raw / BOARD / session / "session.json").read_text(encoding="utf-8"))["prompt"]
    return prompt.split(PROMPT_SEPARATOR, 1)[-1].strip()


def hop_levels_db(x: np.ndarray) -> np.ndarray:
    """Mean power of each whole hop of int16 samples, in dB of full scale."""
    hops = x[: len(x) // HOP * HOP].astype(np.float64).reshape(-1, HOP) / INT16_SCALE
    return 10.0 * np.log10(np.mean(hops * hops, axis=1) + ENERGY_FLOOR)


def active_hops(x: np.ndarray, rule: dict) -> np.ndarray:
    """Hops louder than the item's floor by the rule's margin."""
    levels = hop_levels_db(x)
    return levels > np.percentile(levels, rule["floor_percentile"]) + rule["speech_above_floor_db"]


def runs(mask: np.ndarray) -> list[list[int]]:
    """[start, end) of every run of True."""
    edges = np.flatnonzero(np.diff(np.concatenate([[0], mask.astype(np.int8), [0]])))
    return [[int(a), int(b)] for a, b in zip(edges[::2], edges[1::2], strict=True)]


def to_hops(seconds: float) -> int:
    return round(seconds * FS / HOP)


def to_seconds(spans: list[list[int]]) -> list[list[float]]:
    return [[round(a * HOP / FS, 3), round(b * HOP / FS, 3)] for a, b in spans]


def detected_segments(x: np.ndarray, rule: dict) -> dict[str, list[list[float]]]:
    """Speech segments and noise-only spans of int16 samples by the energy rule, in seconds."""
    active = active_hops(x, rule)
    speech: list[list[int]] = []
    for run in runs(active):
        if speech and run[0] - speech[-1][1] < to_hops(rule["merge_gap_s"]):
            speech[-1][1] = run[1]
        else:
            speech.append(run)
    speech = [s for s in speech if s[1] - s[0] >= to_hops(rule["min_speech_s"])]
    near = np.zeros(len(active), dtype=bool)
    margin = to_hops(rule["noise_margin_s"])
    for a, b in speech:
        near[max(0, a - margin) : b + margin] = True
    noise = [r for r in runs(~near) if r[1] - r[0] >= to_hops(rule["min_noise_s"])]
    return {"speech_s": to_seconds(speech), "noise_s": to_seconds(noise)}


def span_samples(span_s: list[float], length: int, what: str) -> tuple[int, int]:
    start, end = (round(s * FS) for s in span_s)
    if not 0 <= start < end <= length:
        raise ValueError(f"{what}: span {span_s} s lies outside its {length / FS:.3f} s")
    return start, end


def mixture(raw: Path, spec: dict, rule: dict, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, dict]:
    """The speech span, and noise from a seeded start in its span scaled so that ch0's SNR over the active hops of the
    clean speech is spec's; returns clean int16, noise as float samples of the int16 scale, and what was drawn."""
    speech = session_pcm(raw, spec["speech"])
    a, b = span_samples(spec["speech_span_s"], len(speech), spec["speech"])
    clean = speech[a:b]
    noise_all = session_pcm(raw, spec["noise"])
    lo, hi = span_samples(spec["noise_span_s"], len(noise_all), spec["noise"])
    if hi - lo < len(clean):
        raise ValueError(f"{spec['noise']}: span {spec['noise_span_s']} s is shorter than the speech")
    start = lo + int(rng.integers(hi - lo - len(clean) + 1))
    noise = noise_all[start : start + len(clean)].astype(np.float64)
    active = np.repeat(active_hops(clean[:, 0], rule), HOP)
    speech_power = float(np.mean(clean[: len(active), 0].astype(np.float64)[active] ** 2))
    noise_power = float(np.mean(noise[: len(active), 0][active] ** 2))
    if not speech_power > 0 or not noise_power > 0:
        raise ValueError(f"{spec['speech']} or {spec['noise']} is silent over the active hops")
    gain = math.sqrt(speech_power / noise_power / 10.0 ** (spec["snr_db"] / 10.0))
    drawn = {"noise_start_s": round(start / FS, 6), "noise_gain_db": round(20.0 * math.log10(gain), 3)}
    return clean, noise * gain, drawn


def snr_db(clean: np.ndarray, noise: np.ndarray, rule: dict) -> float:
    """ch0's SNR over the active hops of the clean part, as mixture draws it."""
    active = np.repeat(active_hops(clean[:, 0], rule), HOP)
    speech_power = np.mean(clean[: len(active), 0].astype(np.float64)[active] ** 2)
    return float(10.0 * np.log10(speech_power / np.mean(noise[: len(active), 0][active] ** 2)))


def to_pcm(x: np.ndarray, what: str) -> np.ndarray:
    y = np.round(x)
    if y.max() > PCM_MAX or y.min() < PCM_MIN:
        raise ValueError(f"{what} clips int16")
    return y.astype(np.int16)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def item_rng(seed: int, name: str) -> np.random.Generator:
    return np.random.default_rng([seed, zlib.crc32(name.encode("utf-8"))])


def prepare_item(item: dict, cfg: dict, raw: Path, out: Path) -> dict:
    """Write one item's files into out; return its item.json body."""
    rule, name = cfg["segments"], item["name"]
    body: dict = {"name": name}
    files: dict[str, np.ndarray] = {}
    if "mix" in item:
        clean, noise, drawn = mixture(raw, item["mix"], rule, item_rng(cfg["seed"], name))
        files |= {"input": to_pcm(clean + noise, name), "clean": clean, "noise": to_pcm(noise, f"{name} noise")}
        body["source"] = {"mix": item["mix"] | drawn}
        text_session = item["mix"]["speech"]
        segments = detected_segments(clean[:, 0], rule)
    else:
        if "session" in item:
            files["input"] = session_pcm(raw, item["session"])
            body["source"] = {"session": item["session"]}
        else:
            files["input"] = read_pcm(raw / item["raw"], array.N_MICS)
            body["source"] = {"raw": item["raw"]}
        text_session = item.get("session")
        given = item.get("segments")
        segments = detected_segments(files["input"][:, 0], rule) if given == AUTO else given
    text = item.get("text")
    if text == TEXT_FROM_PROMPT:
        if text_session is None:
            raise ValueError(f"{name}: text from a prompt needs a board session")
        text = prompt_text(raw, text_session)
    files[RAW_CH0] = files["input"][:, :1]
    for variant, rel in item.get(OUTSIDE, {}).items():
        files[variant] = read_pcm(raw / rel, 1)
    for stem, pcm in files.items():
        write_pcm(out / f"{stem}.wav", pcm)
    body |= {
        "channels": int(files["input"].shape[1]),
        "rate_hz": FS,
        "samples": len(files["input"]),
        "text": text,
        "text_scope": item.get("text_scope", "item"),
        "segments": {"speech_s": None, "noise_s": None} | segments if segments else None,
        "variants": {v: {"samples": len(files[v])} for v in [RAW_CH0, *item.get(OUTSIDE, {})]},
        "sha256": {f"{stem}.wav": sha256_of(out / f"{stem}.wav") for stem in files},
    }
    (out / "item.json").write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return body


def prepare(cfg: dict, raw: Path, root: Path) -> Path:
    """Every item of cfg under root/<item>/, then root/manifest.yaml with each item's file digests."""
    validate(cfg)
    bodies = [prepare_item(item, cfg, raw, root / item["name"]) for item in cfg["items"]]
    manifest = root / "manifest.yaml"
    listing = {"name": cfg["name"], "seed": cfg["seed"], "items": {b["name"]: b["sha256"] for b in bodies}}
    manifest.write_text(yaml.safe_dump(listing, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return manifest


def pc_config(cfg: dict, variant: dict, gains: np.ndarray) -> chain.ChainConfig:
    """The Python chain of a pc variant: the config's modules, ns_omlsa when it asks, board B's balance."""
    ns = ("ns_omlsa",) if variant["ns"] == "omlsa" else ()
    return chain.ChainConfig(
        modules=tuple(cfg["chain"]["modules"]) + ns,
        balance_gains=gains,
        ns_floor_db=variant.get("ns_floor_db", afe.NS_FLOOR_DB),
        spatial=variant["spatial"],
    )


def board_gains() -> np.ndarray:
    """calib/bal of board B, the file configs/scenes/device.yaml names, as the board and espsr_compare take it."""
    spec = load_yaml(CONFIGS / "scenes" / "device.yaml")["microphone"]["balance"]
    return device.read_balance(ML_ROOT.parent / spec)[0]


def _render_item(job: tuple[dict, Path]) -> list[str]:
    cfg, folder = job
    gains = board_gains()
    done = []
    for name, variant in cfg["variants"].items():
        if variant.get("by") == "pc":
            chain.render(folder / "input.wav", folder / f"{name}.wav", pc_config(cfg, variant, gains))
            done.append(name)
    return done


def render(cfg: dict, root: Path) -> list[str]:
    """Every pc variant of every prepared item, items in parallel."""
    jobs = [(cfg, root / item["name"]) for item in cfg["items"]]
    with multiprocessing.get_context("spawn").Pool(cfg["workers"]) as pool:
        written = pool.map(_render_item, jobs)
    return [f"{job[1].name}: {', '.join(names)}" for job, names in zip(jobs, written, strict=True)]


SIDE_FILES = ("input", "clean", "noise")
COSTS_FILE = "board.json"
MEASURES = ("lag_ms", "noise_db", "speech_db", "snr_gain_db", "si_sdr_db", "stoi", "sig", "bak", "ovrl")
COSTS = ("status", "us_mean", "us_peak", "internal_bytes", "psram_bytes")


def read_mono(path: Path) -> np.ndarray:
    """A mono int16 wav, or ch0 of a wider one, as float64 in full scale."""
    return read_pcm_any(path)[:, 0].astype(np.float64) / INT16_SCALE


def read_pcm_any(path: Path) -> np.ndarray:
    pcm, rate = sf.read(str(path), dtype="int16", always_2d=True)
    if rate != FS:
        raise ValueError(f"{path}: {rate} Hz, the set is {FS} Hz")
    return pcm


def lag_samples(y: np.ndarray, ref: np.ndarray, most: int, window: int) -> int:
    """Samples y runs behind ref: the peak of their cross-correlation over the first window samples, within most."""
    n = min(len(y), len(ref), window)
    size = 1 << int(np.ceil(np.log2(2 * n)))
    xc = np.fft.irfft(np.fft.rfft(y[:n], size) * np.conj(np.fft.rfft(ref[:n], size)), size)
    lags = np.concatenate([np.arange(0, most + 1), np.arange(-most, 0)])
    return int(lags[np.argmax(np.abs(xc[lags]))])


def aligned(y: np.ndarray, lag: int, n: int) -> np.ndarray:
    """y moved back by lag samples to line up with its reference, cut or zero-padded to n."""
    out = y[lag:] if lag >= 0 else np.concatenate([np.zeros(-lag), y])
    return np.pad(out[:n], (0, max(0, n - len(out))))


def span_db(x: np.ndarray, spans_s: list[list[float]] | None) -> float | None:
    """Mean power over the samples of the spans in dB of full scale; None without spans."""
    if not spans_s:
        return None
    parts = [x[round(a * FS) : round(b * FS)] for a, b in spans_s]
    power = np.mean(np.concatenate(parts) ** 2)
    return float(10.0 * np.log10(power + ENERGY_FLOOR))


def measure(y: np.ndarray, raw: np.ndarray, item: dict, clean: np.ndarray | None, rule: dict) -> dict:
    """One variant against the item: its delay, its level change on noise-only and speech spans against raw_ch0,
    their difference as a gain in SNR, and SI-SDR and STOI against the clean part when the item is a mixture."""
    lag = lag_samples(y, raw, round(rule["align_max_s"] * FS), round(rule["align_window_s"] * FS))
    y = aligned(y, lag, len(raw))
    segments = item.get("segments") or {}
    out: dict = {"lag_ms": round(1000 * lag / FS, 1)}
    for key, spans in (("noise_db", segments.get("noise_s")), ("speech_db", segments.get("speech_s"))):
        mine, theirs = span_db(y, spans), span_db(raw, spans)
        out[key] = None if mine is None else round(mine - theirs, 2)
    if out["noise_db"] is not None and out["speech_db"] is not None:
        out["snr_gain_db"] = round(out["speech_db"] - out["noise_db"], 2)
    if clean is not None:
        n = min(len(y), len(clean))
        out["si_sdr_db"] = round(sisdr.si_sdr_db(y[:n], clean[:n]), 2)
        out["stoi"] = round(stoi.stoi_score(y[:n], clean[:n]), 3)
    return out


def listen_copy(y: np.ndarray, item: dict, level_dbfs: float) -> np.ndarray:
    """y at level_dbfs over the item's speech spans, or over all of it, kept under full scale."""
    spans = (item.get("segments") or {}).get("speech_s") or [[0.0, len(y) / FS]]
    level = span_db(y, spans)
    gain = 10 ** ((level_dbfs - level) / 20)
    return y * min(gain, 0.99 / max(np.max(np.abs(y)), ENERGY_FLOOR))


def score_item(cfg: dict, folder: Path, listen: Path) -> dict[str, dict]:
    """Every variant written for one item: its measures, its board costs when it ran on the board, and a copy to
    listen to in listen/<item>/."""
    item = json.loads((folder / "item.json").read_text(encoding="utf-8"))
    raw = read_mono(folder / f"{RAW_CH0}.wav")
    clean = read_mono(folder / "clean.wav") if (folder / "clean.wav").exists() else None
    costs = json.loads((folder / COSTS_FILE).read_text()) if (folder / COSTS_FILE).exists() else {}
    rows = {}
    for wav in sorted(folder.glob("*.wav")):
        if wav.stem in SIDE_FILES:
            continue
        y = read_mono(wav)
        rows[wav.stem] = measure(y, raw, item, clean, cfg["score"]) | {k: costs.get(wav.stem, {}).get(k) for k in COSTS}
        out = listen / folder.name / wav.name
        out.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out), listen_copy(y, item, cfg["listen"]["level_dbfs"]), FS, subtype="PCM_16")
    for name, cost in costs.items():
        rows.setdefault(name, {k: cost.get(k) for k in COSTS})
    return rows


def score(cfg: dict, root: Path, listen: Path, cache: Path) -> dict[str, dict[str, dict]]:
    """score_item over every item of the set, by item then variant, with DNSMOS P.835 of every variant written."""
    found = {item["name"]: score_item(cfg, root / item["name"], listen) for item in cfg["items"]}
    wavs = {
        (item, variant): root / item / f"{variant}.wav"
        for item, rows in found.items()
        for variant in rows
        if (root / item / f"{variant}.wav").exists()
    }
    ids = {f"{item}/{variant}": path for (item, variant), path in wavs.items()}
    mos = refs.dnsmos(ids, cfg["refs"]["dnsmos"], cache, cache / "afe_ref" / "work")
    for item, variant in wavs:
        found[item][variant] |= {k: round(v, 3) for k, v in mos[f"{item}/{variant}"].items()}
    return found


def write_rows(found: dict[str, dict[str, dict]], path: Path) -> None:
    """Every item and variant with every measure and cost, one CSV row each."""
    fields = ["item", "variant", *MEASURES, *COSTS]
    lines = [",".join(fields)]
    for item, rows in found.items():
        for variant, row in sorted(rows.items()):
            values = [item, variant, *("" if row.get(k) is None else str(row[k]) for k in (*MEASURES, *COSTS))]
            lines.append(",".join(values))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["prepare", "render", "score"])
    parser.add_argument("--out", type=Path, help="score: the CSV of every row", default=MEASUREMENTS / "compare.csv")
    parser.add_argument("--config", type=Path, default=CONFIG)
    args = parser.parse_args(argv)
    cfg, paths = load_yaml(args.config), data_paths()
    root = paths["interim"] / "scenes" / cfg["name"]
    if args.step == "render":
        print("\n".join(render(cfg, root)))
        return 0
    if args.step == "score":
        write_rows(score(cfg, root, paths["cache"] / "listen" / cfg["name"], paths["cache"]), args.out)
        print(f"wrote {args.out}")
        return 0
    manifest = prepare(cfg, paths["raw"], root)
    for name, digests in yaml.safe_load(manifest.read_text(encoding="utf-8"))["items"].items():
        print(f"{name}: {', '.join(digests)}")
    print(f"wrote {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
