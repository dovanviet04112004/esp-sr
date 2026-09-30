"""DS-CNN of Zhang et al. 2017 ("Hello Edge") over one window of features (KEHOACH 3.12): a convolution, depthwise
separable blocks, a mean over the whole map and a linear layer to one logit a class. Each convolution pads as
TensorFlow's SAME does, as in the paper's code, so a stride s leaves ceil(size / s) outputs."""

from __future__ import annotations

import math

from torch import Tensor, nn
from torch.nn import functional

Size = tuple[int, int]


def same_pads(size: Size, kernel: Size, stride: Size) -> tuple[tuple[int, int, int, int], Size]:
    """Zero padding in ZeroPad2d's order (dims before, after, hops before, after) and the size it leaves."""
    out = (math.ceil(size[0] / stride[0]), math.ceil(size[1] / stride[1]))
    total = [max((o - 1) * s + k - n, 0) for o, s, k, n in zip(out, stride, kernel, size, strict=True)]
    return (total[1] // 2, total[1] - total[1] // 2, total[0] // 2, total[0] - total[0] // 2), out


class Separable(nn.Module):
    """relu(bn(pointwise(relu(bn(depthwise(x)))))) at one stride."""

    def __init__(self, channels: int, kernel: Size, stride: Size, size: Size) -> None:
        super().__init__()
        pads, self.size = same_pads(size, kernel, stride)
        self.pad = nn.ZeroPad2d(pads)
        self.depthwise = nn.Conv2d(channels, channels, kernel, stride, groups=channels, bias=False)
        self.depthwise_bn = nn.BatchNorm2d(channels)
        self.pointwise = nn.Conv2d(channels, channels, 1, bias=False)
        self.pointwise_bn = nn.BatchNorm2d(channels)

    def forward(self, x: Tensor) -> Tensor:
        x = functional.relu(self.depthwise_bn(self.depthwise(self.pad(x))))
        return functional.relu(self.pointwise_bn(self.pointwise(x)))


class DsCnn(nn.Module):
    """Features (batch, 1, hops, dims) to logits (batch, n_classes); window is (hops, dims)."""

    def __init__(
        self,
        window: Size,
        n_classes: int,
        channels: int,
        first_kernel: Size,
        first_stride: Size,
        depthwise_kernel: Size,
        strides: list[Size],
    ) -> None:
        super().__init__()
        pads, size = same_pads(window, first_kernel, first_stride)
        self.pad = nn.ZeroPad2d(pads)
        self.first = nn.Conv2d(1, channels, first_kernel, first_stride, bias=False)
        self.first_bn = nn.BatchNorm2d(channels)
        self.macs = first_kernel[0] * first_kernel[1] * channels * size[0] * size[1]
        blocks = []
        for stride in strides:
            blocks.append(Separable(channels, depthwise_kernel, stride, size))
            size = blocks[-1].size
            per_position = depthwise_kernel[0] * depthwise_kernel[1] * channels + channels * channels
            self.macs += per_position * size[0] * size[1]
        self.blocks = nn.Sequential(*blocks)
        self.out = nn.Linear(channels, n_classes)
        self.macs += channels * n_classes

    def forward(self, x: Tensor) -> Tensor:
        x = functional.relu(self.first_bn(self.first(self.pad(x))))
        # A global average pool, which esp-dl runs, rather than a mean over two axes.
        return self.out(functional.adaptive_avg_pool2d(self.blocks(x), 1).flatten(1))


def build(cfg: dict, window: Size, n_classes: int, size: str | None = None) -> DsCnn:
    """The DS-CNN of the model section of command_kws.yaml, at its configured size unless size names another."""
    m = cfg["model"]
    s = m["sizes"][size or m["size"]]
    strides = [tuple(t) for t in s["strides"]]
    kernel, depthwise = tuple(m["first_kernel"]), tuple(m["depthwise_kernel"])
    return DsCnn(window, n_classes, s["channels"], kernel, tuple(s["first_stride"]), depthwise, strides)
