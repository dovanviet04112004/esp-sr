"""How a ctc run hears tone, three checks of KEHOACH 3.11 (E11-T23). val: a checked sắc or nặng syllable of val, a
sentence's first or a later one, moved by Praat onto the other tone's contour, formants kept, or resynthesised as it
was, its session simulated again: how far the pitch dims move and whether the net's CTC score turns to the other tone.
owner: the owner's bật / tắt sessions with each first word so moved, both microphones (measurements/command.md 12.17).
places: tone right at first and later syllables of val, pitch dims as simulated and held at their mean (12.18).
Run: python -m srpipe.tasks.command.ctc.tone_flip {val,owner,places} <run under ml/>"""

from __future__ import annotations

import argparse
import csv
import json
import multiprocessing
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from signal import SIG_IGN, SIGINT
from signal import signal as set_handler

import numpy as np
import torch
import yaml
from torch.nn import functional

from srpipe.core import audio_io, corpus, repitch, screen, splits
from srpipe.core.audio_io import ItemReader
from srpipe.core.config import ML_ROOT, data_paths, device_of, load_yaml, on_contract_pitch
from srpipe.dsp.afe.chain import ChainConfig
from srpipe.dsp.spec.mel import Mel, MelConfig
from srpipe.generated import grid, lang_vi
from srpipe.lang import g2p
from srpipe.lang.normalize import LangError, normalize
from srpipe.scenes import device
from srpipe.tasks import command
from srpipe.tasks.command import ctc
from srpipe.tasks.command import eval as gate
from srpipe.tasks.command.ctc import train
from srpipe.tasks.command.ctc.postproc.ctc_score import BLANK
from srpipe.tasks.wake.data import sentence_units
from srpipe.tts import CONFIG as TTS_CONFIG
from srpipe.tts import engines

CHECKS = ("val", "owner", "places")
CONDITIONS = ("recorded", "same", "swap")  # owner: as recorded, F0 kept, F0 moved
HOLDS = {"voicing": (0,), "f0": (1, 2), "pitch": (0, 1, 2)}  # pitch dims POV, log F0, delta held, by name
PLACES = ("first", "later")
MODES = ("same", "swap")  # resynthesised on its own contour, or on the other tone's
OTHER_TONE = dict(zip(lang_vi.CHECKED_TONES, reversed(lang_vi.CHECKED_TONES), strict=True))
N_PITCH = 3  # the pitch dims close every hop's features
ALIGN_GAIN = 0.5  # headroom: a clip read at full scale may peak past int16
FS, HOP, LAG = grid.SAMPLE_RATE_HZ, grid.HOP_SAMPLES, device.CHAIN_LAG_HOPS


@dataclass(frozen=True)
class Target:
    """A syllable the check moves: its clip's index in the session, its place in the sentence and tone, its voiced
    stretch in the clip in seconds, the other tone's contour for it in Hz, and the clip's units past the blank with
    the index of this syllable's tone among them."""

    row: int
    place: str
    tone: str
    stretch: tuple[float, float]
    target_hz: np.ndarray
    units: np.ndarray
    tone_at: int


class Replaced:
    """A reader whose items in dry are read from there, any other through reader once and then from read."""

    def __init__(self, reader: ItemReader, dry: dict[str, np.ndarray], read: dict[str, np.ndarray]) -> None:
        self.reader, self.dry, self.done = reader, dry, read

    def read(self, item: str) -> np.ndarray:
        if item in self.dry:
            return self.dry[item]
        if item not in self.done:
            self.done[item] = self.reader.read(item)
        return self.done[item]


@dataclass(frozen=True)
class Simulation:
    """A finished build's simulation, to run its sessions again."""

    cfg: dict
    cut: str
    pads_s: tuple[float, float]
    speeds: tuple[float, ...]
    bank: Path
    mics: device.Microphones
    pools: list[list[str]]
    floor: device.Floor | None
    readers: dict[str, ItemReader]
    chain_cfg: ChainConfig
    mel: Mel
    pitch: Callable[[np.ndarray], np.ndarray]


def simulation(built: dict, paths: dict) -> Simulation:
    """The simulation of a build by its manifest."""
    cfg, interim = built["config"], paths["interim"]
    mics = device.load_microphones(cfg["microphone"])
    floor_cfg = cfg["microphone"].get("floor")
    return Simulation(
        cfg,
        built.get("cut", "pads"),
        tuple(built.get("pads_s") or (cfg["session"]["pad_s"],) * 2),
        tuple(built.get("speeds", ())),
        device.room_bank(cfg, interim, 1),
        mics,
        device.noise_files(cfg, paths["raw"], set(screen.rejected(interim))),
        device.load_floor(floor_cfg, paths["raw"], mics.pcm_shift) if floor_cfg else None,
        {"raw": ItemReader(paths["raw"]), "interim": ItemReader(interim)},
        ChainConfig(balance_gains=mics.gains),
        Mel(MelConfig(**cfg["features"])),
        device.pitch_of(cfg["pitch"]),
    )


