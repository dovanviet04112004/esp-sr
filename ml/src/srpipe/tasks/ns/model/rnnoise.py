"""RNNoise-16k (KEHOACH 3.9, ADR-0014): RNNoise's topology on 18 bands rebuilt on the 257-bin grid, with PyTorch's GRU,
the one esp-dl runs, where RNNoise has ReLU candidates.

Features equals postproc.bands.Features run hop by hop from a reset, batched over (batch, hops); the net reads them
normalised by train's statistics and gives 18 band gains, spread to 257 bins through the same triangles, and a speech
logit.
"""

from __future__ import annotations

import math

import torch
from torch import Tensor, nn

from srpipe.tasks.ns.postproc import bands


def delayed(x: Tensor, hops: int) -> Tensor:
    """x (batch, hops, ...) delayed by hops along time, zeros at the start."""
    if hops == 0:
        return x
    return torch.cat([torch.zeros_like(x[:, :hops]), x[:, :-hops]], dim=1)[:, : x.shape[1]]


class Features(nn.Module):
    """The slot's power (batch, hops, 257) to RNNoise-16k's features (batch, hops, n_features), causal."""

    def __init__(self, spec: dict, power_floor: float) -> None:
        super().__init__()
        self.register_buffer("w", torch.from_numpy(bands.triangles(spec["bands_hz"])))
        self.register_buffer("dct", torch.from_numpy(bands.dct_table(len(spec["bands_hz"]))))
        self.floor = float(power_floor)
        self.range = float(spec["range_db"] * bands.LN10_OVER_10)
        self.follow = float(spec["follow_db"] * bands.LN10_OVER_10)
        self.delta = spec["delta_ceps"]
        self.ring = spec["variability_hops"]

    def forward(self, power: Tensor) -> Tensor:
        log = torch.log(self.floor + power @ self.w.T)
        log_max = follow = torch.full_like(log[..., 0], math.log(self.floor))
        floored = []
        for b in range(log.shape[-1]):
            v = torch.maximum(torch.maximum(log_max - self.range, follow - self.follow), log[..., b])
            log_max, follow = torch.maximum(log_max, v), torch.maximum(follow - self.follow, v)
            floored.append(v)
        c0 = torch.stack(floored, dim=-1) @ self.dct.T
        c1, c2, d = delayed(c0, 1), delayed(c0, 2), self.delta
        ring = torch.stack([delayed(c0, k) for k in range(self.ring)], dim=-2)
        dist = ((ring.unsqueeze(-2) - ring.unsqueeze(-3)) ** 2).sum(-1)
        eye = torch.eye(self.ring, dtype=torch.bool, device=power.device)
        variability = dist.masked_fill(eye, math.inf).min(-1).values.mean(-1, keepdim=True)
        smooth, rest, first = c0[..., :d] + c1[..., :d] + c2[..., :d], c0[..., d:], c0[..., :d] - c2[..., :d]
        return torch.cat([smooth, rest, first, c0[..., :d] - 2.0 * c1[..., :d] + c2[..., :d], variability], dim=-1)


class Rnnoise(nn.Module):
    """RNNoise-16k: dense, the speech GRU and its logit, the noise GRU, the denoise GRU, band gains."""

    def __init__(self, spec: dict, power_floor: float) -> None:
        super().__init__()
        n = bands.n_features(spec)
        gru = spec["gru"]
        self.features = Features(spec, power_floor)
        self.register_buffer("mean", torch.zeros(n))
        self.register_buffer("std", torch.ones(n))
        self.dense = nn.Linear(n, spec["dense"])
        self.gru_vad = nn.GRU(spec["dense"], gru["vad"], batch_first=True)
        self.vad = nn.Linear(gru["vad"], 1)
        self.gru_noise = nn.GRU(spec["dense"] + gru["vad"] + n, gru["noise"], batch_first=True)
        self.gru_denoise = nn.GRU(gru["vad"] + gru["noise"] + n, gru["denoise"], batch_first=True)
        self.gains = nn.Linear(gru["denoise"], len(spec["bands_hz"]))

    def normalised(self, power: Tensor) -> Tensor:
        return (self.features(power) - self.mean) / self.std

    def net(self, x: Tensor, state: list[Tensor] | None = None) -> tuple[Tensor, Tensor, list[Tensor]]:
        """Band gains in 0..1 and the speech logit a hop from normalised features; the GRU states after the last hop."""
        h_vad, h_noise, h_denoise = state or (None, None, None)
        d = torch.tanh(self.dense(x))
        v, h_vad = self.gru_vad(d, h_vad)
        n, h_noise = self.gru_noise(torch.cat([d, v, x], dim=-1), h_noise)
        o, h_denoise = self.gru_denoise(torch.cat([v, n, x], dim=-1), h_denoise)
        return torch.sigmoid(self.gains(o)), self.vad(v).squeeze(-1), [h_vad, h_noise, h_denoise]

    def forward(self, power: Tensor) -> tuple[Tensor, Tensor]:
        """257 gains in 0..1 and the speech logit a hop from the slot's power, from zero state."""
        band_gains, logit, _ = self.net(self.normalised(power))
        return band_gains @ self.features.w, logit
