"""The int8 graphs of the rnnt track (KEHOACH 3.12, 3.14, ADR-0016): the encoder with the joiner's frame projection,
the predictor reading its context one-hot, and the joiner of the two projections, each as esp-dl runs it.
"""

from __future__ import annotations

import numpy as np
import torch
from torch import Tensor, nn

from srpipe.tasks.command.ctc.model import encoder
from srpipe.tasks.command.rnnt.model import transducer


def pointwise(linear: nn.Linear) -> nn.Conv1d:
    """linear as the 1x1 convolution over (batch, features, frames) esp-dl runs."""
    conv = nn.Conv1d(linear.in_features, linear.out_features, 1, bias=linear.bias is not None)
    with torch.no_grad():
        conv.weight.copy_(linear.weight[:, :, None])
        if linear.bias is not None:
            conv.bias.copy_(linear.bias)
    return conv


class FramesGraph(nn.Module):
    """Features (1, dims, hops) to the joiner's projected frames (1, width, frames): the encoder, then the frame
    projection, so each frame is projected once."""

    def __init__(self, net: encoder.CtcNet) -> None:
        super().__init__()
        self.front, self.stacks = net.front, net.stacks
        self.project = pointwise(net.transducer.joiner.frame_proj)

    def forward(self, x: Tensor) -> Tensor:
        return self.project(self.stacks(self.front(x)))


class PredictorGraph(nn.Module):
    """A context of classes one-hot (1, classes + 1, context) to the joiner's projected prefix (1, width, 1): the
    embedding as a 1x1 convolution, the depthwise mix over the whole context, ReLU, the prefix projection."""

    def __init__(self, t: transducer.Transducer) -> None:
        super().__init__()
        p = t.predictor
        self.embed = nn.Conv1d(p.embed.num_embeddings, p.embed.embedding_dim, 1, bias=False)
        with torch.no_grad():
            self.embed.weight.copy_(p.embed.weight.T[:, :, None])
        self.mix, self.project = p.mix, pointwise(t.joiner.prefix_proj)

    def forward(self, context: Tensor) -> Tensor:
        return self.project(torch.relu(self.mix(self.embed(context))))


class JoinerGraph(nn.Module):
    """A projected frame and a projected prefix, each (1, width, 1), to the logits of the classes (1, classes, 1)."""

    def __init__(self, t: transducer.Transducer) -> None:
        super().__init__()
        self.out = pointwise(t.joiner.out)

    def forward(self, frame: Tensor, prefix: Tensor) -> Tensor:
        return self.out(torch.tanh(frame + prefix))


def one_hot(context: tuple[int, ...], classes: int) -> np.ndarray:
    """A predictor context as PredictorGraph reads it: (1, classes + 1, len(context)), the pad id its last row."""
    x = np.zeros((1, classes + 1, len(context)), dtype=np.float32)
    x[0, list(context), np.arange(len(context))] = 1.0
    return x
