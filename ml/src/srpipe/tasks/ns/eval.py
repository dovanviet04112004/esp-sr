"""Score a run's ns candidates against the OM-LSA floor on a held set (E9-T4, KEHOACH 3.9, 3.15).

Each variant's gains, from the capture's slot power, go onto the talker alone and the rest alone through the grid's
iSTFT, as scenes.ns scores OM-LSA: noise and speech taken down, SNR and SI-SDR gained after settle_s, the cold start
apart, by foreground class, SNR, speech level and corpus. Recorded, never deciding (KEHOACH 3.15).
Run: python -m srpipe.tasks.ns.eval score [--run RUN] [--set val|test]"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import os
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import torch
import yaml

from srpipe.core.audio_io import INT16_SCALE
from srpipe.core.config import data_paths, load_device, load_yaml
from srpipe.dsp.afe import ns_omlsa
from srpipe.dsp.spec import fft
from srpipe.dsp.spec.window import sqrt_hann
from srpipe.generated import grid
from srpipe.metrics.sisdr import si_sdr_db
from srpipe.scenes import device
from srpipe.scenes import vad as vad_scenes
from srpipe.tasks.ns import data, model
from srpipe.tasks.ns.postproc import bins as slot_gains

HOP = grid.HOP_SAMPLES
OMLSA = "omlsa"
WINDOWS = ("settled", "cold")
FIGURES = ("noise_down_db", "pause_noise_down_db", "speech_down_db", "snr_gain_db", "si_sdr_gain_db")
SLICES = ("class", "snr", "level", "corpus")
_nets: dict[str, torch.nn.Module] = {}


def last_run(paths: dict) -> Path:
    """The run whose metrics.yaml was written last: every candidate kept its weights there."""
    done = sorted((paths["artifacts"] / "ns" / "runs").glob("*/metrics.yaml"), key=lambda p: p.stat().st_mtime)
    if not done:
        raise FileNotFoundError("no finished ns run: make ns-train")
    return done[-1].parent


def load_nets(run: Path, cfg: dict) -> None:
    """The run's candidates on the CPU, once per worker process."""
    torch.set_num_threads(1)
    for name in cfg["train"]["candidates"]:
        net = model.build(cfg, name)
        net.load_state_dict(torch.load(run / name / "model.pt", map_location="cpu"))
        _nets[name] = net.eval()


def synthesis(bins: np.ndarray) -> np.ndarray:
    """The grid's iSTFT of a whole sequence (hops, N_BINS), equal to Istft hop by hop, one hop late."""
    frames = fft.inverse(bins, grid.FFT_SIZE) * sqrt_hann(grid.FFT_SIZE)
    out = frames[:, :HOP].copy()
    out[1:] += frames[:-1, HOP:]
    return out.reshape(-1)


def hop_energy(x: np.ndarray) -> np.ndarray:
    return np.sum(x.reshape(-1, HOP).astype(np.float64) ** 2, axis=1)


def ratio_db(num: float, den: float) -> float:
    return 10.0 * math.log10(num / den) if num > 0 and den > 0 else math.nan


def window_figures(parts: dict[str, np.ndarray], labels: np.ndarray, within: np.ndarray) -> dict[str, float]:
    """scenes.ns.figures over the output hops within; SI-SDR of the output against the talker over them. NaN where
    the window holds no such hop."""
    s, n, s_ref, n_ref = (hop_energy(parts[k]) for k in ("speech", "noise", "speech_ref", "noise_ref"))
    # Output hop k carries input hop k - 1.
    talk = np.concatenate([[False], labels[:-1].astype(bool)])
    speech, pause = within & talk, within & ~talk
    out = {
        "noise_down_db": ratio_db(n_ref[within].sum(), n[within].sum()),
        "pause_noise_down_db": ratio_db(n_ref[pause].sum(), n[pause].sum()),
        "speech_down_db": ratio_db(s_ref[speech].sum(), s[speech].sum()),
        "snr_gain_db": ratio_db(s[speech].sum(), n[speech].sum()) - ratio_db(s_ref[speech].sum(), n_ref[speech].sum()),
        "si_sdr_gain_db": math.nan,
    }
    samples = np.repeat(within, HOP)
    reference = parts["speech_ref"][samples]
    if speech.any() and np.any(reference):
        output = (parts["speech"] + parts["noise"])[samples]
        mixture = (parts["speech_ref"] + parts["noise_ref"])[samples]
        out["si_sdr_gain_db"] = si_sdr_db(output, reference) - si_sdr_db(mixture, reference)
    return out


