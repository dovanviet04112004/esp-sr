"""The RNN-T half of the command net (KEHOACH 3.12, ADR-0016): MultiNet7's stateless predictor and its joiner over the
units of the ctc head, the RNN-T loss over the joiner's lattice and the greedy path val reads."""

from __future__ import annotations

import numpy as np
import torch
from torch import Tensor, nn
from torch.nn import functional
from torch.utils.checkpoint import checkpoint

from srpipe.tasks.command.ctc.postproc.ctc_score import BLANK


class Predictor(nn.Module):
    """Each prefix of emitted units, blank standing before the first and nothing before that, to one vector: its last
    context classes embedded, mixed by a depthwise convolution over them, ReLU. (batch, units) ids to (batch, units,
    width); the id pad embeds to zeros, what stands before the leading blank."""

    def __init__(self, classes: int, width: int, context: int) -> None:
        super().__init__()
        self.context, self.pad = context, classes
        self.embed = nn.Embedding(classes + 1, width, padding_idx=classes)
        self.mix = nn.Conv1d(width, width, context, groups=width, bias=False)

    def forward(self, units: Tensor) -> Tensor:
        embedded = self.embed(units).transpose(1, 2)
        return functional.relu(self.mix(functional.pad(embedded, (self.context - 1, 0)))).transpose(1, 2)


class Joiner(nn.Module):
    """An encoder frame and a predictor vector, each projected to width, summed, tanh, to class logits; the two
    projections run once per frame and once per prefix, the rest once per pair."""

    def __init__(self, encoder_width: int, predictor_width: int, width: int, classes: int) -> None:
        super().__init__()
        self.frame_proj = nn.Linear(encoder_width, width)
        self.prefix_proj = nn.Linear(predictor_width, width)
        self.out = nn.Linear(width, classes)

    def forward(self, frames: Tensor, prefixes: Tensor) -> Tensor:
        """Logits of projected frames and projected prefixes whose shapes broadcast."""
        return self.out(torch.tanh(frames + prefixes))


class Transducer(nn.Module):
    """The predictor and joiner of the rnnt section of command_ctc.yaml."""

    def __init__(self, encoder_width: int, classes: int, width: int, context: int) -> None:
        super().__init__()
        self.predictor = Predictor(classes, width, context)
        self.joiner = Joiner(encoder_width, width, width, classes)


def with_blank_first(units: list[np.ndarray], device: torch.device) -> tuple[Tensor, Tensor]:
    """The predictor's input, each unit list as classes after a leading blank, zero-padded: (batch, longest + 1); and
    the targets as classes, zero-padded: (batch, longest) int32."""
    longest = max(len(u) for u in units)
    targets = np.zeros((len(units), longest), dtype=np.int32)
    for row, u in enumerate(units):
        targets[row, : len(u)] = np.asarray(u, dtype=np.int32) + 1
    targets = torch.from_numpy(targets).to(device)
    return functional.pad(targets.long(), (1, 0), value=BLANK), targets


def lattice_loss(out: nn.Module, frames: Tensor, prefixes: Tensor, targets: Tensor, n_frames: Tensor, n_units: Tensor):
    """Summed RNN-T loss of a few sentences, each over its own frames and units, the lattice cut to the longest."""
    from torchaudio.functional import rnnt_loss

    t, u = int(n_frames.max()), int(n_units.max())
    logits = out(torch.tanh(frames[:, :t, None] + prefixes[:, None, : u + 1]))
    return rnnt_loss(logits, targets[:, :u].contiguous(), n_frames, n_units, blank=BLANK, reduction="none") / n_units


def rnnt_loss_of(
    transducer: Transducer, encoded: Tensor, n_frames: np.ndarray, units: list[np.ndarray], chunk: int
) -> Tensor:
    """Mean over the batch of each sentence's RNN-T loss per unit, encoded (batch, width, frames). The joiner's
    lattice is built chunk sentences at a time and built again for the backward pass, so one chunk's is all a step
    holds: a batch of 12 s sentences would need gigabytes."""
    device = encoded.device
    prefixes, targets = with_blank_first(units, device)
    frames = transducer.joiner.frame_proj(encoded.transpose(1, 2))
    projected = transducer.joiner.prefix_proj(transducer.predictor(prefixes))
    n_f = torch.from_numpy(np.asarray(n_frames, dtype=np.int32)).to(device)
    n_u = torch.tensor([len(u) for u in units], dtype=torch.int32, device=device)
    losses = []
    for k in range(0, len(units), chunk):
        part = slice(k, k + chunk)
        args = (frames[part], projected[part], targets[part], n_f[part], n_u[part])
        losses.append(checkpoint(lattice_loss, transducer.joiner.out, *args, use_reentrant=False))
    return torch.cat(losses).mean()


@torch.no_grad()
def greedy_paths(transducer: Transducer, encoded: Tensor, n_frames: np.ndarray) -> list[list[int]]:
    """Each sentence's greedy RNN-T path, at most one unit a frame, as unit ids; the batch steps frame by frame."""
    batch, predictor = encoded.shape[0], transducer.predictor
    frames = transducer.joiner.frame_proj(encoded.transpose(1, 2))
    history = torch.full((batch, predictor.context), predictor.pad, dtype=torch.long, device=encoded.device)
    history[:, -1] = BLANK
    paths: list[list[int]] = [[] for _ in range(batch)]
    for t in range(int(np.max(n_frames))):
        prefix = transducer.joiner.prefix_proj(predictor(history)[:, -1])
        best = transducer.joiner(frames[:, t], prefix).argmax(-1)
        for row in torch.nonzero((best != BLANK) & (t < torch.as_tensor(n_frames, device=best.device))).flatten():
            paths[int(row)].append(int(best[row]) - 1)
        moved = best != BLANK
        history[moved] = torch.cat([history[moved, 1:], best[moved, None]], dim=1)
    return paths
