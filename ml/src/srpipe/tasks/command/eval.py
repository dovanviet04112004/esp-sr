"""Gate 3 of the command tracks (KEHOACH 3.12) on the sessions recorded through board B: the sessions of each sitting
through the product's chain run on without a reset, each utterance its vad finds scored once on the command window
svc_listen cuts for it (KEHOACH 5.4).
A session saying a command the net knows counts the utterances decided as that command; any other counts those
rejected.
Run: python -m srpipe.tasks.command.eval {kws,ctc} <run under ml/>
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import numpy as np
import soundfile as sf
import torch
import yaml

from srpipe.core import corpus
from srpipe.core.audio_io import to_float
from srpipe.core.config import data_paths, device_of, load_run_config, load_yaml
from srpipe.dsp.afe.chain import ChainConfig
from srpipe.dsp.spec.mel import Mel, MelConfig
from srpipe.dsp.spec.pitch import PitchConfig, PitchTracker
from srpipe.dsp.spec.stft import Stft
from srpipe.generated import array, grid, listen
from srpipe.scenes import device
from srpipe.tasks import command
from srpipe.tasks.command import ctc, kws
from srpipe.tasks.command.ctc.model import encoder
from srpipe.tasks.command.ctc.postproc import ctc_score
from srpipe.tasks.command.kws.model import dscnn
from srpipe.tasks.command.kws.postproc import decide
from srpipe.tasks.command.rnnt.postproc import rnnt_search

REJECT = "reject"
HOPS_PER_S = grid.SAMPLE_RATE_HZ / grid.HOP_SAMPLES
SITTING_KEYS = ("board", "fw", "pcm_shift")  # a manifest row's fields one run of the board shares
Spans = list[tuple[int, int]]


@dataclass(frozen=True)
class Scored:
    """One session: what each of its utterances should get, and per utterance the decision with its score."""

    session: str
    kind: str
    distance_cm: str
    prompt: str
    expected: str  # a command id, or REJECT
    decided: list[tuple]  # per utterance: a command id or REJECT, then ‰

    @property
    def right(self) -> int:
        return sum(d[0] == self.expected for d in self.decided)


class Heard(NamedTuple):
    """One utterance of the ctc track: the command scoring best, unless no command fits the window, with the figures
    of KEHOACH 3.12 in permille: its score, its lead over the second and the free loop's gap over it; whole is false
    when a part of the command scores no lower than it, which rejects it at any threshold."""

    command: str
    score: int
    lead: int
    gap: int
    whole: bool = True

    def accepted(self, reject: int, margin: int) -> bool:
        return self.command != REJECT and self.whole and self.gap <= reject and self.lead >= margin


@dataclass(frozen=True)
class Ctc:
    """A ctc run as the board runs it: the net, its feature statistics, every listed command and its variants, the
    run's config."""

    model: encoder.CtcNet
    mean: np.ndarray
    std: np.ndarray
    names: list[str]
    lexicon: list[list[np.ndarray]]
    cfg: dict


def load_ctc(run: Path, commands: Path = command.COMMANDS) -> Ctc:
    """A ctc run with every command of a command set file, default_vi.json unless another is given."""
    trained = load_run_config(run)
    stats = np.load(run / "feature_stats.npz")
    model = encoder.build(trained)
    model.load_state_dict(torch.load(run / "model.pt", map_location="cpu"))
    model.eval()
    listed = json.loads(commands.read_text(encoding="utf-8"))["commands"]
    lexicon = [ctc_score.variants(c["text"]) for c in listed]
    return Ctc(model, stats["mean"], stats["std"], [c["id"] for c in listed], lexicon, trained)


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
    trained = load_run_config(run)
    names = kws.classes(trained, load_yaml(command.CONFIG))
    stats = np.load(run / "feature_stats.npz")
    model = dscnn.build(trained, (trained["window_hops"], len(stats["mean"])), len(names))
    model.load_state_dict(torch.load(run / "model.pt", map_location="cpu"))
    model.eval()
    val = yaml.safe_load((run / "metrics.yaml").read_text(encoding="utf-8"))["val"]
    return Kws(model, stats["mean"], stats["std"], names, trained, (val["reject_permille"], val["margin_permille"]))


