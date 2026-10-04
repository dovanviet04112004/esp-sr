"""Train the ctc net of command (E11-T12, KEHOACH 3.12) on board-simulated log-mel and pitch, write a run directory.

An example is a sentence of processed/command/<version> as the device computes it, drawn from a ring of train's shards,
its target the lang_vi units of its text in the configured dialect. CTC, SpecAugment, Adam with a cosine decay, and a
cost on a layer's stream above train.stream's cap; val gives the CTC loss, the best path's unit error rate and the
loudest frame of any stream. Ctrl-C pauses after the step under way. Run: python -m srpipe.tasks.command.ctc.train"""

from __future__ import annotations

import argparse
import json
import math
import signal
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import yaml
from torch.nn import functional

from srpipe.core import screen, splits
from srpipe.core.config import apply_overrides, contract_front, data_paths, load_run_config, load_yaml
from srpipe.core.logger import row_line
from srpipe.core.run_dir import create_run_dir
from srpipe.core.seed import seed_everything
from srpipe.dsp.spec import pitch
from srpipe.generated import grid
from srpipe.tasks.command import ctc
from srpipe.tasks.command.ctc import data as built
from srpipe.tasks.command.ctc.model import encoder
from srpipe.tasks.command.ctc.postproc.ctc_score import BLANK
from srpipe.tasks.command.rnnt.model import transducer
from srpipe.tasks.wake.data import sentence_units

BRANCH = "command_ctc"
HOPS_PER_S = grid.SAMPLE_RATE_HZ / grid.HOP_SAMPLES
VAL_BATCH = 64
DELTA_PITCH = 2  # pitch's third feature: delta log pitch a hop
LOG_PER_DB = math.log(10.0) / 10.0  # log-mel is a natural log of power
POOL_STREAM = 1  # pass p's shard order: [train.seed, POOL_STREAM, p]
POOL_DTYPE = np.float16
GB = 1e9
MEAN_SQUARE_FLOOR = 1e-12  # log2 of a silent frame's stream stays finite


class Units:
    """Sentences' unit ids end to end, sentence k the slice from starts[k] to starts[k + 1]: a list of arrays without
    an array object a sentence, read as int64 like one."""

    def __init__(self, flat: np.ndarray, starts: np.ndarray) -> None:
        self.flat, self.starts = flat, starts

    def __len__(self) -> int:
        return len(self.starts) - 1

    def __getitem__(self, k: int) -> np.ndarray:
        return self.flat[self.starts[k] : self.starts[k + 1]].astype(np.int64)


@dataclass
class Sentences:
    """Sentences of one role end to end: features (hops, dims), and per sentence its first hop, hops and units."""

    features: np.ndarray
    first: np.ndarray
    hops: np.ndarray
    units: list[np.ndarray] | Units  # lang_vi unit ids, shifted past the CTC blank


def load_role(folders: list[Path], units_of: dict[str, list[int]], longest: int, dtype: str) -> Sentences:
    """Every sentence of the finished builds in folders that has lang_vi units and at most longest hops."""
    features, first, hops, units, offset = [], [], [], [], 0
    for folder in folders:
        built = yaml.safe_load((folder / "manifest.yaml").read_text(encoding="utf-8"))
        if not built.get("pitch"):
            raise ValueError(f"{folder} was simulated without pitch")
        for name in sorted(n for n in built["sha256"] if n.endswith(".items.jsonl")):
            stem = str(folder / name).removesuffix(".items.jsonl")
            mel = np.load(stem + ".features.npy")
            features.append(np.concatenate([mel, np.load(stem + ".pitch.npy")], axis=1).astype(dtype))
            for line in (folder / name).read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                said = units_of.get(item["item"].split("@")[0])
                if said and item["n_frames"] <= longest:
                    first.append(offset + item["frame_offset"])
                    hops.append(item["n_frames"])
                    units.append(np.asarray(said, dtype=np.int64) + 1)
            offset += len(mel)
    if not first:
        raise ValueError(f"no sentence of {[f.name for f in folders]} has units within {longest} hops")
    return Sentences(np.concatenate(features), np.array(first), np.array(hops), units)


