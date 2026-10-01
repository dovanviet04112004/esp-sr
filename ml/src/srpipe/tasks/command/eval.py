"""Gate 3 of the command tracks (KEHOACH 3.12) on the sessions recorded through board B: each session through the
product's chain, each utterance its vad finds scored once where vad turns off after it, as LENH scores it. A session
saying a command the net learned counts the utterances decided as that command; any other counts those rejected.
Run: python -m srpipe.tasks.command.eval kws <run under ml/>
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import yaml

from srpipe.core import corpus
from srpipe.core.config import CONFIGS, data_paths, load_yaml
from srpipe.dsp.afe.chain import ChainConfig
from srpipe.dsp.spec.mel import Mel, MelConfig
from srpipe.dsp.spec.pitch import PitchConfig, PitchTracker
from srpipe.generated import array, grid
from srpipe.scenes import device
from srpipe.tasks import command
from srpipe.tasks.command import kws
from srpipe.tasks.command.kws.model import dscnn
from srpipe.tasks.command.kws.postproc import decide
from srpipe.tasks.wake.eval import utterances

REJECT = "reject"
HOPS_PER_S = grid.SAMPLE_RATE_HZ / grid.HOP_SAMPLES


@dataclass(frozen=True)
class Scored:
    """One session: what each of its utterances should get, and per utterance the decision with its score."""

    session: str
    kind: str
    distance_cm: str
    prompt: str
    expected: str  # a command id, or REJECT
    decided: list[tuple[str, int]]  # a command id or REJECT, and its score in permille

    @property
    def right(self) -> int:
        return sum(d == self.expected for d, _ in self.decided)


@dataclass(frozen=True)
class Kws:
    """A kws run as the board runs it: the net, its feature statistics, its classes and config, its val thresholds."""

    model: dscnn.DsCnn
    mean: np.ndarray
    std: np.ndarray
    names: list[str]
    cfg: dict
    thresholds: tuple[int, int]  # reject_permille, margin_permille


def load_kws(run: Path) -> Kws:
    trained = load_yaml(run / "config.resolved.yaml")
    names = kws.classes(load_yaml(command.CONFIG), trained["commands"])
    stats = np.load(run / "feature_stats.npz")
    model = dscnn.build(trained, (trained["window_hops"], len(stats["mean"])), len(names))
    model.load_state_dict(torch.load(run / "model.pt", map_location="cpu"))
    model.eval()
    val = yaml.safe_load((run / "metrics.yaml").read_text(encoding="utf-8"))["val"]
    return Kws(model, stats["mean"], stats["std"], names, trained, (val["reject_permille"], val["margin_permille"]))


def counted(row: dict, spec: dict) -> bool:
    """Whether a session of the board manifest is of the product's pcm_shift, of a kind scored, and not left out."""
    kept = row["pcm_shift"] == str(spec["pcm_shift"]) and row["kind"] in spec["kinds"]
    return kept and row["session"] not in spec["left_out"]


def expected_of(kind: str, prompt: str, command_of: dict[tuple, str]) -> str:
    """A command session's command when the net learned it, by how its prompt sounds; REJECT for any other."""
    return command_of.get(tuple(corpus.sounds(prompt)), REJECT) if kind == "cmd" else REJECT


def heard(folder: Path, chain_cfg: ChainConfig, mel: Mel) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Clean samples, vad and log-mel per hop of a session's ch0 and ch1 through the product's chain."""
    channels = [sf.read(folder / f"ch{m}.wav", dtype="int16")[0] for m in range(array.N_MICS)]
    n = min(len(c) for c in channels) // grid.HOP_SAMPLES * grid.HOP_SAMPLES
    clean, figures, features = device.listen(np.stack([c[:n] for c in channels], axis=1), chain_cfg, mel)
    return clean, figures[:, 0].astype(bool), features


def windows(
    clean: np.ndarray, features: np.ndarray, spans: list[tuple[int, int]], window: int, lead: int, tracker: PitchTracker
) -> np.ndarray:
    """Per utterance the window ending where vad turns off after it: log-mel, then pitch from a tracker reset lead hops
    before the utterance as the simulation resets it for an item; a window reaching before the session repeats its
    first hop."""
    out = []
    for first, last in spans:
        end = min(last + 1, len(features) - 1)
        start = max(0, first - lead)
        pitch = device.item_pitch(tracker, clean[start * grid.HOP_SAMPLES : (end + 1) * grid.HOP_SAMPLES])
        x = np.concatenate([features[start : end + 1], pitch], axis=1)[-window:]
        out.append(np.pad(x, ((window - len(x), 0), (0, 0)), mode="edge"))
    return np.stack(out).astype(np.float32)


def decisions(net: Kws, x: np.ndarray) -> list[tuple[str, int]]:
    """Each window decided as the device decides it, at the run's val thresholds."""
    with torch.no_grad():
        logits = net.model(torch.from_numpy((x - net.mean) / net.std)[:, None]).numpy()
    n_commands = net.names.index(kws.OTHER)
    out = []
    for row in logits:
        k, score = (int(v) for v in decide.decide(row, n_commands, *net.thresholds)[:2])
        out.append((REJECT if k == decide.REJECTED else net.names[k], score))
    return out