def counted(row: dict, spec: dict) -> bool:
    """Whether a session of the board manifest is of the product's pcm_shift, of a kind scored, not left out and not
    one the split gives to train (KEHOACH 1.3)."""
    kept = row["pcm_shift"] == str(spec["pcm_shift"]) and row["kind"] in spec["kinds"]
    return kept and row["session"] not in spec["left_out"] and row["session"] not in spec.get("train", [])


def expected_of(kind: str, prompt: str, command_of: dict[tuple, str]) -> str:
    """A command session's command when the net learned it, by how its prompt sounds; REJECT for any other."""
    return command_of.get(tuple(corpus.sounds(prompt)), REJECT) if kind == "cmd" else REJECT


def sittings(manifest: list[dict]) -> list[list[dict]]:
    """Manifest rows in their order, cut where the board, its firmware or its pcm_shift changes: the sessions one run of
    the board heard one after another."""
    out: list[list[dict]] = []
    for r in manifest:
        if out and all(out[-1][-1][k] == r[k] for k in SITTING_KEYS):
            out[-1].append(r)
        else:
            out.append([r])
    return out


def channels_of(folder: Path) -> np.ndarray:
    """A session's ch0 and ch1 side by side, cut to whole hops: (samples, mics) int16."""
    channels = [sf.read(folder / f"ch{m}.wav", dtype="int16")[0] for m in range(array.N_MICS)]
    n = min(len(c) for c in channels) // grid.HOP_SAMPLES * grid.HOP_SAMPLES
    return np.stack([c[:n] for c in channels], axis=1)


