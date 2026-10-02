"""Train RNNoise-16k and NSNet-16k S/M/L on identical batches (E9-T4, KEHOACH 3.9, ADR-0014) into a run directory.

Step k's batch is a pure function of the config, the pools and k: train examples of the Mixer at the slot, as the power
of the capture, of the talker alone and of the rest. Every candidate learns the same loss on the 257 gains the slot
applies; each epoch scores val and checkpoints; the last weights are kept.
Run: python -m srpipe.tasks.ns.train [--smoke] [--only NAME ...] [--resume RUN] [--set KEY=VALUE ...]"""

from __future__ import annotations

import argparse
import bisect
import copy
import hashlib
import math
import resource
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import yaml
from torch import Tensor, nn
from torch.nn import functional
from torch.optim.lr_scheduler import LambdaLR
from torch.utils.data import DataLoader, Dataset

from srpipe.core.config import apply_overrides, data_paths, load_device, load_yaml
from srpipe.core.logger import get_logger
from srpipe.core.run_dir import create_run_dir
from srpipe.core.seed import seed_everything
from srpipe.generated import grid
from srpipe.tasks import ns
from srpipe.tasks.ns import data, model

PARTS = ("power", "speech", "noise")
KIB = 1024


def smoke_config(cfg: dict) -> dict:
    """make ns-smoke's config: train.smoke over train, its example_s over mix."""
    out = copy.deepcopy(cfg)
    smoke = out["train"].pop("smoke")
    out["mix"]["example_s"] = smoke.pop("example_s")
    out["train"] |= smoke
    return out


@dataclass(frozen=True)
class Plan:
    """The run's steps: each epoch's examples in the Mixer's order, batch after batch; firsts[e] is epoch e's first
    step and firsts[-1] the run's step count."""

    batch: int
    firsts: tuple[int, ...]

    @property
    def steps(self) -> int:
        return self.firsts[-1]

    def where(self, step: int) -> tuple[int, int]:
        """A step's epoch and its first example in that epoch."""
        epoch = bisect.bisect_right(self.firsts, step) - 1
        return epoch, (step - self.firsts[epoch]) * self.batch

    def ends_epoch(self, step: int) -> bool:
        return step + 1 in self.firsts[1:]