@dataclass(frozen=True)
class Heard:
    """A session simulated again: its clips' spans in samples, the session's pitch features, and each window's first
    hop, clips by index and features, log-mel then pitch."""

    spans: list[tuple[int, int]]
    pitch: np.ndarray
    windows: list[tuple[int, list[int], np.ndarray]]


def simulated(
    sim: Simulation, k: int, rows: list[splits.Row], dry: dict[str, np.ndarray], read: dict | None = None
) -> Heard:
    """Session k as its build simulated it, the clips of dry read from there; files read go into read, so the
    session simulated again decodes none twice (the simulation copies what it reads before changing it)."""
    done = {} if read is None else read
    readers = {name: Replaced(reader, dry, done) for name, reader in sim.readers.items()}
    captured, spans, draws, heard = device.simulate_session(
        sim.cfg, k, rows, sim.bank, sim.mics, sim.pools, readers, sim.floor, sim.speeds
    )
    clean, _, feats = device.listen(captured, replace(sim.chain_cfg, agc_start_db=draws.get("agc_start_db")), sim.mel)
    pitch = sim.pitch(clean)
    cut = device.cut_items(sim.cut, spans, sim.pads_s, heard)
    windows = [
        (first, held, np.concatenate([feats[first:stop], pitch[first:stop]], axis=1).astype(np.float32))
        for first, stop, held, _ in cut
    ]
    return Heard(spans, pitch, windows)


def checked_places(said: list[g2p.Syllable]) -> dict[str, int]:
    """The syllable each place takes in a sentence: the first when its rhyme is checked with sắc or nặng, and the
    first such syllable after it."""
    checked = [k for k, s in enumerate(said) if s.coda in lang_vi.CHECKED_CODAS and s.tone in lang_vi.CHECKED_TONES]
    places = {"first": 0} if checked and checked[0] == 0 else {}
    return places | ({"later": later[0]} if (later := [k for k in checked if k > 0]) else {})


def unit_ids(said: list[g2p.Syllable]) -> tuple[np.ndarray, list[int]]:
    """A sentence's units past the CTC blank, as training reads them, and the index of each syllable's tone unit."""
    units, tone_at = [], []
    for s in said:
        units += [g2p.UNIT_ID[u] + 1 for u in s.units()]
        tone_at.append(len(units) - 1)
    return np.asarray(units, dtype=np.int64), tone_at


def other_tone_lead(net: gate.Ctc, x: np.ndarray, t: Target) -> float:
    """The CTC log-probability of t's units with its tone made the other one, less that of its own units, in a
    window as the device runs it."""
    other = t.units.copy()
    other[t.tone_at] = g2p.UNIT_ID[OTHER_TONE[t.tone]] + 1
    window, frames = gate.normalised_window(net, x)
    with torch.no_grad():
        log_probs = net.model(window).log_softmax(1)[0, :, :frames].T[:, None, :].repeat(1, 2, 1)
    labels = torch.from_numpy(np.stack([t.units, other]))
    lengths = torch.tensor([frames, frames]), torch.tensor([len(t.units)] * 2)
    nll = functional.ctc_loss(log_probs, labels, *lengths, blank=BLANK, reduction="none")
    return float(nll[0] - nll[1])


def stored_windows(folder: Path, built: dict) -> tuple[dict[str, int], dict[int, list[np.ndarray]]]:
    """A finished build's windows: the session of each clip a window holds alone, and per session a reader of each
    window's features, log-mel then pitch, in order."""
    alone: dict[str, int] = {}
    placed: dict[int, list] = defaultdict(list)
    for name in sorted(n for n in built["sha256"] if n.endswith(".items.jsonl")):
        stem = str(folder / name).removesuffix(".items.jsonl")
        for line in (folder / name).read_text(encoding="utf-8").splitlines():
            item = yaml.safe_load(line)
            if "clips" not in item:
                alone[item["item"]] = item["session"]
            placed[item["session"]].append((stem, item["frame_offset"], item["n_frames"]))
    return alone, placed


def stored_features(placed: list[tuple[str, int, int]]) -> list[np.ndarray]:
    out = []
    for stem, offset, hops in placed:
        mel = np.load(stem + ".features.npy", mmap_mode="r")[offset : offset + hops]
        pitch = np.load(stem + ".pitch.npy", mmap_mode="r")[offset : offset + hops]
        out.append(np.concatenate([mel, pitch], axis=1).astype(np.float32))
    return out


_worker: dict = {}


def _start(run: Path, built: dict, paths: dict, spec: dict) -> None:
    # Ctrl-C reaches every process of the terminal: the parent alone stops the check, terminating its workers.
    set_handler(SIGINT, SIG_IGN)
    torch.set_num_threads(1)
    _worker.update(net=gate.load_ctc(run), sim=simulation(built, paths), rcfg=repitch.repitch_config(spec))