def with_silence(
    clean: np.ndarray, vad: np.ndarray, features: np.ndarray, mel: Mel
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A session followed by listen's gap and a hop of digital silence, enough to close an utterance running to its
    end: no vad, and log-mel from the STFT going on from the session's last hop, as svc_listen computes it."""
    hop, tail = grid.HOP_SAMPLES, listen.UTTERANCE_GAP_HOPS + 1
    stft = Stft()
    stft.analyze(to_float(clean[-hop:]))
    silence = np.zeros(hop, dtype=clean.dtype)
    after = np.stack([mel.log(stft.analyze(to_float(silence))) for _ in range(tail)]).astype(features.dtype)
    return (
        np.concatenate([clean, np.zeros(tail * hop, dtype=clean.dtype)]),
        np.concatenate([vad, np.zeros(tail, dtype=bool)]),
        np.concatenate([features, after]),
    )


def windows(
    features: np.ndarray, pitch: np.ndarray, spans: list[tuple[int, int]], window: int, lead: int
) -> np.ndarray:
    """Per utterance the window ending where vad turns off after it, from lead hops before it: log-mel, then the
    session's pitch; a window reaching before the session repeats its first hop."""
    out = []
    for first, last in spans:
        end = min(last + 1, len(features) - 1)
        start = max(0, first - lead)
        x = np.concatenate([features[start : end + 1], pitch[start : end + 1]], axis=1)[-window:]
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


def ctc_windows(features: np.ndarray, pitch: np.ndarray, spans: Spans) -> list[np.ndarray]:
    """Each utterance's command window: its log-mel, then the session's pitch over it."""
    return [
        np.concatenate([features[start : end + 1], pitch[start : end + 1]], axis=1).astype(np.float32)
        for start, end in device.command_cut(spans)
    ]


def normalised_window(net: Ctc, x: np.ndarray) -> tuple[torch.Tensor, int]:
    """A window as the device runs it, zero-padded to the net's chunk as in training and normalised: (1, dims, hops);
    and the frames of its own hops."""
    multiple, hops = net.model.chunk_multiple, len(x)
    padded = np.zeros((-(-hops // multiple) * multiple, x.shape[1]), dtype=np.float32)
    padded[:hops] = x
    return torch.from_numpy((padded - net.mean) / net.std).T[None], -(-hops // net.model.front.hop_stride)


def heard_of(net: Ctc, decision: np.ndarray, scores: np.ndarray) -> Heard:
    """A decision taken with no threshold as Heard: the best command, unless none fits, and whether a part of it
    turned it down, the only rejection left at such thresholds."""
    k, score, lead, gap = (int(v) for v in decision)
    best = int(np.argmax(scores))
    if scores[best] == -np.inf:
        return Heard(REJECT, score, lead, gap)
    return Heard(net.names[best], score, lead, gap, whole=k != ctc_score.REJECTED)


def ctc_heard(net: Ctc, x: np.ndarray) -> Heard:
    """One window decided by the ctc track as the device decides it, with no threshold; net.model is the float net or
    anything called as it is, such as the ladder's int8 simulation."""
    window, frames = normalised_window(net, x)
    with torch.no_grad():
        log_probs = net.model(window).log_softmax(1)[0, :, :frames].numpy()
    per_frames = ctc_score.window_frames(net.model.front.hop_stride)
    return heard_of(net, *ctc_score.decide(log_probs, net.lexicon, ctc_score.CAP, 0, per_frames))


def rnnt_log_probs(net: Ctc, encoded: torch.Tensor) -> rnnt_search.LogProbs:
    """The float joiner's log-probabilities over a window's frames, each predictor context projected once."""
    t = net.model.transducer
    with torch.no_grad():
        frames = t.joiner.frame_proj(encoded[0].T)
    projected: dict[tuple[int, ...], torch.Tensor] = {}

    def log_probs(frame: int, contexts: list[tuple[int, ...]]) -> np.ndarray:
        with torch.no_grad():
            for context in contexts:
                if context not in projected:
                    projected[context] = t.joiner.prefix_proj(t.predictor(torch.tensor([context]))[0, -1])
            prefixes = torch.stack([projected[c] for c in contexts])
            return t.joiner(frames[frame][None], prefixes).log_softmax(-1).numpy().astype(np.float32)

    return log_probs


def rnnt_heard(net: Ctc, x: np.ndarray) -> Heard:
    """One window decided by the rnnt track (ADR-0016) with no threshold, the context of the run's config."""
    if net.model.transducer is None:
        raise ValueError("the run learnt no transducer: its config has no rnnt section")
    window, frames = normalised_window(net, x)
    with torch.no_grad():
        encoded = net.model.encode(window)
    tree, size = rnnt_search.command_tree(net.lexicon), net.cfg["rnnt"]["context"]
    log_probs, pad = rnnt_log_probs(net, encoded), net.model.transducer.predictor.pad
    per_frames = ctc_score.window_frames(net.model.front.hop_stride)
    return heard_of(net, *rnnt_search.decide(log_probs, frames, tree, ctc_score.CAP, 0, size, pad, per_frames))


def heard_rows(cfg: dict, rows: list[dict], paths: dict, manifest: list[dict] | None = None) -> Iterator[tuple]:
    """Each row in manifest order: the row, its clean samples, vad, log-mel and pitch per hop through the product's
    chain, which runs on without a reset over every session of the row's sitting in manifest, the board manifest
    unless given, as the board's chain and svc_listen's pitch run on between utterances (KEHOACH 3.11, 5.4); each
    session followed by silence as with_silence closes it. A row outside manifest, or a sitting with a recording not
    on disk, is refused."""
    device_cfg = device_of(cfg)
    mics = device.load_microphones(device_cfg["microphone"])
    chain_cfg, mel = ChainConfig(balance_gains=mics.gains), Mel(MelConfig(**device_cfg["features"]))
    tracker = PitchTracker(PitchConfig(**device_cfg["pitch"]))
    if manifest is None:
        spec = load_yaml(command.CONFIG)["eval"]["board"]
        manifest = list(csv.DictReader((paths["manifests"] / spec["manifest"]).open(encoding="utf-8")))
    asked = {r["session"]: r for r in rows}
    if absent := sorted(asked.keys() - {r["session"] for r in manifest}):
        raise ValueError(f"{', '.join(absent)}: not sessions of the board manifest")
    for sitting in sittings(manifest):
        if not asked.keys() & {r["session"] for r in sitting}:
            continue
        folders = [paths["raw"] / "device" / r["board"] / r["session"] for r in sitting]
        if missing := [f.name for f in folders if not f.is_dir()]:
            raise FileNotFoundError(f"{', '.join(missing)}: in the board manifest, not under {paths['raw'] / 'device'}")
        pcm = [channels_of(f) for f in folders]
        clean, figures, features = device.listen(np.concatenate(pcm), chain_cfg, mel)
        cuts = np.cumsum([len(p) // grid.HOP_SAMPLES for p in pcm])[:-1]
        samples, vads = np.split(clean, cuts * grid.HOP_SAMPLES), np.split(figures[:, 0].astype(bool), cuts)
        parts = zip(samples, vads, np.split(features, cuts), strict=True)
        sessions = [with_silence(c, v, f, mel) for c, v, f in parts]
        pitch = device.stream_pitch(tracker, np.concatenate([s[0] for s in sessions]))
        pitches = np.split(pitch, np.cumsum([len(s[1]) for s in sessions])[:-1])
        for r, (c, v, f), p in zip(sitting, sessions, pitches, strict=True):
            if r["session"] in asked:
                yield asked[r["session"]], c, v, f, p


def heard_sessions(cfg: dict, spec: dict, paths: dict) -> Iterator[tuple]:
    """heard_rows over every counted session of the board manifest."""
    rows = list(csv.DictReader((paths["manifests"] / spec["manifest"]).open(encoding="utf-8")))
    yield from heard_rows(cfg, [r for r in rows if counted(r, spec)], paths, rows)


def board(cfg: dict, spec: dict, paths: dict, said: dict[str, str], decided_of: Callable) -> list[Scored]:
    """Every counted session of the board manifest found on disk, its utterances decided by decided_of(clean,
    features, pitch, spans); a session saying the text of a command of said expects that command."""
    command_of = {tuple(corpus.sounds(text)): cid for cid, text in said.items()}
    results = []
    for r, clean, vad, features, pitch in heard_sessions(cfg, spec, paths):
        spans = device.utterances(vad)
        decided = decided_of(clean, features, pitch, spans) if spans else []
        expected = expected_of(r["kind"], r["prompt"], command_of)
        results.append(Scored(r["session"], r["kind"], r["distance_cm"], r["prompt"], expected, decided))
    return results


def kws_board(net: Kws, spec: dict, paths: dict) -> list[Scored]:
    lead = round(net.cfg["simulate"]["pads_s"][0] * HOPS_PER_S)
    if pilot := net.cfg["speech_commands"]:
        said = {word: word for word in pilot["keywords"]}
    else:
        said = {c["id"]: c["text"] for c in command.learned(load_yaml(command.CONFIG))}

    def decided_of(clean, features, pitch, spans):
        return decisions(net, windows(features, pitch, spans, net.cfg["window_hops"], lead))

    return board(net.cfg, spec, paths, {cid: text for cid, text in said.items() if cid in net.names}, decided_of)


def ctc_board(
    net: Ctc, spec: dict, paths: dict, heard: Callable = ctc_heard, commands: Path = command.COMMANDS
) -> list[Scored]:
    """The board sessions decided over their command windows by heard, the ctc track's or the rnnt track's, a session
    expecting the command of the set file commands whose text it says."""
    listed = json.loads(commands.read_text(encoding="utf-8"))["commands"]

    def decided_of(clean, features, pitch, spans):
        return [heard(net, x) for x in ctc_windows(features, pitch, spans)]

    return board(net.cfg, spec, paths, {c["id"]: c["text"] for c in listed}, decided_of)


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


def ctc_table(results: list[Scored], names: list[str], spec: dict, sweep: dict) -> str:
    """Each session's best command per utterance with its figures, each command's utterances its best command gets
    right, then per reject threshold of the sweep at its margin the share of each command's utterances accepted as it
    and of every other utterance rejected, by kind."""
    lines = [
        "| Session | Kind | cm | Prompt | Expected | Best right | Best: score/lead/gap ‰ |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        said = " · ".join(f"{h.command} {h.score}/{h.lead}/{h.gap}" for h in r.decided) or "-"
        prompt = r.prompt[:40].replace("|", "\\|")
        right = f"{r.right}/{len(r.decided)}"
        lines.append(f"| {r.session} | {r.kind} | {r.distance_cm} | {prompt} | {r.expected} | {right} | {said} |")
    lines.append("")
    for cid in names:
        right, total = share([r for r in results if r.expected == cid])
        lines.append(f"- {cid}: best {right}/{total}")
    others = [r for r in results if r.expected == REJECT]
    kinds = list(dict.fromkeys(r.kind for r in others))
    margin, recall, rejection = sweep["margin"], spec["command_recall"], spec["rejection"]
    lines += [
        "",
        f"| δ₁ ‰, δ₂ {margin} ‰ | worst command (gate {recall:.0%}) | commands | rejected (gate {rejection:.0%}) | "
        + " | ".join(kinds)
        + " |",
        "|---" * (4 + len(kinds)) + "|",
    ]
    for reject in sweep["reject_sweep"]:
        accepted = []
        for cid in names:
            heard = [h for r in results if r.expected == cid for h in r.decided]
            if heard:
                accepted.append(sum(h.command == cid and h.accepted(reject, margin) for h in heard) / len(heard))
        cells = []
        for group in [others] + [[r for r in others if r.kind == kind] for kind in kinds]:
            heard = [h for r in group for h in r.decided]
            cells.append(f"{sum(not h.accepted(reject, margin) for h in heard)}/{len(heard)}")
        worst, mean = (min(accepted), sum(accepted) / len(accepted)) if accepted else (0.0, 0.0)
        lines.append(f"| {reject} | {worst:.0%} | {mean:.0%} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("track", choices=["kws", "ctc", "rnnt"])
    parser.add_argument("run", type=Path, help="a run directory of the track's train")
    parser.add_argument(
        "--commands",
        type=Path,
        default=command.COMMANDS,
        help="ctc, rnnt: a command set file to score in place of "
        "default_vi.json, as the board runs the set the host gives it; the gate of KEHOACH 3.12 is the default's",
    )
    args = parser.parse_args(argv)
    spec = load_yaml(command.CONFIG)["eval"]
    if args.track == "kws":
        net = load_kws(args.run)
        print(f"{args.run}: reject {net.thresholds[0]}‰, margin {net.thresholds[1]}‰")
        print(table(kws_board(net, spec["board"], data_paths()), net.names, spec))
        return 0
    ctc_cfg = load_yaml(ctc.CONFIG)
    net = load_ctc(args.run, args.commands)
    forms = sum(len(v) for v in net.lexicon)
    said = f"{len(net.names)} commands of {args.commands.name}, {forms} variants"
    print(f"{args.run}: {said}, windows up to {listen.WINDOW_S} s")
    heard = rnnt_heard if args.track == "rnnt" else ctc_heard
    results = ctc_board(net, spec["board"], data_paths(), heard, args.commands)
    print(ctc_table(results, net.names, spec, ctc_cfg["eval"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