def plan(cfg: dict, mixer: data.Mixer) -> Plan:
    spec, firsts = cfg["train"], [0]
    for epoch in range(spec["epochs"]):
        firsts.append(firsts[-1] + len(mixer.kinds(epoch)) // spec["batch"])
    cap = spec.get("steps", firsts[-1])
    return Plan(spec["batch"], tuple(min(f, cap) for f in firsts))


def batch_of(examples: Iterable[data.Example], count: int, gains: np.ndarray) -> dict[str, Tensor]:
    """count examples at the slot, each into its row as it comes: powers of the capture, of the talker alone and of
    the rest (count, hops, 257), and vad; in shared memory, so a loader worker hands it over without a copy."""
    out: dict[str, Tensor] = {}
    for i, e in enumerate(examples):
        powers = data.slot_powers(e, gains)
        if not out:
            out = {part: torch.empty((count, *powers[0].shape)).share_memory_() for part in PARTS}
            out["vad"] = torch.empty((count, len(e.vad))).share_memory_()
        for part, value in zip(PARTS, powers, strict=True):
            out[part][i] = torch.from_numpy(value)
        out["vad"][i] = torch.from_numpy(e.vad.astype(np.float32))
    return out


class TrainBatches(Dataset):
    """Step k's batch, built in a loader worker from the train pools; the Mixer opens there, never in the parent."""

    def __init__(self, cfg: dict, dev: dict, paths: dict, steps: Plan) -> None:
        self.cfg, self.dev, self.paths, self.steps = cfg, dev, paths, steps
        self.mixer: data.Mixer | None = None

    def __len__(self) -> int:
        return self.steps.steps

    def __getitem__(self, step: int) -> dict[str, Tensor]:
        if self.mixer is None:
            self.mixer = data.Mixer(self.cfg, self.dev, self.paths, "train", self.cfg["mix"]["seed"])
        epoch, first = self.steps.where(step)
        examples = (self.mixer.example(epoch, j) for j in range(first, first + self.steps.batch))
        return batch_of(examples, self.steps.batch, self.mixer.mics.gains)


class HeldBatches(Dataset):
    """A stored val or test set (data.sets) in chunks of at most batch examples, each inside one shard; limit keeps
    only the first examples."""

    def __init__(self, folder: Path, gains: np.ndarray, batch: int, limit: int | None = None) -> None:
        self.gains, self.chunks = gains, []
        left = math.inf if limit is None else limit
        for scale in sorted(folder.glob("shard_*.scale.npy")):
            count = min(len(np.load(scale)), left)
            stem = scale.with_name(scale.name.removesuffix(".scale.npy"))
            self.chunks += [(stem, i, min(i + batch, count)) for i in range(0, count, batch)]
            left -= count
        if not self.chunks:
            raise FileNotFoundError(f"{folder} holds no shard: python -m srpipe.tasks.ns.data sets")

    def __len__(self) -> int:
        return len(self.chunks)

    def __getitem__(self, k: int) -> dict[str, Tensor]:
        stem, lo, hi = self.chunks[k]
        capture, talker, scale, vad = (
            np.load(f"{stem}.{part}.npy", mmap_mode="r")[lo:hi] for part in ("capture", "talker", "scale", "vad")
        )
        examples = (
            data.Example(np.asarray(c), np.asarray(t, dtype=np.float32).T * s, np.asarray(v), {})
            for c, t, s, v in zip(capture, talker, scale, vad, strict=True)
        )
        return batch_of(examples, hi - lo, self.gains)


def single_thread(_: int) -> None:
    torch.set_num_threads(1)


def loader(dataset: Dataset, spec: dict, workers: int, pin: bool, steps: range, persistent: bool) -> DataLoader:
    return DataLoader(
        dataset,
        batch_size=None,
        sampler=steps,
        num_workers=workers,
        multiprocessing_context="spawn" if workers else None,
        prefetch_factor=spec["prefetch"] if workers else None,
        persistent_workers=persistent and workers > 0,
        pin_memory=pin,
        worker_init_fn=single_thread,
    )


def feature_stats(nets: dict[str, nn.Module], batches: DataLoader, device: str) -> dict[str, tuple[Tensor, Tensor]]:
    """Each candidate's feature mean and deviation over the batches, summed in float64."""
    sums: dict[str, list] = {name: [0.0, 0.0, 0] for name in nets}
    with torch.no_grad():
        for batch in batches:
            power = batch["power"].to(device, non_blocking=True)
            for name, net in nets.items():
                f = net.features(power).double().flatten(0, -2)
                s = sums[name]
                s[0], s[1], s[2] = s[0] + f.sum(0), s[1] + (f * f).sum(0), s[2] + len(f)
    stats = {}
    for name, (total, square, count) in sums.items():
        mean = total / count
        std = (square / count - mean * mean).clamp_min(0.0).sqrt()
        if not torch.all(std > 0):
            flat = torch.nonzero(std == 0).flatten().tolist()
            raise ValueError(f"{name}: features {flat} never vary over the norm batches")
        stats[name] = (mean.float(), std.float())
    return stats


def gain_loss(gains: Tensor, speech: Tensor, mixture: Tensor, spec: dict, power_floor: float) -> Tensor:
    """Clean against output on compressed magnitudes (KEHOACH 3.9), as Braun and Tashev train NSNet2: per bin
    (S^c - g^c X^c)^2 of the speech and mixture magnitudes S and X, least at g = S / X; each sequence over its
    compressed mixture power."""
    c = spec["compression"]
    # A sigmoid can underflow to 0, where g**c has no gradient.
    kept = gains.clamp_min(torch.finfo(gains.dtype).tiny) ** c
    level = (mixture + power_floor) ** (c / 2)
    per_bin = (speech ** (c / 2) - kept * level) ** 2
    return (per_bin.sum(dim=(-2, -1)) / (level**2).sum(dim=(-2, -1))).mean()


def loss_of(gains: Tensor, logit: Tensor | None, batch: dict[str, Tensor], cfg: dict) -> Tensor:
    loss = gain_loss(gains, batch["speech"], batch["power"], cfg["loss"], cfg["power_floor"])
    if logit is None:
        return loss
    return loss + cfg["rnnoise"]["vad_weight"] * functional.binary_cross_entropy_with_logits(logit, batch["vad"])


def db(num: float, den: float) -> float:
    """10 log10(num / den); NaN when either is not positive: a set with no such hops."""
    return 10.0 * math.log10(num / den) if num > 0 and den > 0 else math.nan


def score(nets: dict[str, nn.Module], batches: DataLoader, cfg: dict, device: str) -> dict[str, dict[str, float]]:
    """Per candidate on a held set: the loss, and at the val floor, when there is one, the noise and speech taken down
    and the SNR gained on the slot's power after settle_s, the spectral twin of scenes.ns.figures."""
    settle = round(cfg["eval"]["settle_s"] * grid.SAMPLE_RATE_HZ / grid.HOP_SAMPLES)
    floor_db = cfg["train"]["val_floor_db"]
    floor = 0.0 if floor_db is None else 10.0 ** (floor_db / 20.0)
    sums = {name: torch.zeros(8, dtype=torch.float64, device=device) for name in nets}
    with torch.no_grad():
        for batch in batches:
            b = {k: v.to(device, non_blocking=True) for k, v in batch.items()}
            after = torch.zeros_like(b["vad"])
            after[:, settle:] = 1.0
            talk = b["vad"] * after
            noise, speech = b["noise"].sum(-1), b["speech"].sum(-1)
            for name, net in nets.items():
                gains, logit = net(b["power"])
                kept = gains.clamp_min(floor) ** 2
                noise_out, speech_out = (kept * b["noise"]).sum(-1), (kept * b["speech"]).sum(-1)
                n = len(gains)
                parts = [loss_of(gains, logit, b, cfg) * n, torch.tensor(float(n), device=device)]
                parts += [(noise * after).sum(), (noise_out * after).sum(), (speech * talk).sum()]
                parts += [(speech_out * talk).sum(), (noise * talk).sum(), (noise_out * talk).sum()]
                sums[name] += torch.stack(parts).double()
    out = {}
    for name, s in sums.items():
        loss, count, n_ref, n_out, s_ref, s_out, ns_ref, ns_out = s.tolist()
        out[name] = {
            "loss": loss / count,
            "noise_down_db": db(n_ref, n_out),
            "speech_down_db": db(s_ref, s_out),
            "snr_gain_db": db(s_out, ns_out) - db(s_ref, ns_ref),
        }
    return out


def cosine(optimiser: torch.optim.Optimizer, spec: dict, steps: int) -> LambdaLR:
    """Cosine decay from learning_rate to final_learning_rate at the run's last step, as wake."""
    final = spec["final_learning_rate"] / spec["learning_rate"]
    return LambdaLR(optimiser, lambda k: final + (1 - final) * 0.5 * (1 + math.cos(math.pi * min(k, steps) / steps)))


@dataclass
class Candidate:
    net: nn.Module
    optimiser: torch.optim.Optimizer
    schedule: LambdaLR


def candidates(cfg: dict, names: list[str], device: str, steps: int) -> dict[str, Candidate]:
    """Each candidate built right after seeding, so its start does not hang on which others train."""
    spec, out = cfg["train"], {}
    for name in names:
        torch.manual_seed(spec["seed"])
        net = model.build(cfg, name).to(device)
        optimiser = torch.optim.Adam(net.parameters(), lr=spec["learning_rate"])
        out[name] = Candidate(net, optimiser, cosine(optimiser, spec, steps))
    return out


def checkpoint(run: Path, name: str, epoch: int | None = None) -> Path:
    """An epoch's weights, or with no epoch the last state a resumed run continues from."""
    return run / "checkpoints" / name / ("last.pt" if epoch is None else f"epoch_{epoch:02d}.pt")


def save(run: Path, name: str, c: Candidate, epoch: int, step: int) -> None:
    checkpoint(run, name).parent.mkdir(parents=True, exist_ok=True)
    torch.save(c.net.state_dict(), checkpoint(run, name, epoch))
    state = {"model": c.net.state_dict(), "optimiser": c.optimiser.state_dict(), "schedule": c.schedule.state_dict()}
    torch.save(state | {"epoch": epoch, "step": step}, checkpoint(run, name))


def restore(run: Path, found: dict[str, Candidate], device: str) -> int:
    """Each candidate's last state; the step the run goes on from, the same for all."""
    steps = set()
    for name, c in found.items():
        state = torch.load(checkpoint(run, name), map_location=device)
        c.net.load_state_dict(state["model"])
        c.optimiser.load_state_dict(state["optimiser"])
        c.schedule.load_state_dict(state["schedule"])
        steps.add(state["step"])
    if len(steps) != 1:
        raise ValueError(f"{run}: the candidates stopped at different steps {sorted(steps)}")
    return steps.pop() + 1


def pools_lock(cfg: dict, paths: dict) -> str:
    """sha256 of every pool and held-set manifest the run reads."""
    manifests = [data.pool_dir(paths, cfg, "train") / "manifest.yaml"]
    manifests += [data.set_dir(paths, cfg, role) / "manifest.yaml" for role in data.HELD]
    return "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p}\n" for p in manifests)