def moved_clips(
    dry: dict[int, np.ndarray],
    rows: list[splits.Row],
    targets: list[Target],
    swapped: str | None,
    rcfg: repitch.RepitchConfig,
) -> dict[str, np.ndarray]:
    """Each target's clip resynthesised over every target it holds, onto the other tone's contour at place swapped
    and on its own elsewhere, so a session moved at one place differs from its control only there."""
    out: dict[str, np.ndarray] = {}
    for t in sorted(targets, key=lambda t: (t.row, t.stretch)):
        item = rows[t.row].item
        target_hz = t.target_hz if t.place == swapped else None
        out[item] = repitch.resynthesised(out.get(item, dry[t.row]), t.stretch, target_hz, rcfg)
    return out


def _flips(job: tuple[int, list[splits.Row], list[Target]]) -> list[dict]:
    """Every target of one session: its two leads, kept and moved, and how far its pitch dims moved."""
    k, rows, targets = job
    net, sim, rcfg = _worker["net"], _worker["sim"], _worker["rcfg"]
    dry = {t.row: sim.readers[device.ROOT_OF[rows[t.row].origin]].read(rows[t.row].item) for t in targets}
    places = sorted({t.place for t in targets}, key=PLACES.index)
    read: dict[str, np.ndarray] = {}
    heard = {
        place: simulated(sim, k, rows, moved_clips(dry, rows, targets, place, rcfg), read) for place in [None, *places]
    }
    out = []
    for t in targets:
        alone = {
            mode: [x for _, held, x in heard[at].windows if held == [t.row]]
            for mode, at in zip(MODES, (None, t.place), strict=True)
        }
        row = {"item": rows[t.row].item, "place": t.place, "tone": t.tone}
        if not all(alone.values()):
            out.append(row | {"unheard": True})
            continue
        start = heard[None].spans[t.row][0]
        first, last = ((start + round(s * FS)) // HOP + LAG for s in t.stretch)
        moved = (heard[t.place].pitch[first : last + 1] - heard[None].pitch[first : last + 1]) / net.std[-N_PITCH:]
        leads = {mode: other_tone_lead(net, alone[mode][0], t) for mode in MODES}
        rms = np.sqrt(np.mean(moved**2, axis=0))
        out.append(row | leads | {"moved": float(np.sqrt(np.mean(rms**2))), "moved_dims": rms.tolist()})
    return out


def summary(rows: list[dict]) -> list[dict]:
    """Per place and tone moved from, then per place both tones: syllables, the median pitch move, the share whose other
    tone leads with F0 kept and with it moved, the share added, and the median of how far moving F0 moved the lead."""
    out = []
    groups = [(place, tone) for place in PLACES for tone in (*lang_vi.CHECKED_TONES, "both")]
    for place, tone in groups:
        picked = [r for r in rows if r["place"] == place and tone in (r["tone"], "both") and "unheard" not in r]
        if not picked:
            continue
        kept = float(np.mean([r["same"] > 0 for r in picked]))
        swapped = float(np.mean([r["swap"] > 0 for r in picked]))
        out.append(
            {
                "place": place,
                "tone": tone,
                "syllables": len(picked),
                "moved": float(np.median([r["moved"] for r in picked])),
                "leads_kept": kept,
                "leads_moved": swapped,
                "added": swapped - kept,
                "lead_moved": float(np.median([r["swap"] - r["same"] for r in picked])),
            }
        )
    return out


def branch(rows: list[dict], share: float) -> str:
    """The step KEHOACH 3.11 takes on the check, first syllable against a later one."""
    both = {r["place"]: r for r in rows if r["tone"] == "both"}
    first, later = both.get("first"), both.get("later")
    if first is None or later is None or later["added"] <= 0:
        return "no later syllable follows F0: look at the check itself before reading it"
    if first["moved"] < share * later["moved"]:
        return "the pitch dims hardly follow F0 at the first syllable: train command/v8 alone, then check it"
    if first["added"] >= share * later["added"]:
        return "the net follows F0 at the first syllable: the tracker fails on board; train command/v8 alone"
    return "the pitch dims follow F0 at the first syllable and the net does not: train command/v8 with the tone swap"


def table(rows: list[dict]) -> str:
    lines = [
        "| place | tone moved from | syllables | pitch moved, train sd | other tone leads: kept / moved | added |"
        " lead moved |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['place']} | {r['tone']} | {r['syllables']} | {r['moved']:.2f} | {r['leads_kept']:.2f} / "
            f"{r['leads_moved']:.2f} | {r['added']:+.2f} | {r['lead_moved']:+.2f} |"
        )
    return "\n".join(lines)


def flip_check(run: Path, paths: dict, spec: dict) -> dict:
    """The check on run, every count and the step KEHOACH 3.11 takes, written to <run>/tone_flip.yaml."""
    net = gate.load_ctc(run)
    rcfg = repitch.repitch_config(spec)
    version = net.cfg["split"]["version"]
    folder = paths["processed"] / "command" / version / "val"
    built = yaml.safe_load((folder / "manifest.yaml").read_text(encoding="utf-8"))
    sim = simulation(built, paths)
    rows = splits.read_split(paths["splits"] / "command" / version / "val.txt") * built["repeats"]
    per = built["config"]["session"]["items"]
    sessions = [rows[i : i + per] for i in range(0, len(rows), per)]
    alone, placed = stored_windows(folder, built)
    region = lang_vi.DIALECTS.index(net.cfg["train"]["dialect"])
    listed = {r.item for r in rows}
    text_of = {
        c.item: c.text
        for c in screen.kept_clips(load_yaml(screen.CONFIG), paths, "speech")
        if c.item in listed and c.text
    }
    counts: Counter = Counter()
    picks = []
    for k, session in enumerate(sessions):
        for i, row in enumerate(session):
            if alone.get(row.item) != k or row.item not in text_of:
                continue
            try:
                said_text = normalize(text_of[row.item], region)
                said = g2p.syllables(said_text, region)
            except LangError:
                continue
            if places := checked_places(said):
                picks.append((k, i, row, said_text, said, places))
    counts["sentences"] = len(picks)
    work = paths["cache"] / "tone_flip" / version
    asked, dry = [], {}
    for n, (_, _, row, said_text, _, _) in enumerate(picks):
        dry[n] = sim.readers[device.ROOT_OF[row.origin]].read(row.item)
        wav = work / "wav" / f"c{n:05d}.wav"
        audio_io.write_wav(wav, dry[n] * ALIGN_GAIN)
        asked.append({"id": f"c{n:05d}", "wav": str(wav), "text": said_text})
    times = engines.align(asked, load_yaml(TTS_CONFIG), work, paths["cache"])
    tracks, by_speaker = {}, defaultdict(list)
    for n, (_, _, row, _, said, _) in enumerate(picks):
        if len(times.get(f"c{n:05d}", [])) == len(said):
            tracks[n] = repitch.track(dry[n], rcfg)
            by_speaker[row.spk].append(tracks[n])
    counts["aligned"] = len(tracks)
    median_of = {spk: repitch.median_hz(t) for spk, t in by_speaker.items()}
    found, contours = [], defaultdict(list)
    for n, (k, i, row, _, said, places) in enumerate(picks):
        if n not in tracks or not median_of[row.spk] > 0:
            continue
        for place, j in places.items():
            word = times[f"c{n:05d}"][j]
            stretch = repitch.voiced_stretch(tracks[n], word["start"], word["end"], rcfg)
            if stretch is None:
                counts[f"{place} unvoiced"] += 1
                continue
            contours[(place, said[j].tone)].append(
                repitch.contour_st(tracks[n], stretch, median_of[row.spk], rcfg.points)
            )
            found.append((k, i, row, said, place, j, stretch))
    templates = {key: np.median(np.stack(c), axis=0) for key, c in contours.items()}
    jobs: dict[int, list[Target]] = defaultdict(list)
    for k, i, row, said, place, j, stretch in found:
        tone = said[j].tone
        if (place, OTHER_TONE[tone]) not in templates:
            continue
        units, tone_at = unit_ids(said)
        target_hz = repitch.contour_hz(templates[(place, OTHER_TONE[tone])], median_of[row.spk])
        jobs[k].append(Target(i, place, tone, stretch, target_hz, units, tone_at[j]))
    first_k = min(jobs)
    again = simulated(sim, first_k, sessions[first_k], {})
    kept = stored_features(placed[first_k])
    same = len(again.windows) == len(kept) and all(
        np.array_equal(w[2], s) for w, s in zip(again.windows, kept, strict=True)
    )
    if not same:
        raise ValueError(f"val session {first_k} simulated again differs from {folder}: the check would not see val")
    print(f"{run}: session {first_k} simulated again equals its build; {len(jobs)} sessions to move", flush=True)
    results = []
    context = multiprocessing.get_context("spawn")
    with context.Pool(spec["workers"], initializer=_start, initargs=(run, built, paths, spec)) as pool:
        listed_jobs = [(k, sessions[k], targets) for k, targets in sorted(jobs.items())]
        for done, rows_out in enumerate(pool.imap_unordered(_flips, listed_jobs), start=1):
            results += rows_out
            if done % 20 == 0 or done == len(listed_jobs):
                print(f"{done}/{len(listed_jobs)} sessions", flush=True)
    counts["unheard"] = sum("unheard" in r for r in results)
    rows_summary = summary(results)
    return {
        "run": str(run),
        "split": version,
        "counts": dict(counts),
        "templates_st": {
            f"{place} {tone}": [round(float(v), 2) for v in c] for (place, tone), c in sorted(templates.items())
        },
        "summary": rows_summary,
        "branch": branch(rows_summary, spec["follows_share"]),
        "syllables": results,
    }


@dataclass(frozen=True)
class Spoken:
    """An utterance of the owner the check moves: its session, its index among the session's utterances alone, the
    tone of its first word, and that word's voiced stretch in seconds of the session's recording."""

    session: str
    said: int
    tone: str
    stretch: tuple[float, float]


def owned_windows(spans: gate.Spans, said: gate.Spans, gone: set[int]) -> dict[int, int]:
    """Per utterance alone, the window that holds it and nothing else; one left out, or shared, has none."""
    owned = gate.owners(spans, said)
    holders: dict[int, list[int]] = defaultdict(list)
    for w, ks in enumerate(owned):
        for k in ks:
            holders[k].append(w)
    return {k: ws[0] for k, ws in holders.items() if len(ws) == 1 and owned[ws[0]] == [k] and k not in gone}


def held(x: np.ndarray, dims: tuple[int, ...], mean: np.ndarray) -> np.ndarray:
    """A window with the pitch dims at dims, counted from the first of the three, at their train mean, which the net
    reads as nothing there."""
    out = x.copy()
    cols = [x.shape[1] - N_PITCH + d for d in dims]
    out[:, cols] = mean[cols]
    return out


def owner_windows(
    cfg: dict,
    rows: list[dict],
    paths: dict,
    manifest: list[dict],
    said_of: dict[str, gate.Spans],
    gone_of: dict[str, set[int]],
    pcm_of: dict[str, np.ndarray] | None = None,
) -> tuple[dict[tuple[str, int], np.ndarray], dict[tuple[str, int], tuple[int, int]]]:
    """The window of each utterance alone of rows, the one that holds it alone as Gate 3 runs the sitting for a run of
    config cfg, and its hops; sessions of pcm_of heard as those samples. No net is needed: every checkpoint of a run
    is decided over the same windows."""
    windows, hops = {}, {}
    for r, _, vad, features, pitch in gate.heard_rows(cfg, rows, paths, manifest, pcm_of):
        spans = device.utterances(vad)
        cut_out = gate.ctc_windows(features, pitch, spans)
        for k, w in owned_windows(spans, said_of[r["session"]], gone_of.get(r["session"], set())).items():
            windows[(r["session"], k)], hops[(r["session"], k)] = cut_out[w], spans[w]
    return windows, hops


def decide(net: gate.Ctc, windows: dict, dims: tuple[int, ...] = ()) -> dict:
    """Each window decided as the device decides it, with the pitch dims at dims held at their train mean."""
    return {key: gate.ctc_heard(net, held(x, dims, net.mean) if dims else x) for key, x in windows.items()}


def tally(decided: dict, row_of: dict, command_of: dict, reject: int, margin: int) -> list[dict]:
    """Per day, distance and word, the utterances of decided, those right, and those accepted at reject and margin."""
    every: dict[tuple, Counter] = {}
    for (session, _), h in decided.items():
        r = row_of[session]
        expected = command_of[tuple(corpus.sounds(r["prompt"]))]
        c = every.setdefault((f"{session[6:8]}/{session[4:6]}", r["distance_cm"], r["prompt"].split()[0]), Counter())
        c["utterances"] += 1
        c["right"] += h.command == expected
        c["accepted"] += h.command == expected and h.accepted(reject, margin)
    return [{"day": d, "cm": cm, "word": w} | dict(c) for (d, cm, w), c in every.items()]


def moved_session(pcm: np.ndarray, spoken: list[Spoken], target_of: dict, rcfg: repitch.RepitchConfig) -> np.ndarray:
    """A session's (samples, mics) int16 with each spoken first word resynthesised on every microphone, onto its
    target contour when target_of holds one for it and on its own otherwise."""
    x = pcm.astype(np.float64) / audio_io.INT16_SCALE
    for s in sorted(spoken, key=lambda s: s.stretch):
        for m in range(x.shape[1]):
            x[:, m] = repitch.resynthesised(x[:, m], s.stretch, target_of.get((s.session, s.said)), rcfg)
    return audio_io.to_int16(x)


@dataclass(frozen=True)
class OwnerHeard:
    """The owner's sessions heard once for a run: per condition each utterance's window, the utterances whose first
    word moves, the session rows, the owner's median F0 and the median contour of each tone in semitones over it."""

    windows: dict[str, dict[tuple[str, int], np.ndarray]]
    spoken: list[Spoken]
    row_of: dict[str, dict]
    median_hz: float
    templates: dict[str, np.ndarray]


def owner_heard(cfg: dict, spec: dict, paths: dict) -> OwnerHeard:
    """The owner's sessions of spec's owner_sessions whose prompt opens with a checked sắc or nặng syllable, through
    the chain of a run of config cfg: as recorded, and with every utterance's first word resynthesised on both
    microphones on its own contour and on the other tone's median contour of the owner's."""
    rcfg = repitch.repitch_config(spec)
    board = load_yaml(command.CONFIG)["eval"]["board"]
    manifest = list(csv.DictReader((paths["manifests"] / board["manifest"]).open(encoding="utf-8")))
    region = lang_vi.DIALECTS.index(cfg["train"]["dialect"])
    tone_of = {}
    for r in manifest:
        if r["session"] in spec["owner_sessions"]:
            first = g2p.syllables(normalize(r["prompt"], region), region)[0]
            if first.coda in lang_vi.CHECKED_CODAS and first.tone in lang_vi.CHECKED_TONES:
                tone_of[r["session"]] = first.tone
    rows = [r for r in manifest if r["session"] in tone_of]
    row_of = {r["session"]: r for r in rows}
    device_cfg = device_of(cfg)
    chain_cfg = ChainConfig(balance_gains=device.load_microphones(device_cfg["microphone"]).gains)
    mel = Mel(MelConfig(**device_cfg["features"]))
    gone_of: dict[str, set[int]] = defaultdict(set)
    for named in board.get("left_out_utterances", []):
        session, _, k = named.partition("#")
        gone_of[session].add(int(k))
    said_of = {r["session"]: gate.said_alone(r, paths, chain_cfg, mel) for r in rows}
    recorded, recorded_hops = owner_windows(cfg, rows, paths, manifest, said_of, gone_of)
    windows = {"recorded": recorded}
    pcm = {s: gate.channels_of(paths["raw"] / "device" / r["board"] / s) for s, r in row_of.items()}
    work = paths["cache"] / "tone_flip" / "owner"
    before, after = spec["owner_margin_hops"]
    asked, starts = [], {}
    for n, ((session, k), (first, last)) in enumerate(sorted(recorded_hops.items())):
        mic0 = pcm[session][:, 0].astype(np.float64) / audio_io.INT16_SCALE
        a, b = max(0, (first - before) * HOP), min(len(mic0), (last + after) * HOP)
        wav = work / "wav" / f"u{n:04d}.wav"
        audio_io.write_wav(wav, mic0[a:b] / max(np.abs(mic0[a:b]).max(), 1e-9) * ALIGN_GAIN)
        asked.append({"id": f"u{n:04d}", "wav": str(wav), "text": " ".join(corpus.words(row_of[session]["prompt"]))})
        starts[f"u{n:04d}"] = (session, k, a / FS, b / FS)
    times = engines.align(asked, load_yaml(TTS_CONFIG), work, paths["cache"])
    tracks = {s: repitch.track(p[:, 0].astype(np.float64) / audio_io.INT16_SCALE, rcfg) for s, p in pcm.items()}
    voiced = [
        tracks[s].f0_hz[(tracks[s].times_s >= a) & (tracks[s].times_s < b) & (tracks[s].f0_hz > 0)]
        for s, _, a, b in starts.values()
    ]
    median_hz = float(np.median(np.concatenate(voiced)))
    spoken, contours = [], defaultdict(list)
    for key, (session, k, a, _) in starts.items():
        if not times.get(key):
            continue
        word = times[key][0]
        stretch = repitch.voiced_stretch(tracks[session], a + word["start"], a + word["end"], rcfg)
        if stretch is not None:
            spoken.append(Spoken(session, k, tone_of[session], stretch))
            contours[tone_of[session]].append(repitch.contour_st(tracks[session], stretch, median_hz, rcfg.points))
    templates = {tone: np.median(np.stack(c), axis=0) for tone, c in contours.items()}
    target_of = {
        (s.session, s.said): repitch.contour_hz(templates[OTHER_TONE[s.tone]], median_hz)
        for s in spoken
        if OTHER_TONE[s.tone] in templates
    }
    for condition, targets in (("same", {}), ("swap", target_of)):
        moved = {
            session: moved_session(pcm[session], [s for s in spoken if s.session == session], targets, rcfg)
            for session in {s.session for s in spoken}
        }
        windows[condition] = owner_windows(cfg, rows, paths, manifest, said_of, gone_of, moved)[0]
    return OwnerHeard(windows, spoken, row_of, median_hz, templates)


def owner_report(net: gate.Ctc, heard: OwnerHeard, listed: list[dict], spec: dict) -> dict:
    """The owner's utterances decided by net: per day, distance and word, right and accepted at accept_permille and
    what was heard in each condition; per word the utterances right with F0 kept that moving it turned; and over every
    utterance, the ones creak or the aligner left out included, right and accepted as recorded and with each set of
    pitch dims of HOLDS held."""
    decided = {c: decide(net, heard.windows[c]) for c in CONDITIONS}
    holding = {name: decide(net, heard.windows["recorded"], dims) for name, dims in HOLDS.items()}
    command_of = {tuple(corpus.sounds(c["text"])): c["id"] for c in listed}
    reject, margin = spec["accept_permille"]
    groups: dict[tuple, dict] = {}
    flips: dict[str, Counter] = defaultdict(Counter)
    for s in heard.spoken:
        r = heard.row_of[s.session]
        expected = command_of[tuple(corpus.sounds(r["prompt"]))]
        said = {c: decided[c].get((s.session, s.said)) for c in CONDITIONS}
        if any(h is None for h in said.values()):
            continue
        word = r["prompt"].split()[0]
        key = (f"{s.session[6:8]}/{s.session[4:6]}", r["distance_cm"], word)
        group = groups.setdefault(key, {c: {"right": 0, "accepted": 0, "heard": Counter()} for c in CONDITIONS})
        for c, h in said.items():
            group[c]["right"] += h.command == expected
            group[c]["accepted"] += h.command == expected and h.accepted(reject, margin)
            group[c]["heard"][h.command] += 1
        if said["same"].command == expected:
            flips[word]["kept"] += 1
            flips[word]["turned"] += said["swap"].command != expected
    return {
        "median_hz": round(heard.median_hz, 1),
        "recorded": tally(decided["recorded"], heard.row_of, command_of, reject, margin),
        "held": {name: tally(holding[name], heard.row_of, command_of, reject, margin) for name in HOLDS},
        "templates_st": {tone: [round(float(v), 2) for v in c] for tone, c in sorted(heard.templates.items())},
        "groups": [
            {"day": day, "cm": cm, "word": word, "utterances": sum(g["recorded"]["heard"].values())}
            | {c: g[c] | {"heard": dict(g[c]["heard"])} for c in CONDITIONS}
            for (day, cm, word), g in groups.items()
        ],
        "flips": {word: dict(c) for word, c in flips.items()},
    }


def owner_table(report: dict) -> str:
    lines = [
        "| day | cm | word | utterances | recorded: right (accepted) | same | swap | swap heard |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for g in report["groups"]:
        cells = [f"{g[c]['right']} ({g[c]['accepted']})" for c in CONDITIONS]
        heard = ", ".join(f"{cid} {n}" for cid, n in sorted(g["swap"]["heard"].items()))
        lines.append(
            f"| {g['day']} | {g['cm']} | {g['word']} | {g['utterances']} | " + " | ".join(cells) + f" | {heard} |"
        )
    turned = "; ".join(f"{word} {c.get('turned', 0)}/{c.get('kept', 0)}" for word, c in report["flips"].items())

    def counted(rows: list[dict]) -> str:
        return "; ".join(
            f"{r['day']} {r['cm']} cm {r['word']} {r['right']}/{r['utterances']} ({r['accepted']})" for r in rows
        )

    held_lines = [f"every utterance, {name} held: {counted(rows)}" for name, rows in report["held"].items()]
    return "\n".join(
        [
            *lines,
            "",
            f"turned by moving F0, of those right with it kept: {turned}",
            f"every utterance as recorded: {counted(report['recorded'])}",
            *held_lines,
        ]
    )


def owner_line(report: dict) -> str:
    """One line of the owner check: per day and word right (accepted) as recorded and with every pitch dim held, and
    the "tắt" and "bật" right with F0 kept that the other word's contour turned."""
    every: dict[str, list[int]] = {}
    for name, rows in [("recorded", report["recorded"]), ("pitch held", report["held"]["pitch"])]:
        for r in rows:
            c = every.setdefault(f"{r['day']} {r['word']} {name}", [0, 0, 0])
            c[0], c[1], c[2] = c[0] + r["right"], c[1] + r["utterances"], c[2] + r["accepted"]
    cells = "; ".join(f"{key} {r}/{n} ({a})" for key, (r, n, a) in sorted(every.items()))
    turned = "; ".join(f"{w} {c.get('turned', 0)}/{c.get('kept', 0)}" for w, c in report["flips"].items())
    return f"{cells} | turned {turned}"


def matched(hyp: list[int], ref: np.ndarray) -> list[int | None]:
    """Per reference unit, the hypothesis unit an edit-distance alignment matches or substitutes it with; None when
    deleted."""
    n, m = len(hyp), len(ref)
    d = np.zeros((n + 1, m + 1), dtype=np.int32)
    d[:, 0], d[0, :] = np.arange(n + 1), np.arange(m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i, j] = min(d[i - 1, j] + 1, d[i, j - 1] + 1, d[i - 1, j - 1] + (hyp[i - 1] != ref[j - 1]))
    out: list[int | None] = [None] * m
    i, j = n, m
    while i > 0 and j > 0:
        if d[i, j] == d[i - 1, j - 1] + (hyp[i - 1] != ref[j - 1]):
            out[j - 1] = hyp[i - 1]
            i, j = i - 1, j - 1
        elif d[i, j] == d[i, j - 1] + 1:
            j -= 1
        else:
            i -= 1
    return out


def places_val(cfg: dict, paths: dict) -> train.Sentences:
    """val of a run's split as its training reads it."""
    version, spec = cfg["split"]["version"], cfg["train"]
    listed = {r.item for r in splits.read_split(paths["splits"] / "command" / version / "val.txt")}
    units_of = sentence_units(screen.kept_clips(load_yaml(screen.CONFIG), paths, "speech"), listed, spec["dialect"])
    longest = round(spec["max_s"] * train.HOPS_PER_S)
    return train.load_role([paths["processed"] / "command" / version / "val"], units_of, longest, "float32")


def places_check(net: gate.Ctc, val: train.Sentences) -> dict:
    """Unit error rate and tone right at a sentence's first syllable and at its later ones over val, its best path
    aligned to the units, with the pitch dims as simulated and held at their mean."""
    tones = {g2p.UNIT_ID[t] + 1 for t in lang_vi.TONES}
    out = {}
    for name in ("simulated", "pitch held at mean"):
        right, total, errors, said = Counter(), Counter(), 0, 0
        for k in range(0, len(val.first), train.VAL_BATCH):
            picks = np.arange(k, min(k + train.VAL_BATCH, len(val.first)))
            x, hops, units = train.batch_of(val, picks, net.model.chunk_multiple)
            x = (x - net.mean) / net.std
            if name != "simulated":
                x[..., -N_PITCH:] = 0.0
            with torch.no_grad():
                _, log_probs = train.encoded_of(net.model, torch.from_numpy(x.astype(np.float32)))
            for row, n in enumerate(train.frames_of(hops, net.model.front.hop_stride)):
                hyp, ref = train.best_path(log_probs.numpy()[row, :, :n]), units[row]
                errors, said = errors + train.edit_distance(hyp, ref), said + len(ref)
                seen = False
                for r, h in zip(ref, matched(hyp, ref), strict=True):
                    if int(r) in tones:
                        place = "later" if seen else "first"
                        seen = True
                        total[place] += 1
                        right[place] += h == int(r)
        out[name] = {"unit_error_rate": errors / said} | {f"tone_right_{p}": right[p] / total[p] for p in PLACES}
        out[name] |= {"syllables": dict(total)}
    return {"sentences": len(val.first)} | out


def places_line(report: dict) -> str:
    return "; ".join(
        f"{name}: unit error rate {report[name]['unit_error_rate']:.3f}, tone right first"
        f" {report[name]['tone_right_first']:.3f} later {report[name]['tone_right_later']:.3f}"
        for name in ("simulated", "pitch held at mean")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("check", choices=CHECKS)
    parser.add_argument("run", type=Path, help="a run directory of the ctc train")
    parser.add_argument(
        "--steps", help="owner, places: the run's checkpoints at these steps, comma separated, not its end"
    )
    parser.add_argument(
        "--kaldi-pitch", action="store_true", help="owner: hear through the contract's Kaldi pitch, as the board does"
    )
    args = parser.parse_args(argv)
    spec, paths = load_yaml(ctc.CONFIG)["tone_flip"], data_paths()
    if args.steps and args.check == "val":
        parser.error("--steps goes with owner and places")
    if args.kaldi_pitch and args.check != "owner":
        parser.error("--kaldi-pitch goes with owner")
    tag = "_kaldi_pitch" if args.kaldi_pitch else ""
    if args.check == "owner":
        commands = ML_ROOT.parent / spec["owner_set"]
        listed = json.loads(commands.read_text(encoding="utf-8"))["commands"]
        net = gate.load_ctc(args.run, commands)
        cfg = on_contract_pitch(net.cfg) if args.kaldi_pitch else net.cfg
        heard = owner_heard(cfg, spec, paths)
    elif args.check == "places":
        net = gate.load_ctc(args.run)
        val = places_val(net.cfg, paths)
    if args.steps:
        report = {}
        for step in (int(v) for v in args.steps.split(",")):
            weights = train.checkpoint(args.run, step)
            if args.check == "owner":
                report[step] = owner_report(gate.load_ctc(args.run, commands, weights), heard, listed, spec)
                print(f"step {step}: " + owner_line(report[step]), flush=True)
            else:
                report[step] = places_check(gate.load_ctc(args.run, weights=weights), val)
                print(f"step {step}: " + places_line(report[step]), flush=True)
        out = args.run / f"tone_flip_{args.check}_steps{tag}.yaml"
        out.write_text(yaml.safe_dump(report, allow_unicode=True, sort_keys=False), encoding="utf-8")
        print(out)
        return 0
    if args.check == "val":
        report = flip_check(args.run, paths, spec)
        print(f"counts: {report['counts']}")
        print(table(report["summary"]))
        print(f"KEHOACH 3.11: {report['branch']}")
    elif args.check == "owner":
        report = owner_report(net, heard, listed, spec)
        print(owner_table(report))
    else:
        report = {"run": str(args.run), "split": net.cfg["split"]["version"]} | places_check(net, val)
        print(places_line(report))
    out = args.run / f"tone_flip_{args.check}{tag}.yaml"
    out.write_text(yaml.safe_dump(report, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
