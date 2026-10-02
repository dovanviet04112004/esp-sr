"""The ctc net of command (KEHOACH 3.12, ADR-0013): MultiNet7's encoder frame over log-mel and pitch, CTC logits out.

Convolutions pad the past only; pools and repeats stay inside a chunk whose hops are a multiple of chunk_multiple, so
the outputs of a chunk never read a later chunk and esp-dl streams the net a chunk at a time, a StreamingCache ahead
of each convolution (srpipe.compress.quant.ptq_espdl).
"""

from __future__ import annotations

import math

import torch
from torch import Tensor, nn
from torch.nn import functional

from srpipe.core.config import CONFIGS, load_yaml
from srpipe.dsp.spec import pitch
from srpipe.tasks.command.ctc.postproc.ctc_score import n_classes


class CausalConv(nn.Module):
    """Conv1d over (batch, channels, frames) that pads kernel - 1 frames of the past and none of the future."""

    def __init__(self, c_in: int, c_out: int, kernel: int, groups: int = 1, bias: bool = True) -> None:
        super().__init__()
        self.past_frames = kernel - 1
        self.conv = nn.Conv1d(c_in, c_out, kernel, groups=groups, bias=bias)

    def forward(self, x: Tensor) -> Tensor:
        # ONNX simplification folds this pad into the convolution's leading pads, which ESP-PPQ turns into a cache.
        return self.conv(functional.pad(x, (self.past_frames, 0)))


class FeedForward(nn.Module):
    """width to ff_width and back, Swish between."""

    def __init__(self, width: int, ff_width: int) -> None:
        super().__init__()
        self.up = nn.Conv1d(width, ff_width, 1)
        self.down = nn.Conv1d(ff_width, width, 1)

    def forward(self, x: Tensor) -> Tensor:
        return self.down(functional.silu(self.up(x)))


class LiGlu(nn.Module):
    """MultiNet7's gated convolution: filter times sigmoid(gate) from one projection, then a causal depthwise
    convolution and a projection."""

    def __init__(self, width: int, kernel: int) -> None:
        super().__init__()
        self.gate_filter = nn.Conv1d(width, 2 * width, 1)
        self.depthwise = CausalConv(width, width, kernel, groups=width)
        self.out = nn.Conv1d(width, width, 1)

    def forward(self, x: Tensor) -> Tensor:
        gate, filtered = self.gate_filter(x).chunk(2, dim=1)
        return self.out(functional.silu(self.depthwise(filtered * torch.sigmoid(gate))))


class Mixer(nn.Module):
    """The mean of the last frames frames, projected: the stand-in for self-attention that MultiNet7 calls pool."""

    def __init__(self, width: int, frames: int) -> None:
        super().__init__()
        self.mean = CausalConv(width, width, frames, groups=width, bias=False)
        nn.init.constant_(self.mean.conv.weight, 1.0 / frames)
        self.mean.conv.weight.requires_grad_(False)
        self.proj = nn.Conv1d(width, width, 1)

    def forward(self, x: Tensor) -> Tensor:
        return self.proj(self.mean(x))


class ScalarRmsNorm(nn.Module):
    """x over its root mean square across channels, times one learned scale, as MultiNet7 keeps one number a layer."""

    def __init__(self, width: int, eps: float) -> None:
        super().__init__()
        self.width = width
        self.eps = eps
        self.scale = nn.Parameter(torch.ones(1))

    def forward(self, x: Tensor) -> Tensor:
        # Channels last, the scale spread over them: the form ESP-PPQ fuses into esp-dl's float RMSNormalization.
        t = x.transpose(1, 2)
        t = t / torch.sqrt(t.pow(2).mean(dim=-1, keepdim=True) + self.eps) * self.scale.expand(self.width)
        return t.transpose(1, 2)


class Layer(nn.Module):
    """Three feedforwards, the mixer and two LiGLU blocks, each added back, a norm, then a bypass to the input."""

    def __init__(self, width: int, ff_width: int, kernel: int, mixer_frames: int, eps: float, bypass: float) -> None:
        super().__init__()
        self.ff1, self.ff2, self.ff3 = (FeedForward(width, ff_width) for _ in range(3))
        self.mixer = Mixer(width, mixer_frames)
        self.conv1, self.conv2 = LiGlu(width, kernel), LiGlu(width, kernel)
        self.norm = ScalarRmsNorm(width, eps)
        self.bypass = nn.Parameter(torch.full((1,), bypass))

    def forward(self, x: Tensor) -> Tensor:
        y = x + self.ff1(x)
        y = y + self.mixer(y)
        y = y + self.conv1(y)
        y = y + self.ff2(y)
        y = y + self.conv2(y)
        y = self.norm(y + self.ff3(y))
        return x + (y - x) * self.bypass


