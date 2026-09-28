"""The causal dilated TCN of wake (KEHOACH 3.11): each convolution pads the past only, so no output reads a later hop
and esp-dl streams the network one hop at a time, a StreamingCache ahead of each dilated convolution."""

from __future__ import annotations

from torch import Tensor, nn
from torch.nn import functional


class Block(nn.Module):
    """x + mix(relu(conv(x))), where conv sees (kernel - 1) * dilation hops back and none ahead."""

    def __init__(self, channels: int, kernel: int, dilation: int) -> None:
        super().__init__()
        self.past_hops = (kernel - 1) * dilation
        self.conv = nn.Conv1d(channels, channels, kernel, dilation=dilation)
        self.mix = nn.Conv1d(channels, channels, 1)

    def forward(self, x: Tensor) -> Tensor:
        # ONNX simplification folds this pad into the convolution's leading pads, which ESP-PPQ turns into a cache.
        y = self.conv(functional.pad(x, (self.past_hops, 0)))
        return x + self.mix(functional.relu(y))


class Tcn(nn.Module):
    """Log-mel (batch, n_bands, hops) to per-hop logits (batch, n_out, hops)."""

    def __init__(self, n_bands: int, channels: int, kernel: int, dilations: list[int], n_out: int = 1) -> None:
        super().__init__()
        self.inp = nn.Conv1d(n_bands, channels, 1)
        self.blocks = nn.Sequential(*[Block(channels, kernel, d) for d in dilations])
        self.out = nn.Conv1d(channels, n_out, 1)
        self.receptive_field_hops = 1 + sum(b.past_hops for b in self.blocks)

    def forward(self, x: Tensor) -> Tensor:
        return self.out(self.blocks(functional.relu(self.inp(x))))