def train(cfg: dict, dev: dict, paths: dict, run: Path, device: str, resume: bool) -> dict[str, dict]:
    """Train the run's candidates to the end of the plan; the last epoch's val figures per candidate."""
    spec, log = cfg["train"], get_logger("ns.train", run)
    mixer = data.Mixer(cfg, dev, paths, "train", cfg["mix"]["seed"])
    steps = plan(cfg, mixer)
    found = candidates(cfg, spec["candidates"], device, steps.steps)
    nets = {name: c.net for name, c in found.items()}
    pin = device == "cuda"
    history = yaml.safe_load((run / "history.yaml").read_text(encoding="utf-8")) if resume else []
    first = restore(run, found, device) if resume else 0
    log.info(f"{list(found)} on {device}: {steps.steps} steps of {steps.batch}, epochs from step {steps.firsts}")
    if not resume:
        norm = range(min(spec["norm_batches"], steps.steps))
        batches = loader(TrainBatches(cfg, dev, paths, steps), spec, spec["workers"], pin, norm, persistent=False)
        for name, (mean, std) in feature_stats(nets, batches, device).items():
            nets[name].mean.copy_(mean)
            nets[name].std.copy_(std)
            (run / name).mkdir(exist_ok=True)
            np.savez(run / name / "norm.npz", mean=mean.cpu().numpy(), std=std.cpu().numpy())
        log.info(f"feature statistics over the first {len(norm)} batches")
    held = HeldBatches(data.set_dir(paths, cfg, "val"), mixer.mics.gains, spec["batch"], spec.get("val_examples"))
    batches = loader(
        TrainBatches(cfg, dev, paths, steps), spec, spec["workers"], pin, range(first, steps.steps), persistent=True
    )
    running, waited, begun, count = dict.fromkeys(found, 0.0), 0.0, time.monotonic(), 0
    clock = begun
    for step, batch in zip(range(first, steps.steps), batches, strict=True):
        waited += time.monotonic() - clock
        b = {k: v.to(device, non_blocking=True) for k, v in batch.items()}
        for name, c in found.items():
            loss = loss_of(*c.net(b["power"]), b, cfg)
            if not torch.isfinite(loss):
                raise FloatingPointError(f"{name}: loss {loss.item()} at step {step}")
            c.optimiser.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(c.net.parameters(), spec["grad_clip_norm"])
            c.optimiser.step()
            c.schedule.step()
            running[name] += loss.item()
        count += 1
        clock = time.monotonic()
        if (step + 1) % spec["log_every"] == 0 or step + 1 == steps.steps:
            elapsed = clock - begun
            losses = " ".join(f"{name} {total / count:.5f}" for name, total in running.items())
            lr = next(iter(found.values())).schedule.get_last_lr()[0]
            log.info(
                f"step {step + 1}/{steps.steps} {losses} lr {lr:.2e} {count / elapsed:.2f} steps/s "
                f"{count * steps.batch / elapsed:.1f} examples/s data wait {waited / elapsed:.0%}"
            )
            running, waited, begun, count = dict.fromkeys(found, 0.0), 0.0, clock, 0
        if steps.ends_epoch(step):
            epoch = steps.where(step)[0]
            val = loader(held, spec, spec["val_workers"], pin, range(len(held)), persistent=False)
            for net in nets.values():
                net.eval()
            row = {"epoch": epoch, "step": step + 1, "val": score(nets, val, cfg, device)}
            for net in nets.values():
                net.train()
            history.append(row)
            (run / "history.yaml").write_text(yaml.safe_dump(history, sort_keys=False), encoding="utf-8")
            for name, c in found.items():
                save(run, name, c, epoch, step)
                figures = " ".join(f"{k} {v:.3f}" for k, v in row["val"][name].items())
                log.info(f"epoch {epoch} val {name}: {figures}")
                loss = row["val"][name]["loss"]
                if loss > spec["divergence_ratio"] * history[0]["val"][name]["loss"]:
                    raise FloatingPointError(f"{name}: val loss {loss:.4g} diverged at epoch {epoch}")
            # The val pass is timed apart: the next log line's rates and data wait cover training steps only.
            now = time.monotonic()
            begun, clock = begun + now - clock, now
    del batches
    peak_mb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / KIB
    gpu_mb = torch.cuda.max_memory_allocated() / KIB / KIB if device == "cuda" else 0.0
    log.info(f"peak RSS of a loader worker {peak_mb:.0f} MB, of the GPU {gpu_mb:.0f} MB")
    for name, net in nets.items():
        (run / name).mkdir(exist_ok=True)
        torch.save(net.state_dict(), run / name / "model.pt")
    return history[-1]["val"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--smoke", action="store_true", help="train.smoke: a few small steps, to time the loader")
    parser.add_argument("--only", nargs="+", metavar="NAME", help="train these candidates only, on the same batches")
    parser.add_argument("--resume", type=Path, metavar="RUN", help="go on from a run's last checkpoints")
    parser.add_argument(
        "--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE", help="a dotted override of ns.yaml"
    )
    args = parser.parse_args(argv)
    paths = data_paths()
    if args.resume:
        run, cfg = args.resume, load_yaml(args.resume / "config.resolved.yaml")
    else:
        cfg = apply_overrides(load_yaml(ns.CONFIG), args.overrides)
        cfg = smoke_config(cfg) if args.smoke else cfg
        cfg["train"]["candidates"] = args.only or model.names(cfg)
        for name in cfg["train"]["candidates"]:
            model.build(cfg, name)
        run = create_run_dir(paths["artifacts"], "ns", cfg, sorted(data.split_dir(paths, cfg).glob("*.txt")))
        (run / "pools.lock").write_text(pools_lock(cfg, paths), encoding="utf-8")
    seed_everything(cfg["train"]["seed"])
    torch.backends.cudnn.deterministic, torch.backends.cudnn.benchmark = True, False
    device = "cuda" if torch.cuda.is_available() else "cpu"
    val = train(cfg, load_device(cfg["device"]), paths, run, device, bool(args.resume))
    (run / "metrics.yaml").write_text(yaml.safe_dump({"val": val}, sort_keys=False), encoding="utf-8")
    print(f"{run}\n" + yaml.safe_dump(val, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