def feature_sums(features: np.ndarray, block: int = 1 << 20) -> tuple[np.ndarray, np.ndarray]:
    """The sum and the sum of squares of every dim over every hop, in float64."""
    total, square = np.zeros(features.shape[1]), np.zeros(features.shape[1])
    for k in range(0, len(features), block):
        x = features[k : k + block].astype(np.float64)
        total, square = total + x.sum(axis=0), square + (x * x).sum(axis=0)
    return total, square


def stats_of(total: np.ndarray, square: np.ndarray, hops: int) -> tuple[np.ndarray, np.ndarray]:
    """Mean and deviation of every dim from feature_sums over hops hops."""
    mean = total / hops
    return mean.astype(np.float32), np.sqrt(square / hops - mean * mean).astype(np.float32)


def feature_stats(features: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Mean and deviation of every dim over every hop, accumulated in float64."""
    return stats_of(*feature_sums(features), len(features))


@dataclass(frozen=True)
class Shard:
    """A finished shard of a train build: its files' stem, its hops, and the sentences it gives, each one's first hop
    within it, hops, and units past the blank as uint8 end to end from unit_starts."""

    stem: str
    n_hops: int
    first: np.ndarray
    hops: np.ndarray
    units: np.ndarray
    unit_starts: np.ndarray

    def features(self) -> np.ndarray:
        """Its log-mel and pitch side by side (hops, dims), as stored."""
        mel, pitch_ = (np.load(self.stem + s) for s in (".features.npy", ".pitch.npy"))
        if len(mel) != self.n_hops or len(pitch_) != self.n_hops:
            raise ValueError(f"{self.stem}: {len(mel)} and {len(pitch_)} hops, its items list {self.n_hops}")
        return np.concatenate([mel, pitch_], axis=1)


def shards_of(folders: list[Path], units_of: dict[str, list[int]], longest: int) -> list[Shard]:
    """Every shard of the finished builds in folders, with its sentences that have lang_vi units and at most longest
    hops; read from the item listings alone."""
    out = []
    for folder in folders:
        built = yaml.safe_load((folder / "manifest.yaml").read_text(encoding="utf-8"))
        if not built.get("pitch"):
            raise ValueError(f"{folder} was simulated without pitch")
        for name in sorted(n for n in built["sha256"] if n.endswith(".items.jsonl")):
            first, hops, units, n_hops = [], [], [], 0
            for line in (folder / name).read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                n_hops = max(n_hops, item["frame_offset"] + item["n_frames"])
                said = units_of.get(item["item"].split("@")[0])
                if said and item["n_frames"] <= longest:
                    first.append(item["frame_offset"])
                    hops.append(item["n_frames"])
                    units.append(np.asarray(said, dtype=np.uint8) + 1)
            starts = np.cumsum([0] + [len(u) for u in units])
            flat = np.concatenate(units) if units else np.zeros(0, dtype=np.uint8)
            stem = str(folder / name).removesuffix(".items.jsonl")
            out.append(Shard(stem, n_hops, np.array(first, dtype=np.int64), np.array(hops), flat, starts))
    if not any(len(s.first) for s in out):
        raise ValueError(f"no sentence of {[f.name for f in folders]} has units within {longest} hops")
    return out


class Pool:
    """Train's sentences from a ring of its shards (KEHOACH 3.12). The ring holds capacity hops: it fills with the
    shards of pass 0's order while they fit, then before every rotate_steps-th step the next shard of the order not in
    the ring comes in, at the end or back at the start, over the shards it overlaps, the oldest. A pass is a seeded
    order of every shard. The ring of a step is a function of the step alone, which a resumed run rebuilds; train
    within capacity is held whole."""

    def __init__(self, shards: list[Shard], dims: int, capacity: int, rotate_steps: int, seed: int) -> None:
        if wide := [s.stem for s in shards if s.n_hops > capacity]:
            raise ValueError(f"{wide[0]}: a shard longer than the ring's {capacity} hops")
        self.shards, self.dims, self.rotate_steps, self.seed = shards, dims, rotate_steps, seed
        total = sum(s.n_hops for s in shards)
        self.whole = total <= capacity
        self.capacity = total if self.whole else capacity
        self.ring = np.empty((self.capacity, dims), dtype=POOL_DTYPE)
        self.placed: list[tuple[int, int]] = []  # (shard, first hop in the ring), oldest first
        self.held: set[tuple[int, int]] = set()  # what the ring's memory holds
        self.rotations, self.write, self.passes, self.at_order = -1, 0, -1, 0
        self.order = np.zeros(0, dtype=np.int64)
        self.view: Sentences | None = None

    @property
    def sentences(self) -> int:
        return sum(len(s.first) for s in self.shards)

    @property
    def hours(self) -> float:
        return sum(int(s.hops.sum()) for s in self.shards) / HOPS_PER_S / splits.SECONDS_PER_HOUR

    def stats(self) -> tuple[np.ndarray, np.ndarray]:
        """Mean and deviation of every dim over every hop of every shard, read one shard at a time."""
        total, square = np.zeros(self.dims), np.zeros(self.dims)
        for shard in self.shards:
            more, more_square = feature_sums(shard.features())
            total, square = total + more, square + more_square
        return stats_of(total, square, sum(s.n_hops for s in self.shards))

    def _upcoming(self) -> int:
        """The next shard of the order not in the ring, the order going on into the next pass."""
        inside = {shard for shard, _ in self.placed}
        while True:
            if self.at_order == len(self.order):
                self.passes += 1
                self.order = np.random.default_rng([self.seed, POOL_STREAM, self.passes]).permutation(len(self.shards))
                self.at_order = 0
            shard = int(self.order[self.at_order])
            if shard not in inside:
                return shard
            self.at_order += 1

    def _place(self, shard: int) -> None:
        n = self.shards[shard].n_hops
        if self.write + n > self.capacity:
            self.write = 0
        end = self.write + n
        self.placed = [(s, a) for s, a in self.placed if a + self.shards[s].n_hops <= self.write or a >= end]
        self.placed.append((shard, self.write))
        self.write, self.at_order = end, self.at_order + 1

    def _rotate_to(self, rotations: int) -> None:
        if self.rotations < 0:
            if self.whole:
                self.placed = [(k, a) for k, a in enumerate(np.cumsum([0] + [s.n_hops for s in self.shards])[:-1])]
            else:
                while self.write + self.shards[shard := self._upcoming()].n_hops <= self.capacity:
                    self._place(shard)
            self.rotations = 0
        while self.rotations < rotations:
            self._place(self._upcoming())
            self.rotations += 1

    def at(self, step: int) -> Sentences:
        """The sentences of the ring as step's batch draws from it, the shards it lacks read in."""
        rotations = 0 if self.whole else (step - 1) // self.rotate_steps
        if self.view is not None and rotations == self.rotations:
            return self.view
        if rotations < self.rotations:
            raise ValueError(f"step {step} is behind the ring's {self.rotations} rotations")
        self._rotate_to(rotations)
        for shard, start in self.placed:
            if (shard, start) not in self.held:
                self.ring[start : start + self.shards[shard].n_hops] = self.shards[shard].features()
        self.held = set(self.placed)
        mine = [self.shards[shard] for shard, _ in self.placed]
        first = np.concatenate([start + self.shards[shard].first for shard, start in self.placed])
        lengths = np.concatenate([np.diff(s.unit_starts) for s in mine])
        units = Units(np.concatenate([s.units for s in mine]), np.concatenate([[0], np.cumsum(lengths)]))
        self.view = Sentences(self.ring, first, np.concatenate([s.hops for s in mine]), units)
        return self.view


def batch_of(data: Sentences, picks: np.ndarray, multiple: int) -> tuple[np.ndarray, np.ndarray, list[np.ndarray]]:
    """The picked sentences zero-padded at the end to the longest, rounded up to multiple: (batch, hops, dims) float32,
    their hops and units."""
    width = -(-int(data.hops[picks].max()) // multiple) * multiple
    x = np.zeros((len(picks), width, data.features.shape[1]), dtype=np.float32)
    for row, k in enumerate(picks):
        x[row, : data.hops[k]] = data.features[data.first[k] : data.first[k] + data.hops[k]]
    return x, data.hops[picks], [data.units[k] for k in picks]


def mask(x: np.ndarray, hops: np.ndarray, spec: dict, n_mel: int, rng: np.random.Generator) -> None:
    """SpecAugment in place on normalised sentences: zero over spans of mel bands and spans of each one's hops."""
    for w, n in zip(x, hops, strict=True):
        for _ in range(spec["bands"]):
            width = int(rng.integers(spec["band_width"] + 1))
            at = int(rng.integers(n_mel - width + 1))
            w[:n, at : at + width] = 0.0
        for _ in range(spec["hops"]):
            width = min(int(rng.integers(spec["hop_width"] + 1)), int(n))
            at = int(rng.integers(n - width + 1))
            w[at : at + width] = 0.0


def need_frames(units: np.ndarray) -> int:
    """The fewest CTC frames units fit in: one a unit, and a blank between two equal neighbours."""
    return len(units) + int(np.count_nonzero(units[1:] == units[:-1]))


def resampled(x: np.ndarray, places: np.ndarray) -> np.ndarray:
    """x (hops, dims) at the hop nearest each fractional place: hops dropped or repeated, never two blended, as the
    board never gives a blend of two frames (KEHOACH 3.12)."""
    return x[np.minimum(np.rint(places).astype(np.int64), len(x) - 1)]


def augmented(
    x: np.ndarray, units: np.ndarray, spec: dict, n_mel: int, stride: int, rng: np.random.Generator
) -> np.ndarray:
    """One sentence's raw features (hops, dims), every draw new (KEHOACH 3.12): said tempo times as fast with its pitch
    kept, never in fewer hops than CTC's frames for its units; SpecAugment's time warp about one hop; a straight slope
    across the mel bands."""
    n = len(x)
    rate = math.exp(rng.uniform(math.log(spec["tempo"][0]), math.log(spec["tempo"][1])))
    m = max(min(n, stride * need_frames(units)), round(n / rate))
    hop = np.arange(m, dtype=np.float64)
    w = spec["warp_hops"]
    if m > 2 * (w + 1):
        at, by = int(rng.integers(w + 1, m - w - 1)), int(rng.integers(-w, w + 1))
        hop = np.where(hop < at, hop * (at + by) / at, at + by + (hop - at) * (m - 1 - at - by) / (m - 1 - at))
    y = resampled(x, hop * (n - 1) / max(m - 1, 1))
    y[:, n_mel + DELTA_PITCH] *= (n - 1) / max(m - 1, 1)
    tilt_db = rng.uniform(-spec["tilt_db"], spec["tilt_db"])
    y[:, :n_mel] += np.linspace(-tilt_db, tilt_db, n_mel, dtype=np.float32) * LOG_PER_DB
    return y


def augmented_batch(
    data: Sentences, picks: np.ndarray, multiple: int, spec: dict, n_mel: int, stride: int, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray, list[np.ndarray]]:
    """batch_of over the picked sentences each augmented, their hops as augmented."""
    said = [
        augmented(
            data.features[data.first[k] : data.first[k] + data.hops[k]].astype(np.float32),
            data.units[k],
            spec,
            n_mel,
            stride,
            rng,
        )
        for k in picks
    ]
    hops = np.array([len(s) for s in said])
    x = np.zeros((len(picks), -(-int(hops.max()) // multiple) * multiple, data.features.shape[1]), dtype=np.float32)
    for row, s in enumerate(said):
        x[row, : len(s)] = s
    return x, hops, [data.units[k] for k in picks]


def frames_of(hops: np.ndarray, stride: int) -> np.ndarray:
    """CTC frames the net gives for each sentence's hops."""
    return -(-hops // stride)


def encoded_of(net: encoder.CtcNet, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """The encoder's frames (batch, width, frames) of normalised sentences (batch, hops, dims), and the CTC head's
    per-frame log-probabilities (batch, classes, frames) of them."""
    encoded = net.encode(x.transpose(1, 2))
    return encoded, net.head(encoded).log_softmax(1)


def loss_of(net: encoder.CtcNet, cfg: dict, x: torch.Tensor, frames: np.ndarray, units: list[np.ndarray]):
    """The CTC loss of a batch; with a transducer, its RNN-T loss plus rnnt.ctc_weight times that (ADR-0016)."""
    encoded, log_probs = encoded_of(net, x)
    loss = ctc_loss(log_probs, frames, units)
    if net.transducer is None:
        return loss
    r = cfg["rnnt"]
    return transducer.rnnt_loss_of(net.transducer, encoded, frames, units, r["lattice_cells"]) + r["ctc_weight"] * loss


def ctc_loss(log_probs: torch.Tensor, frames: np.ndarray, units: list[np.ndarray]) -> torch.Tensor:
    return functional.ctc_loss(
        log_probs.permute(2, 0, 1),
        torch.from_numpy(np.concatenate(units)).to(log_probs.device),
        torch.from_numpy(frames),
        torch.tensor([len(u) for u in units]),
        blank=BLANK,
        zero_infinity=True,
    )


def stream_penalty(kept: list[list[torch.Tensor]], spec: dict) -> torch.Tensor:
    """spec's weight times, summed over every point of every layer's stream that encoder.streams_kept kept, the mean
    over frames of the square of the octaves a frame's root mean square sits above spec's cap_rms (KEHOACH 3.12)."""
    cap = math.log2(spec["cap_rms"])
    above = [0.5 * torch.log2(ms.clamp_min(MEAN_SQUARE_FLOOR)) - cap for layer in kept for ms in layer]
    return spec["weight"] * torch.stack([functional.relu(octaves).pow(2).mean() for octaves in above]).sum()


def best_path(log_probs: np.ndarray) -> list[int]:
    """The classes of the most likely frame sequence, repeats merged and blanks dropped."""
    path = log_probs.argmax(axis=0)
    return [int(c) for k, c in enumerate(path) if c != BLANK and (k == 0 or c != path[k - 1])]


def edit_distance(a: list[int], b: list[int] | np.ndarray) -> int:
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, start=1):
        diagonal, row[0] = row[0], i
        for j, y in enumerate(b, start=1):
            diagonal, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, diagonal + int(x != y))
    return row[-1]


def evaluate(net: encoder.CtcNet, data: Sentences, stats: tuple, device: str, cells: int | None = None) -> dict:
    """Mean CTC loss of val and the unit error rate of the best path over every val sentence, and the root mean square
    of the loudest frame of any layer's stream with that layer, counted through the stacks; with a transducer, also
    its mean RNN-T loss per unit, its lattice built at most cells cells at a time, and the unit error rate of its
    greedy path."""
    mean, std = stats
    stride = net.front.hop_stride
    losses, rnnt_losses, errors, rnnt_errors, total, peaks = [], [], 0, 0, 0, []
    with torch.no_grad(), encoder.streams_kept(net) as kept:
        for k in range(0, len(data.first), VAL_BATCH):
            picks = np.arange(k, min(k + VAL_BATCH, len(data.first)))
            x, hops, units = batch_of(data, picks, net.chunk_multiple)
            encoded, log_probs = encoded_of(net, torch.from_numpy((x - mean) / std).to(device))
            peaks.append([float(torch.stack([ms.max() for ms in layer]).max()) for layer in kept])
            for layer in kept:
                layer.clear()
            frames = frames_of(hops, stride)
            losses.append(float(ctc_loss(log_probs, frames, units)) * len(picks))
            heard = log_probs.cpu().numpy()
            for row, n in enumerate(frames):
                errors += edit_distance(best_path(heard[row, :, :n]), units[row])
                total += len(units[row])
            if net.transducer is not None:
                rnnt_losses.append(
                    float(transducer.rnnt_loss_of(net.transducer, encoded, frames, units, cells)) * len(picks)
                )
                paths = transducer.greedy_paths(net.transducer, encoded, frames)
                rnnt_errors += sum(edit_distance(p, u) for p, u in zip(paths, units, strict=True))
    loudest = np.sqrt(np.max(peaks, axis=0))
    row = {"loss": sum(losses) / len(data.first), "unit_error_rate": errors / total}
    row |= {"stream_rms_max": float(loudest.max()), "stream_layer": int(loudest.argmax())}
    if net.transducer is not None:
        row |= {"rnnt_loss": sum(rnnt_losses) / len(data.first), "rnnt_unit_error_rate": rnnt_errors / total}
    return row


def checkpoint(run: Path, step: int | None = None) -> Path:
    """An evaluated step's weights, or with no step the last state a resumed run goes on from."""
    return run / "checkpoints" / ("last.pt" if step is None else f"step_{step:06d}.pt")


@contextmanager
def pause_asked() -> Iterator[Callable[[], bool]]:
    """Whether Ctrl-C or SIGTERM came since entering: a loop inside pauses at its next step, not part way through
    one; the handlers before are back on leaving."""
    asked = []
    before = {s: signal.signal(s, lambda signum, frame: asked.append(signum)) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield lambda: bool(asked)
    finally:
        for s, handler in before.items():
            signal.signal(s, handler)


def train(cfg: dict, sets: dict, device: str, run: Path | None = None, resume: bool = False) -> tuple:
    """The net of the last step, the feature statistics and one row of val figures per evaluation; sets["train"] a
    Pool, sets["val"] Sentences. With run, each evaluated net is saved beside the state the run goes on from when
    resumed: weights, optimiser, schedule, the batch draws, the history and the train losses since the last
    evaluation, so a resumed run ends where an unbroken one would. Ctrl-C saves that state after the step under way
    and raises KeyboardInterrupt."""
    spec = cfg["train"]
    rng = seed_everything(spec["seed"])
    pool = sets["train"]
    mean, std = pool.stats()
    net = encoder.build(cfg).to(device)
    optimiser = torch.optim.Adam(net.parameters(), lr=spec["learning_rate"])
    final = spec["final_learning_rate"] / spec["learning_rate"]
    schedule = torch.optim.lr_scheduler.LambdaLR(
        optimiser, lambda step: final + (1 - final) * 0.5 * (1 + math.cos(math.pi * step / spec["steps"]))
    )
    history, first, losses, penalties = [], 1, [], []
    if resume:
        state = torch.load(checkpoint(run), map_location=device, weights_only=False)
        net.load_state_dict(state["model"])
        optimiser.load_state_dict(state["optimiser"])
        schedule.load_state_dict(state["schedule"])
        rng.bit_generator.state = state["draws"]
        history, first, losses = state["history"], state["step"] + 1, state.get("losses", [])
        penalties = state.get("penalties", [])

    def save(step: int) -> None:
        state = {"model": net.state_dict(), "optimiser": optimiser.state_dict(), "step": step, "losses": losses}
        state |= {"penalties": penalties, "schedule": schedule.state_dict(), "draws": rng.bit_generator.state}
        torch.save(state | {"history": history}, checkpoint(run))

    n_mel = pool.dims - pitch.N_FEATURES
    said = f"{pool.sentences} sentences, {pool.hours:.1f} h, a ring of {len(pool.ring) / HOPS_PER_S / 3600:.1f} h"
    print(f"{said}; steps {first} to {spec['steps']} of {spec['batch']}", flush=True)
    with pause_asked() as paused:
        for step in range(first, spec["steps"] + 1):
            data = pool.at(step)
            picks = rng.integers(len(data.first), size=spec["batch"])
            if "augment" in spec:
                stride = net.front.hop_stride
                x, hops, units = augmented_batch(data, picks, net.chunk_multiple, spec["augment"], n_mel, stride, rng)
            else:
                x, hops, units = batch_of(data, picks, net.chunk_multiple)
            x = (x - mean) / std
            mask(x, hops, spec["masks"], n_mel, rng)
            with encoder.streams_kept(net) as kept:
                loss = loss_of(net, cfg, torch.from_numpy(x).to(device), frames_of(hops, net.front.hop_stride), units)
            penalty = stream_penalty(kept, spec["stream"]) if "stream" in spec else torch.zeros((), device=device)
            optimiser.zero_grad()
            (loss + penalty).backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), spec["clip_norm"])
            optimiser.step()
            schedule.step()
            losses.append(loss.item())
            penalties.append(penalty.item())
            if step % spec["eval_every"] == 0 or step == spec["steps"]:
                net.eval()
                row = {"step": step, "train_loss": float(np.mean(losses)), "stream_penalty": float(np.mean(penalties))}
                row |= {"lr": schedule.get_last_lr()[0]}
                row |= evaluate(net, sets["val"], (mean, std), device, (cfg.get("rnnt") or {}).get("lattice_cells"))
                net.train()
                losses, penalties = [], []
                history.append(row)
                print(row_line(row), flush=True)
                if run:
                    checkpoint(run, step).parent.mkdir(parents=True, exist_ok=True)
                    torch.save(net.state_dict(), checkpoint(run, step))
                    save(step)
            if paused():
                if run:
                    checkpoint(run).parent.mkdir(parents=True, exist_ok=True)
                    save(step)
                    print(f"paused after step {step}; make ctc-train RESUME={run} goes on", flush=True)
                raise KeyboardInterrupt
    net.eval()
    return net, (mean, std), history


def load_sets(cfg: dict) -> dict:
    """The sentences of cfg's split with units in its dialect, at most train.max_s long: train a Pool of
    train.pool_gb in float16, with split.board the board cut's shard but its split.board.noise runs,
    split.board.repeat times among them (KEHOACH 1.3, 3.12), val Sentences in float32."""
    paths = data_paths()
    if stale := built.unbuilt(cfg, paths):
        raise ValueError(f"{', '.join(str(p) for p in stale)}: not simulated as the config asks; make ctc-features")
    spec, version = cfg["train"], cfg["split"]["version"]
    folder = paths["splits"] / "command" / version
    split_files = sorted(folder.glob("*.txt"))
    root = paths["processed"] / "command" / version
    roles = {"train": [f for f in split_files if splits.role_of(f.name) == "train"], "val": [folder / "val.txt"]}
    listed = {r.item for files in roles.values() for f in files for r in splits.read_split(f)}
    clips = screen.kept_clips(load_yaml(screen.CONFIG), paths, "speech")
    units_of = sentence_units(clips, listed, spec["dialect"])
    longest = round(spec["max_s"] * HOPS_PER_S)
    dims = encoder.n_dims(cfg)
    capacity = int(spec["pool_gb"] * GB // (dims * np.dtype(POOL_DTYPE).itemsize))
    shards = shards_of([root / f.stem for f in roles["train"]], units_of, longest)
    if board := cfg["split"].get("board"):
        cut = root / built.BOARD
        units = built.board_units(cut, spec["dialect"], board.get("noise", []))
        shards += shards_of([cut], units, longest) * board["repeat"]
    return {
        "train": Pool(shards, dims, capacity, spec["rotate_steps"], spec["seed"]),
        "val": load_role([root / f.stem for f in roles["val"]], units_of, longest, "float32"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--resume", type=Path, metavar="RUN", help="go on from a run's last checkpoint")
    args = parser.parse_args(argv)
    cfg = (
        load_run_config(args.resume)
        if args.resume
        else apply_overrides(load_yaml(ctc.CONFIG), args.overrides) | {"listen": contract_front()}
    )
    paths = data_paths()
    split_files = sorted((paths["splits"] / "command" / cfg["split"]["version"]).glob("*.txt"))
    sets = load_sets(cfg)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    run = args.resume or create_run_dir(paths["artifacts"], BRANCH, cfg, split_files)
    try:
        net, (mean, std), history = train(cfg, sets, device, run, bool(args.resume))
    except KeyboardInterrupt:
        return 128 + signal.SIGINT
    torch.save(net.state_dict(), run / "model.pt")
    np.savez(run / "feature_stats.npz", mean=mean, std=std)
    report = {"val": history[-1], "history": history}
    (run / "metrics.yaml").write_text(yaml.safe_dump(report, sort_keys=False), encoding="utf-8")
    print(f"{run}\n" + yaml.safe_dump({"val": history[-1]}, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
