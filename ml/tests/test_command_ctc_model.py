"""The ctc net keeps MultiNet7's encoder size and never reads a later chunk, the premise of streaming it by chunks."""

from __future__ import annotations

import copy

import pytest

torch = pytest.importorskip("torch")

from srpipe.core.config import load_yaml  # noqa: E402
from srpipe.tasks.command import ctc  # noqa: E402
from srpipe.tasks.command.ctc.model import encoder  # noqa: E402

MULTINET7_ENCODER_PARAMS = 1_896_000  # ADR-0013: 1.90 million, read from mn7_data


@pytest.fixture
def cfg():
    return load_yaml(ctc.CONFIG)


@pytest.fixture
def net(cfg):
    torch.manual_seed(1)
    return encoder.build(cfg).eval()


def trainable(module) -> int:
    return sum(p.numel() for p in module.parameters() if p.requires_grad)


def worst_prefix_gap(net, x, chunk_hops: int) -> float:
    """Largest change a chunk's outputs see when the input stops at the chunk's end."""
    with torch.no_grad():
        whole = net(x)
        gaps = []
        for end in range(chunk_hops, x.shape[-1], chunk_hops):
            frames = end // net.front.hop_stride
            gaps.append((net(x[..., :end]) - whole[..., :frames]).abs().max().item())
    return max(gaps)


def test_the_encoder_is_multinet7s_size_and_the_head_its_units(cfg, net) -> None:
    assert abs(trainable(net.stacks) - MULTINET7_ENCODER_PARAMS) / MULTINET7_ENCODER_PARAMS < 0.005
    x = torch.randn(1, encoder.n_dims(cfg), 4 * cfg["chunk_hops"])
    with torch.no_grad():
        y = net(x)
    assert y.shape == (1, encoder.n_classes(), x.shape[-1] // net.front.hop_stride)
    assert encoder.n_classes() == 45 and encoder.BLANK == 0


def test_a_chunk_never_reads_a_later_chunk(cfg, net) -> None:
    x = torch.randn(1, encoder.n_dims(cfg), 8 * cfg["chunk_hops"])
    assert worst_prefix_gap(net, x, cfg["chunk_hops"]) < 1e-4
    later = x.clone()
    later[..., 4 * cfg["chunk_hops"] :] += 5.0
    frames = 4 * cfg["chunk_hops"] // net.front.hop_stride
    with torch.no_grad():
        assert torch.equal(net(x)[..., :frames], net(later)[..., :frames])


def test_a_front_that_pads_the_future_fails_the_same_check(cfg, net) -> None:
    ahead = copy.deepcopy(net)
    for conv in ahead.front.convs:
        conv.padding = (ahead.front.past_hops // 2, conv.padding[1])
    ahead.front.past_hops = 0
    x = torch.randn(1, encoder.n_dims(cfg), 8 * cfg["chunk_hops"])
    assert worst_prefix_gap(ahead, x, cfg["chunk_hops"]) > 1e-3


def test_build_refuses_a_chunk_the_stacks_cannot_align(cfg) -> None:
    bad = copy.deepcopy(cfg)
    bad["chunk_hops"] = 12
    with pytest.raises(ValueError, match="multiple"):
        encoder.build(bad)
