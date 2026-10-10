"""The speaker verification survey of KEHOACH 3.17 on the PC: extractors of ml/spk_ref embed the owner's Gate 3 command
windows through board B, the command split's test speakers through the board simulation near 1 m and 3 m with no
noise source, and whole VIVOS test utterances with no board. The owner's model is the mean of k enrolment windows.
Figures: EER, and the owner's windows kept where at most rule.impostors_passing of the impostors' pass, in all and
on those the locked command run accepts as the command said. Run: python -m srpipe.scenes.speaker [--workers N]"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing
from collections import defaultdict
from dataclasses import dataclass
from itertools import pairwise

import numpy as np
import soundfile as sf
import yaml

from srpipe.core import corpus, splits
from srpipe.core.audio_io import ItemReader, to_float
from srpipe.core.config import CONFIGS, ML_ROOT, data_paths, device_of, load_yaml, on_contract_pitch
from srpipe.dsp.afe.chain import ChainConfig
from srpipe.dsp.spec.mel import Mel, MelConfig
from srpipe.generated import grid
from srpipe.scenes import device, refs
from srpipe.tasks import command
from srpipe.tasks.command import eval as gate

CONFIG = CONFIGS / "scenes" / "speaker.yaml"
HOP = grid.HOP_SAMPLES
SESSION_STREAM = 1  # the impostors' sessions: speakers' utterances and rooms
ENROL_STREAM = 2  # the owner's enrolment sets
LANGUAGE = "vivos"


@dataclass(frozen=True)
class Window:
    """One window of float samples at the grid's rate, its speaker, the group it is reported in, and for the owner's
    whether the command run accepts it as the command said."""

    spk: str
    group: str
    samples: np.ndarray
    accepted: bool = False


def board_cfg(cfg: dict) -> dict:
    """The model-config shape device_of reads: the survey's board simulation with the contract's front end."""
    return {"features": cfg["features"]}


def window_of(clean: np.ndarray, start: int, end: int) -> np.ndarray:
    """The clean int16 samples of hops start..end as float32."""
    return to_float(clean[start * HOP : (end + 1) * HOP]).astype(np.float32)


def owner_windows(cfg: dict, paths: dict) -> list[Window]:
    """Every command window Gate 3 scores of the owner's sessions, grouped by session date and distance: a session
    saying a listed command, a window over an utterance the chain finds in the session alone, not left out; each
    decided by owner.command's run as board B decides it, its pitch dims held, at that NVS's thresholds."""
    spec = load_yaml(command.CONFIG)["eval"]["board"]
    decide = cfg["owner"]["command"]
    net = gate.load_ctc(ML_ROOT / decide["run"])
    heard = gate.holding(gate.ctc_heard, gate.HOLDS[decide["hold"]])
    run_cfg = on_contract_pitch(net.cfg)
    listed = json.loads(command.COMMANDS.read_text(encoding="utf-8"))["commands"]
    command_of = {tuple(corpus.sounds(c["text"])): c["id"] for c in listed}
    dev = device_of(run_cfg)
    chain_cfg = ChainConfig(balance_gains=device.load_microphones(dev["microphone"]).gains)
    mel = Mel(MelConfig(**dev["features"]))
    left = {(s, int(k)) for s, _, k in (n.partition("#") for n in spec.get("left_out_utterances", []))}
    out = []
    for r, clean, vad, features, pitch in gate.heard_sessions(run_cfg, spec, paths):
        expected = gate.expected_of(r["kind"], r["prompt"], command_of)
        if r["spk"] != cfg["owner"]["spk"] or expected == gate.REJECT:
            continue
        spans = device.utterances(vad)
        owned = gate.owners(spans, gate.said_alone(r, paths, chain_cfg, mel))
        group = f"{r['session'][:8]} {r['distance_cm']}"
        cuts = zip(device.command_cut(spans), owned, gate.ctc_windows(features, pitch, spans), strict=True)
        for (start, end), said, x in cuts:
            if said and not any((r["session"], k) in left for k in said):
                h = heard(net, x)
                acted = h.command == expected and h.accepted(decide["reject_permille"], decide["margin_permille"])
                out.append(Window(r["spk"], group, window_of(clean, start, end), acted))
    return out


def _impostor_session(job: tuple) -> list[Window]:
    dev, k, rows, bank, room, group, roots = job
    mics = device.load_microphones(dev["microphone"])
    readers = {name: ItemReader(root) for name, root in roots.items()}
    captured, spans, draws, _ = device.simulate_session(dev, k, rows, bank, mics, [], readers, room=room)
    chain_cfg = ChainConfig(balance_gains=mics.gains, agc_start_db=draws.get("agc_start_db"))
    clean, figures, _ = device.listen(captured, chain_cfg, Mel(MelConfig(**dev["features"])))
    said = [(s // HOP + device.CHAIN_LAG_HOPS, -(-e // HOP) + device.CHAIN_LAG_HOPS) for s, e in spans]
    utterances = device.utterances(figures[:, 0].astype(bool))
    return [
        Window(rows[0].spk, group, window_of(clean, start, end))
        for (start, end), (first, last) in zip(device.command_cut(utterances), utterances, strict=True)
        if any(first < stop and begin <= last for begin, stop in said)
    ]


def impostor_jobs(cfg: dict, paths: dict) -> list[tuple]:
    """One session of per_speaker utterances in a room of each distance group for every test speaker with enough
    utterances, drawn from the survey's seed; the simulation's own noise source off."""
    spec = cfg["impostors"]
    dev = device_of(board_cfg(cfg))
    bank = device.room_bank(dev, paths["interim"], 1)
    labels = yaml.safe_load((bank / "rooms.yaml").read_text(encoding="utf-8"))["labels"]
    distance = np.array([label["talker"]["distance_m"] for label in labels])
    rooms = {name: np.flatnonzero((distance >= lo) & (distance <= hi)) for name, (lo, hi) in spec["rooms_m"].items()}
    by = defaultdict(list)
    for row in splits.read_split(paths["splits"] / spec["split"]):
        by[row.spk].append(row)
    speakers = sorted(s for s, rows in by.items() if len(rows) >= spec["min_utterances"])
    quiet = dev | {"seed": cfg["seed"], "noise": dev["noise"] | {"probability": 0.0}}
    roots = {"raw": paths["raw"], "interim": paths["interim"]}
    rng = np.random.default_rng([cfg["seed"], SESSION_STREAM])
    jobs = []
    for spk in speakers:
        for group, pool in rooms.items():
            picked = rng.choice(len(by[spk]), min(spec["per_speaker"], len(by[spk])), replace=False)
            rows = [by[spk][int(i)] for i in sorted(picked)]
            jobs.append((quiet, len(jobs), rows, bank, int(rng.choice(pool)), group, roots))
    return jobs


def impostor_windows(cfg: dict, paths: dict, workers: int) -> list[Window]:
    """The windows of every session of impostor_jobs, simulated by workers processes."""
    with multiprocessing.get_context("spawn").Pool(workers) as pool:
        return [w for part in pool.map(_impostor_session, impostor_jobs(cfg, paths)) for w in part]


def language_windows(cfg: dict, paths: dict) -> list[Window]:
    """Every utterance of the baseline corpus, whole, its speaker from its folder."""
    out = []
    for folder in sorted(p for p in (paths["raw"] / cfg["language"]["corpus"]).iterdir() if p.is_dir()):
        for wav in sorted(folder.glob("*.wav")):
            x, rate = sf.read(wav, dtype="float32")
            if rate != grid.SAMPLE_RATE_HZ:
                raise ValueError(f"{wav}: {rate} Hz, the grid is {grid.SAMPLE_RATE_HZ}")
            out.append(Window(folder.name, LANGUAGE, x))
    return out


def unit(x: np.ndarray) -> np.ndarray:
    return x / np.linalg.norm(x, axis=-1, keepdims=True)


def eer(target: np.ndarray, nontarget: np.ndarray) -> float:
    """The equal error rate of scores accepted at or above a threshold: where the share of targets under it meets the
    share of non-targets at or above it, averaged there."""
    thresholds = np.unique(np.concatenate([target, nontarget]))
    miss = np.searchsorted(np.sort(target), thresholds, side="left") / len(target)
    false = 1.0 - np.searchsorted(np.sort(nontarget), thresholds, side="left") / len(nontarget)
    k = int(np.argmin(np.abs(miss - false)))
    return float((miss[k] + false[k]) / 2.0)


def threshold_at(nontarget: np.ndarray, passing: float) -> float:
    """The lowest threshold, accepting scores above it, that lets at most passing of the non-targets through."""
    ranked = np.sort(nontarget)[::-1]
    allowed = math.floor(passing * len(ranked))
    return float(ranked[allowed]) if allowed < len(ranked) else -math.inf


def pair_eer(embeddings: np.ndarray, speakers: list[str]) -> float:
    """EER over every pair of windows: same speaker against different speakers."""
    e = unit(embeddings)
    scores = e @ e.T
    same = np.equal.outer(np.array(speakers), np.array(speakers))
    upper = np.triu(np.ones_like(same), k=1)
    return eer(scores[same & upper], scores[~same & upper])


def owner_figures(
    embeddings: np.ndarray, windows: list[Window], cfg: dict, edges_s: list[float]
) -> dict[int, dict[str, float]]:
    """Per enrolment count, the median over the draws of: EER of the owner's other windows against the impostors',
    the share of them kept at the rule's threshold in all, per group and per window length, the impostors passing in
    each group, and the accepted windows kept at each share of rule.curve."""
    owner = cfg["owner"]
    enrol_group = f"{owner['enrol']['date']} {owner['enrol']['distance_cm']}"
    mine = [i for i, w in enumerate(windows) if w.spk == owner["spk"]]
    pool = [i for i in mine if windows[i].group == enrol_group]
    others = [i for i, w in enumerate(windows) if w.spk != owner["spk"]]
    e = unit(embeddings)
    passing = cfg["rule"]["impostors_passing"]
    out = {}
    for count in owner["counts"]:
        rng = np.random.default_rng([cfg["seed"], ENROL_STREAM, count])
        rows = defaultdict(list)
        for _ in range(owner["draws"]):
            enrolled = set(rng.choice(pool, count, replace=False).tolist())
            model = unit(e[sorted(enrolled)].mean(axis=0))
            tested = [i for i in mine if i not in enrolled]
            target, nontarget = e[tested] @ model, e[others] @ model
            threshold = threshold_at(nontarget, passing)
            rows["eer"].append(eer(target, nontarget))
            rows["kept"].append(float(np.mean(target > threshold)))
            for group in sorted({windows[i].group for i in tested}):
                held = [k for k, i in enumerate(tested) if windows[i].group == group]
                rows[f"kept {group}"].append(float(np.mean(target[held] > threshold)))
                acted = [k for k in held if windows[tested[k]].accepted]
                rows[f"kept accepted {group}"].append(float(np.mean(target[acted] > threshold)))
            acted = [k for k, i in enumerate(tested) if windows[i].accepted]
            rows["kept accepted"].append(float(np.mean(target[acted] > threshold)))
            for share in cfg["rule"]["curve"]:
                rows[f"kept accepted at {share}"].append(float(np.mean(target[acted] > threshold_at(nontarget, share))))
            seconds = np.array([len(windows[i].samples) / grid.SAMPLE_RATE_HZ for i in tested])
            bins = np.digitize(seconds, edges_s)
            for b in range(len(edges_s) + 1):
                if np.any(bins == b):
                    rows[f"kept length {b}"].append(float(np.mean(target[bins == b] > threshold)))
            for group in sorted({windows[i].group for i in others}):
                held = [k for k, i in enumerate(others) if windows[i].group == group]
                rows[f"passing {group}"].append(float(np.mean(nontarget[held] > threshold)))
        out[count] = {key: float(np.median(v)) for key, v in rows.items()} | {"kept min": float(np.min(rows["kept"]))}
    return out


def length_names(edges_s: list[float]) -> list[str]:
    bounds = [0.0, *edges_s, math.inf]
    return [
        f"< {hi:g} s" if lo == 0 else (f">= {lo:g} s" if hi == math.inf else f"{lo:g}-{hi:g} s")
        for lo, hi in pairwise(bounds)
    ]


def report(results: dict, windows: list[Window], cfg: dict) -> str:
    """Markdown tables of the survey's figures, percentages."""
    owner, groups = cfg["owner"], sorted({w.group for w in windows if w.spk == cfg["owner"]["spk"]})
    impostor_groups = sorted({w.group for w in windows if w.spk != owner["spk"]})
    counts = defaultdict(int)
    for w in windows:
        counts[(w.spk == owner["spk"], w.group)] += 1
    speakers = len({w.spk for w in windows if w.spk != owner["spk"]})
    accepted = {g: sum(w.accepted for w in windows if w.spk == owner["spk"] and w.group == g) for g in groups}
    lines = [
        "owner windows (accepted by the command run): "
        f"{', '.join(f'{g} cm {counts[(True, g)]} ({accepted[g]})' for g in groups)}; "
        f"impostor windows of {speakers} "
        f"speakers: {', '.join(f'{g} {counts[(False, g)]}' for g in impostor_groups)}",
        "",
        "| extractor | VIVOS test, no board, EER | test speakers through the simulation, windows, EER |",
        "|---|---|---|",
    ]
    for name, r in results.items():
        lines.append(f"| {name} | {100 * r['language_eer']:.2f}% | {100 * r['impostor_eer']:.2f}% |")
    passing = 100 * cfg["rule"]["impostors_passing"]
    lines += [
        "",
        f"Owner against impostors, median of {owner['draws']} enrolments; kept at <= {passing:g}% of impostors "
        "passing:",
        "",
        f"| extractor | k | EER | kept (min) | {' | '.join(f'{g} cm' for g in groups)} | "
        f"{' | '.join(f'passing {g}' for g in impostor_groups)} |",
        "|---" * (4 + len(groups) + len(impostor_groups)) + "|",
    ]
    for name, r in results.items():
        for count, f in r["owner"].items():
            kept = " | ".join(f"{100 * f.get(f'kept {g}', math.nan):.1f}%" for g in groups)
            passed = " | ".join(f"{100 * f[f'passing {g}']:.1f}%" for g in impostor_groups)
            lines.append(
                f"| {name} | {count} | {100 * f['eer']:.2f}% | {100 * f['kept']:.1f}% ({100 * f['kept min']:.1f}%) "
                f"| {kept} | {passed} |"
            )
    lines += [
        "",
        f"Owner windows the command run accepts, kept at <= {passing:g}% of impostors passing, median:",
        "",
        f"| extractor | k | kept | {' | '.join(f'{g} cm' for g in groups)} |",
        "|---" * (3 + len(groups)) + "|",
    ]
    for name, r in results.items():
        for count, f in r["owner"].items():
            kept = " | ".join(f"{100 * f.get(f'kept accepted {g}', math.nan):.1f}%" for g in groups)
            lines.append(f"| {name} | {count} | {100 * f['kept accepted']:.1f}% | {kept} |")
    curve = cfg["rule"]["curve"]
    lines += [
        "",
        "Owner windows the command run accepts, kept at each share of impostors passing, median:",
        "",
        f"| extractor | k | {' | '.join(f'{100 * c:g}%' for c in curve)} |",
        "|---" * (2 + len(curve)) + "|",
    ]
    for name, r in results.items():
        for count, f in r["owner"].items():
            kept = " | ".join(f"{100 * f[f'kept accepted at {c}']:.1f}%" for c in curve)
            lines.append(f"| {name} | {count} | {kept} |")
    names = length_names(cfg["window_s"])
    lines += [
        "",
        "Owner kept by window length, largest k:",
        "",
        f"| extractor | {' | '.join(names)} |",
        "|---" * (1 + len(names)) + "|",
    ]
    for name, r in results.items():
        f = r["owner"][max(r["owner"])]
        lines.append(
            f"| {name} | "
            + " | ".join(f"{100 * f.get(f'kept length {b}', math.nan):.1f}%" for b in range(len(names)))
            + " |"
        )
    return "\n".join(lines)