def floor_name(floor_db: float | None) -> str:
    return "none" if floor_db is None else f"{floor_db:g}"


def variants(power: np.ndarray, floors: list[float | None]) -> dict[str, np.ndarray]:
    """One example's 257 gains a hop for every variant: OM-LSA from a reset, each candidate at each floor."""
    omlsa = ns_omlsa.Omlsa()
    out = {OMLSA: np.stack([omlsa.process(p).gain for p in power])}
    with torch.no_grad():
        for name, net in _nets.items():
            gains = net(torch.from_numpy(power)[None])[0][0].numpy()
            for floor in floors:
                out[f"{name}@{floor_name(floor)}"] = slot_gains.floored(gains, floor)
    return out


def _score_shard(task: tuple) -> list[dict]:
    stem, balance, spec, meta = task
    capture, talker, scale, labels = (
        np.load(f"{stem}.{part}.npy", mmap_mode="r") for part in ("capture", "talker", "scale", "vad")
    )
    settle = round(spec["settle_s"] * grid.SAMPLE_RATE_HZ / HOP)
    rows = []
    for k in range(len(scale)):
        x = device.slot_bins(np.asarray(capture[k]).T.astype(np.float64) / INT16_SCALE, balance)
        s = device.slot_bins(np.asarray(talker[k], dtype=np.float32).T * scale[k], balance)
        noise_bins = x - s
        refs = {"speech_ref": synthesis(s), "noise_ref": synthesis(noise_bins)}
        settled = np.arange(len(refs["speech_ref"]) // HOP) >= settle
        level = level_dbfs(refs["speech_ref"], labels[k]) if meta[k]["class"] != "no_talker" else None
        slices = meta[k] | {"level": bucket(level, spec["level_edges_dbfs"])}
        for name, g in variants((np.abs(x) ** 2).astype(np.float32), spec["floors_db"]).items():
            parts = refs | {"speech": synthesis(g * s), "noise": synthesis(g * noise_bins)}
            for window, within in zip(WINDOWS, (settled, ~settled), strict=True):
                rows.append({"variant": name, "window": window, **slices, **window_figures(parts, labels[k], within)})
    return rows


def level_dbfs(speech: np.ndarray, labels: np.ndarray) -> float | None:
    """The talker's level at the slot over its speech hops, in dB of full scale."""
    talk = np.concatenate([[False], labels[:-1].astype(bool)])
    energy = hop_energy(speech)[talk]
    return 10.0 * math.log10(float(energy.mean()) / HOP) if len(energy) and energy.mean() > 0 else None


def bucket(value: float | None, edges: list[float]) -> str | None:
    """The span of edges holding value, as '[a, b)', '< a' or '>= b'."""
    if value is None:
        return None
    at = int(np.searchsorted(edges, value, side="right"))
    if at == 0:
        return f"< {edges[0]:g}"
    return f">= {edges[-1]:g}" if at == len(edges) else f"[{edges[at - 1]:g}, {edges[at]:g})"


def corpus_of(mixer: data.Mixer, items: list[str], window: int) -> str | None:
    """The corpus holding most of an example window's speech samples."""
    s, n = mixer.stream(0), mixer.n
    begin, end = window * n, (window + 1) * n
    held: dict[str, int] = defaultdict(int)
    for u, at in zip(s.order, s.start, strict=True):
        overlap = min(end, int(at) + int(mixer.speech.length[u])) - max(begin, int(at))
        if overlap > 0:
            held[items[int(u)].split("/")[1]] += overlap
    return max(held, key=held.get) if held else None


def example_meta(mixer: data.Mixer, items: list[str], draws: list[dict], snr_edges_db: list[float]) -> list[dict]:
    """Each example's foreground class, SNR bucket and corpus; the worker adds its speech level."""
    return [
        {
            "class": "no_talker" if not d["talker"] else d.get("class", "tone_only"),
            "snr": bucket(d.get("snr_db"), snr_edges_db),
            "corpus": corpus_of(mixer, items, d["window"]) if d["talker"] else None,
        }
        for d in draws
    ]


def mean_or_none(values: list[float]) -> float | None:
    finite = [v for v in values if math.isfinite(v)]
    return float(np.mean(finite)) if finite else None


def summarise(rows: list[dict]) -> dict:
    """Mean of every figure per variant and window, over all examples and within each slice, with its count."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["variant"], r["window"], "all", "all")].append(r)
        for key in SLICES:
            if r[key] is not None:
                groups[(r["variant"], r["window"], key, r[key])].append(r)
    out: dict = {}
    for (variant, window, key, value), rs in sorted(groups.items()):
        means = {f: mean_or_none([r[f] for r in rs]) for f in FIGURES}
        out.setdefault(variant, {}).setdefault(window, {}).setdefault(key, {})[value] = {"examples": len(rs)} | means
    return out


def number(x: float | None) -> str:
    return "—" if x is None else vad_scenes.number(x, 1)


def table(summary: dict, key: str) -> str:
    """Vietnamese rows of the settled window: variants down, one slice value per column."""
    values = sorted({v for variant in summary.values() for v in variant["settled"].get(key, {})})
    lines = [f"| Biến thể | {' | '.join(values)} |", "|---" * (len(values) + 1) + "|"]
    for name, variant in summary.items():
        cells = []
        for value in values:
            m = variant["settled"].get(key, {}).get(value)
            figures = ("noise_down_db", "speech_down_db", "snr_gain_db")
            cells.append(" / ".join(number(m[f]) for f in figures) if m else number(None))
        lines.append(f"| {name} | {' | '.join(cells)} |")
    lines.append("")
    lines.append("Ô: nhiễu giảm dB (mọi bước) / tiếng nói giảm dB (bước nói) / SNR tăng dB (bước nói), sau settle_s.")
    return "\n".join(lines)


def score(run: Path, cfg: dict, dev: dict, paths: dict, role: str, workers: int) -> dict:
    """The run's variants on a stored held set, mixed as its manifest records, written to <run>/eval/<role>.yaml."""
    folder = data.set_dir(paths, cfg, role)
    manifest = yaml.safe_load((folder / "manifest.yaml").read_text(encoding="utf-8"))
    held_cfg = cfg | {key: manifest["config"][key] for key in ("mix", "sets")}
    mixer = data.Mixer(held_cfg, dev, paths, role, held_cfg["sets"]["seed"])
    items = (data.pool_dir(paths, cfg, role) / "speech_items.txt").read_text(encoding="utf-8").splitlines()
    tasks = []
    for listing in sorted(folder.glob("shard_*.items.jsonl")):
        stem = listing.with_name(listing.name.removesuffix(".items.jsonl"))
        draws = [json.loads(line) for line in listing.read_text(encoding="utf-8").splitlines()]
        tasks.append(
            (stem, mixer.mics.gains, cfg["eval"], example_meta(mixer, items, draws, cfg["eval"]["snr_edges_db"]))
        )
    # Spawned workers read these as they load numpy: one BLAS thread each, not one a core in each of them.
    os.environ.update(dict.fromkeys(("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"), "1"))
    spawn = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(workers, mp_context=spawn, initializer=load_nets, initargs=(run, cfg)) as pool:
        rows = [r for shard in pool.map(_score_shard, tasks) for r in shard]
    summary = summarise(rows)
    out = run / "eval" / f"{role}.yaml"
    out.parent.mkdir(exist_ok=True)
    out.write_text(yaml.safe_dump(summary, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("what", choices=["score"])
    parser.add_argument("--run", type=Path, help="a run under artifacts/ns/runs; the last finished one by default")
    parser.add_argument("--set", dest="role", choices=data.HELD, default="val")
    args = parser.parse_args(argv)
    paths = data_paths()
    run = args.run or last_run(paths)
    cfg = load_yaml(run / "config.resolved.yaml")
    summary = score(run, cfg, load_device(cfg["device"]), paths, args.role, cfg["eval"]["workers"])
    for key in ("all", *SLICES):
        print(f"\n{args.role}, {key}\n\n{table(summary, key)}")
    print(f"\n{run / 'eval' / f'{args.role}.yaml'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