def board(net: Kws, spec: dict, paths: dict) -> list[Scored]:
    """Every counted session of the board manifest found on disk, scored."""
    device_cfg = load_yaml(CONFIGS / net.cfg["features"])
    mics = device.load_microphones(device_cfg["microphone"])
    chain_cfg, mel = ChainConfig(balance_gains=mics.gains), Mel(MelConfig(**device_cfg["features"]))
    tracker = PitchTracker(PitchConfig(**device_cfg["pitch"]))
    lead = round(net.cfg["simulate"]["pads_s"][0] * HOPS_PER_S)
    learned = command.learned(load_yaml(command.CONFIG))
    command_of = {tuple(corpus.sounds(c["text"])): c["id"] for c in learned if c["id"] in net.names}
    results = []
    for r in csv.DictReader((paths["manifests"] / spec["manifest"]).open(encoding="utf-8")):
        folder = paths["raw"] / "device" / r["board"] / r["session"]
        if not counted(r, spec) or not folder.exists():
            continue
        clean, vad, features = heard(folder, chain_cfg, mel)
        decided = []
        if spans := utterances(vad, spec):
            decided = decisions(net, windows(clean, features, spans, net.cfg["window_hops"], lead, tracker))
        expected = expected_of(r["kind"], r["prompt"], command_of)
        results.append(Scored(r["session"], r["kind"], r["distance_cm"], r["prompt"], expected, decided))
    return results


def share(results: list[Scored]) -> tuple[int, int]:
    return sum(r.right for r in results), sum(len(r.decided) for r in results)


def table(results: list[Scored], names: list[str], spec: dict) -> str:
    """Each session's decisions, then each learned command's utterances right and the rest's rejected, by the gate."""
    lines = ["| Session | Kind | cm | Prompt | Expected | Right | Decisions (‰) |", "|---|---|---|---|---|---|---|"]
    for r in results:
        said = " · ".join(f"{d} {s}" for d, s in r.decided) or "-"
        prompt = r.prompt[:40].replace("|", "\\|")
        right = f"{r.right}/{len(r.decided)}"
        lines.append(f"| {r.session} | {r.kind} | {r.distance_cm} | {prompt} | {r.expected} | {right} | {said} |")
    lines.append("")
    for cid in names[: names.index(kws.OTHER)]:
        right, total = share([r for r in results if r.expected == cid])
        verdict = "pass" if total and right / total >= spec["command_recall"] else "fail"
        lines.append(f"- {cid}: {right}/{total} right ({verdict} at {spec['command_recall']:.0%})")
    rejected = [r for r in results if r.expected == REJECT]
    right, total = share(rejected)
    verdict = "pass" if total and right / total >= spec["rejection"] else "fail"
    lines.append(f"- rejected: {right}/{total} ({verdict} at {spec['rejection']:.0%})")
    for kind in dict.fromkeys(r.kind for r in rejected):
        right, total = share([r for r in rejected if r.kind == kind])
        lines.append(f"  - {kind}: {right}/{total}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("track", choices=["kws"])
    parser.add_argument("run", type=Path, help="a run directory of python -m srpipe.tasks.command.kws.train")
    args = parser.parse_args(argv)
    spec = load_yaml(command.CONFIG)["eval"]
    net = load_kws(args.run)
    print(f"{args.run}: reject {net.thresholds[0]}‰, margin {net.thresholds[1]}‰")
    print(table(board(net, spec["board"], data_paths()), net.names, spec))
    return 0


if __name__ == "__main__":
    sys.exit(main())
