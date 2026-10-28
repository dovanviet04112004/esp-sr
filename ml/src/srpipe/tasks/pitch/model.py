"""SwiftF0's network after its front end (Nieradzik 2025) in torch, its weights read from the pinned model.onnx:
three log spectrograms of 128 bands in, per frame 95 logits of log-spaced pitch steps and one confidence logit out
(KEHOACH 3.11)."""

from __future__ import annotations

from pathlib import Path

import torch
from torch import nn
from torch.nn import functional

BANDS = 128
SPECTRA = 3  # STFTs of 1024, 2048, 512 points, in channel order
PITCH_BINS = 95
CHANNELS, HEAD_CHANNELS, HIDDEN = 12, 4, 16
HEAD_BANDS = 96  # the lowest bands, the only ones the head reads
BLOCKS = ((7, 3, 1), (7, 1, 17), (5, 1, 27), (3, 1, 40), (3, 1, 48), (7, 3, 2))  # kernel bands, kernel frames, dilation


class Block(nn.Module):
    """Two convolutions with a ReLU between them, the input added back, then a ReLU."""

    def __init__(self, bands: int, frames: int, dilation: int) -> None:
        super().__init__()
        pad = (dilation * (bands - 1) // 2, (frames - 1) // 2)
        self.first = nn.Conv2d(CHANNELS, CHANNELS, (bands, frames), padding=pad, dilation=(dilation, 1))
        self.second = nn.Conv2d(CHANNELS, CHANNELS, (bands, frames), padding=pad, dilation=(dilation, 1))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return torch.relu(x + self.second(torch.relu(self.first(x))))


class SwiftF0Net(nn.Module):
    """(1, SPECTRA, BANDS, frames) log spectrograms to (1, PITCH_BINS, frames) pitch logits and (1, frames) confidence
    logits, as the ONNX graph computes them between its front end and its decoding."""

    def __init__(self) -> None:
        super().__init__()
        self.stem = nn.Conv2d(SPECTRA, CHANNELS, 3, padding=1)
        self.blocks = nn.ModuleList(Block(*b) for b in BLOCKS)
        self.head = nn.Conv2d(CHANNELS, HEAD_CHANNELS, 3)
        self.spread = nn.Conv2d(HEAD_CHANNELS, HEAD_CHANNELS, (5, 1), padding=(2, 0))
        self.pitch = nn.Conv2d(HEAD_CHANNELS, 1, 1)
        self.hidden = nn.Conv1d(HEAD_CHANNELS + 1, HIDDEN, 3, padding=1)
        self.confidence = nn.Conv1d(HIDDEN, 1, 1)

    def forward(self, spectra: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        x = torch.relu(self.stem(spectra))
        for block in self.blocks:
            x = block(x)
        # The head pads one band below and none above, so 96 bands give the 95 pitch steps.
        head = torch.relu(self.head(functional.pad(x[:, :, :HEAD_BANDS], (1, 1, 1, 0))))
        peaks = self.spread(head).amax(dim=2)
        level = spectra.mean(dim=(1, 2))[:, None]
        confidence = self.confidence(torch.relu(self.hidden(torch.cat([peaks, level], dim=1))))
        return self.pitch(head)[:, 0], confidence[:, 0]


def load(onnx_file: Path) -> SwiftF0Net:
    """The network with each convolution's weights and bias from model.onnx's Conv nodes, which run in the order the
    network registers its convolutions."""
    import onnx
    from onnx import numpy_helper

    graph = onnx.load(str(onnx_file)).graph
    tensors = {t.name: numpy_helper.to_array(t) for t in graph.initializer}
    convs = [n for n in graph.node if n.op_type == "Conv"]
    net = SwiftF0Net()
    mine = [m for m in net.modules() if isinstance(m, nn.Conv1d | nn.Conv2d)]
    if len(convs) != len(mine):
        raise ValueError(f"{onnx_file}: {len(convs)} Conv nodes, the network has {len(mine)} convolutions")
    with torch.no_grad():
        for conv, node in zip(mine, convs, strict=True):
            weight, bias = (torch.from_numpy(tensors[name].copy()) for name in node.input[1:3])
            if weight.shape != conv.weight.shape:
                found, wanted = tuple(weight.shape), tuple(conv.weight.shape)
                raise ValueError(f"{node.name or node.output[0]}: weight {found}, the network's {wanted}")
            conv.weight.copy_(weight)
            conv.bias.copy_(bias)
    return net.eval()