def downsample(x: Tensor, rate: int) -> Tensor:
    """The mean of each run of rate frames, runs aligned to the chunk."""
    return functional.avg_pool1d(x, rate, rate) if rate > 1 else x


def upsample(x: Tensor, rate: int) -> Tensor:
    """Each frame repeated rate times."""
    if rate == 1:
        return x
    return torch.cat([x.unsqueeze(-1)] * rate, dim=-1).reshape(x.shape[0], x.shape[1], -1)


class Stack(nn.Module):
    """Layers run at 1 / rate of the base frame rate, their output repeated back and blended into the input."""

    def __init__(self, rate: int, layers: list[Layer], combine: float) -> None:
        super().__init__()
        self.rate = rate
        self.layers = nn.Sequential(*layers)
        self.combine = nn.Parameter(torch.full((1,), combine))

    def forward(self, x: Tensor) -> Tensor:
        y = upsample(self.layers(downsample(x, self.rate)), self.rate)
        return x + (y - x) * self.combine


class Front(nn.Module):
    """Features (batch, n_mel + n_pitch, hops) to (batch, width, hops / hop_stride): conv2d over (hops, bands) of the
    mel part, pitch averaged to the same rate, both projected."""

    def __init__(
        self, n_mel: int, n_pitch: int, channels: list[int], kernel: list[int], hop_strides: list[int],
        band_strides: list[int], width: int,
    ) -> None:  # fmt: skip
        super().__init__()
        self.n_mel = n_mel
        self.past_hops = kernel[0] - 1
        self.hop_stride = math.prod(hop_strides)
        band_pad = kernel[1] // 2
        convs, c_in, bands = [], 1, n_mel
        for c_out, hop_stride, band_stride in zip(channels, hop_strides, band_strides, strict=True):
            convs.append(nn.Conv2d(c_in, c_out, tuple(kernel), (hop_stride, band_stride), padding=(0, band_pad)))
            c_in, bands = c_out, (bands + 2 * band_pad - kernel[1]) // band_stride + 1
        self.convs = nn.ModuleList(convs)
        self.bands_out = bands
        self.proj = nn.Conv1d(c_in * bands + n_pitch, width, 1)

    def forward(self, x: Tensor) -> Tensor:
        mel = x[:, : self.n_mel].transpose(1, 2).unsqueeze(1)
        for conv in self.convs:
            mel = functional.silu(conv(functional.pad(mel, (0, 0, self.past_hops, 0))))
        mel = mel.permute(0, 1, 3, 2).reshape(mel.shape[0], mel.shape[1] * self.bands_out, -1)
        pitched = functional.avg_pool1d(x[:, self.n_mel :], self.hop_stride, self.hop_stride)
        return self.proj(torch.cat([mel, pitched], dim=1))


class CtcNet(nn.Module):
    """Features (batch, n_mel + n_pitch, hops) to CTC logits (batch, n_classes, hops / hop_stride)."""

    def __init__(self, front: Front, stacks: list[Stack], width: int, classes: int) -> None:
        super().__init__()
        self.front = front
        self.stacks = nn.Sequential(*stacks)
        self.head = nn.Conv1d(width, classes, 1)
        self.chunk_multiple = front.hop_stride * max(s.rate for s in stacks)

    def encode(self, x: Tensor) -> Tensor:
        """The frames every head reads, (batch, width, hops / hop_stride)."""
        return self.stacks(self.front(x))

    def forward(self, x: Tensor) -> Tensor:
        return self.head(self.encode(x))


def n_dims(cfg: dict) -> int:
    """Features a hop: the mel bands of the feature config, then pitch's."""
    return load_yaml(CONFIGS / cfg["features"])["features"]["n_bands"] + pitch.N_FEATURES


def layer(cfg: dict, kernel: int) -> Layer:
    """One encoder layer of the model section with the given depthwise kernel."""
    m = cfg["model"]
    return Layer(m["width"], m["ff_width"], kernel, m["mixer_frames"], m["norm_eps"], m["bypass_init"])


def build(cfg: dict) -> CtcNet:
    """The net of the model section of command_ctc.yaml; refuse a chunk the stacks cannot keep aligned."""
    m, f = cfg["model"], cfg["model"]["front"]
    n_mel = n_dims(cfg) - pitch.N_FEATURES
    front = Front(n_mel, pitch.N_FEATURES, f["channels"], f["kernel"], f["hop_strides"], f["band_strides"], m["width"])
    stacks = [
        Stack(s["rate"], [layer(cfg, s["kernel"]) for _ in range(s["layers"])], m["bypass_init"]) for s in m["stacks"]
    ]
    net = CtcNet(front, stacks, m["width"], n_classes())
    if cfg["chunk_hops"] % net.chunk_multiple:
        raise ValueError(f"chunk_hops {cfg['chunk_hops']} is not a multiple of {net.chunk_multiple}")
    return net
