"""Rung 4 of the ctc net (KEHOACH 3.14): its graph, quantised for a batch, trained on with CTC through ESP-PPQ's fake
quantisation, on the run's train sentences masked as train.py masks them; a val row before the first step and every
eval_every steps."""

from __future__ import annotations

import math

import numpy as np
import torch

from srpipe.compress.quant import qat_espdl
from srpipe.core.seed import seed_everything
from srpipe.dsp.spec import pitch
from srpipe.tasks.command.ctc import train
from srpipe.tasks.command.ctc.model import encoder


def as_input(x: np.ndarray, device: str) -> torch.Tensor:
    """Normalised sentences (batch, hops, dims) as the graph reads them, (batch, dims, hops)."""
    return torch.from_numpy(np.ascontiguousarray(x.transpose(0, 2, 1))).to(device)


def evaluate(net: qat_espdl.Trainable, data: train.Sentences, stats: tuple, model: encoder.CtcNet, device: str) -> dict:
    """Mean CTC loss of val and the unit error rate of the best path, in batches of the graph's batch, the last one
    filled up by repeating its own sentences, which are counted once."""
    mean, std = stats
    batch, stride = qat_espdl.batch_of(net.graph), model.front.hop_stride
    losses, errors, total = [], 0, 0
    for k in range(0, len(data.first), batch):
        real = np.arange(k, min(k + batch, len(data.first)))
        x, hops, units = train.batch_of(data, np.resize(real, batch), model.chunk_multiple)
        log_probs = net.infer(as_input((x - mean) / std, device)).log_softmax(1)
        frames, n = train.frames_of(hops, stride), len(real)
        losses.append(float(train.ctc_loss(log_probs[:n], frames[:n], units[:n])) * n)
        heard = log_probs.cpu().numpy()
        for row in range(n):
            errors += train.edit_distance(train.best_path(heard[row, :, : frames[row]]), units[row])
            total += len(units[row])
    return {"loss": sum(losses) / len(data.first), "unit_error_rate": errors / total}


def fit(graph, sets: dict[str, train.Sentences], stats: tuple, cfg: dict, trained: dict, model, device: str) -> list:
    """graph trained quant.qat.steps steps of its batch at a cosine-decayed learning rate, masked and clipped as the
    run trained (trained, its config); the val rows."""
    spec = cfg["quant"]["qat"]
    mean, std = stats
    net = qat_espdl.Trainable(graph, device)
    optimiser = torch.optim.Adam(net.parameters, lr=spec["learning_rate"])
    final = spec["final_learning_rate"] / spec["learning_rate"]
    schedule = torch.optim.lr_scheduler.LambdaLR(
        optimiser, lambda step: final + (1 - final) * 0.5 * (1 + math.cos(math.pi * step / spec["steps"]))
    )
    rng = seed_everything(cfg["quant"]["seed"])
    data, batch = sets["train"], qat_espdl.batch_of(graph)
    n_mel = data.features.shape[1] - pitch.N_FEATURES
    history = [{"step": 0} | evaluate(net, sets["val"], stats, model, device)]
    print(history[0], flush=True)
    losses = []
    for step in range(1, spec["steps"] + 1):
        x, hops, units = train.batch_of(data, rng.integers(len(data.first), size=batch), model.chunk_multiple)
        x = (x - mean) / std
        train.mask(x, hops, trained["train"]["masks"], n_mel, rng)
        log_probs = net(as_input(x, device)).log_softmax(1)
        loss = train.ctc_loss(log_probs, train.frames_of(hops, model.front.hop_stride), units)
        optimiser.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(net.parameters, trained["train"]["clip_norm"])
        optimiser.step()
        schedule.step()
        losses.append(loss.item())
        if step % spec["eval_every"] == 0 or step == spec["steps"]:
            row = {"step": step, "train_loss": float(np.mean(losses)), "lr": schedule.get_last_lr()[0]}
            history.append(row | evaluate(net, sets["val"], stats, model, device))
            print(" ".join(f"{k} {v:.4g}" for k, v in history[-1].items()), flush=True)
            losses = []
    net.freeze()
    return history