def survey(cfg: dict, paths: dict, workers: int) -> tuple[dict, list[Window]]:
    """Every extractor's figures, and the owner's and impostors' windows they came from."""
    owner, impostors = owner_windows(cfg, paths), impostor_windows(cfg, paths, workers)
    language = language_windows(cfg, paths)
    board_windows = owner + impostors
    results = {}
    work = paths["cache"] / "spk_ref" / "work"
    for name, spec in cfg["extractors"].items():
        e = refs.embed(name, spec, [w.samples for w in board_windows + language], paths["cache"], work)
        board_e, language_e = e[: len(board_windows)], e[len(board_windows) :]
        results[name] = {
            "language_eer": pair_eer(language_e, [w.spk for w in language]),
            "impostor_eer": pair_eer(board_e[len(owner) :], [w.spk for w in impostors]),
            "owner": owner_figures(board_e, board_windows, cfg, cfg["window_s"]),
        }
    return results, board_windows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workers", type=int, default=8, help="processes simulating the impostors' sessions")
    args = parser.parse_args(argv)
    cfg, paths = load_yaml(CONFIG), data_paths()
    results, windows = survey(cfg, paths, args.workers)
    out = paths["cache"] / "spk_ref" / "work" / "survey.yaml"
    out.write_text(yaml.safe_dump(results, sort_keys=False), encoding="utf-8")
    print(report(results, windows, cfg))
    print(f"\n{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
