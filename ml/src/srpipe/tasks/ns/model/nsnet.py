"""NSNet-16k (KEHOACH 3.9, ADR-0014): the NSNet2 family on the grid, the log power of the first bins normalised by
train's statistics, a dense layer, stacked GRUs, a sigmoid gain a bin, the last bin's gain spread to the Nyquist bin."""

from __future__ import annotations

import torch
from torch import Tensor, nn

from srpipe.generated import grid


class Nsnet(nn.Module):
    """One size of NSNet-16k: hidden units of its dense layer and of each GRU."""

    def __init__(self, spec: dict, hidden: int, power_floor: float) -> None:
        super().__init__()
        self.bins, self.floor = spec["bins"], float(power_floor)
        self.register_buffer("mean", torch.zeros(self.bins))
        self.register_buffer("std", torch.ones(self.bins))
        self.dense_in = nn.Linear(self.bins, hidden)
        self.grus = nn.ModuleList(nn.GRU(hidden, hidden, batch_first=True) for _ in range(spec["layers"]))
        self.dense_out = nn.Linear(hidden, self.bins)

    def features(self, power: Tensor) -> Tensor:
        return torch.log(power[..., : self.bins] + self.floor)

    def normalised(self, power: Tensor) -> Tensor:
        return (self.features(power) - self.mean) / self.std

    def net(self, x: Tensor, state: list[Tensor] | None = None) -> tuple[Tensor, None, list[Tensor]]:
        """Gains in 0..1 of the first bins a hop from normalised features; each GRU's state after the last hop."""
        y, after = torch.relu(self.dense_in(x)), []
        for gru, h in zip(self.grus, state or [None] * len(self.grus), strict=True):
            y, h = gru(y, h)
            after.append(h)
        return torch.sigmoid(self.dense_out(y)), None, after

    def forward(self, power: Tensor) -> tuple[Tensor, None]:
        """257 gains in 0..1 a hop from the slot's power, from zero state; NSNet-16k has no speech head."""
        g, _, _ = self.net(self.normalised(power))
        return torch.cat([g, g[..., -1:].expand(*g.shape[:-1], grid.N_BINS - self.bins)], dim=-1), None
