"""The ctc net is MultiNet7's encoder frame, of its size at its widths, and never reads a later chunk, the premise of
streaming it by chunks; a lookahead starts as the identity, reads its frames ahead and none further, and streams as a
causal convolution that many frames late; its transducer is MultiNet7's predictor and joiner on our classes, and a
prefix never reads a later unit."""

from __future__ import annotations

import copy

import pytest

torch = pytest.importorskip("torch")

from srpipe.core.config import load_yaml  # noqa: E402
from srpipe.tasks.command import ctc  # noqa: E402
from srpipe.tasks.command.ctc.model import encoder  # noqa: E402
from srpipe.tasks.command.ctc.postproc import ctc_score  # noqa: E402

MULTINET7_ENCODER_PARAMS = 1_896_000  # ADR-0013: 1.90 million, read from mn7_data
MULTINET7_WIDTHS = {"width": 128, "ff_width": 256}


@pytest.fixture
def cfg():
    return load_yaml(ctc.CONFIG)


@pytest.fixture
def net(cfg):
    torch.manual_seed(1)
    return encoder.build(cfg).eval()


@pytest.fixture
def multinet7(cfg):
    """The configured net at MultiNet7's widths, where its sizes read from MultiNet7's weights hold."""
    sized = copy.deepcopy(cfg)
    sized["model"] |= MULTINET7_WIDTHS
    return encoder.build(sized)


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


def test_the_encoder_is_multinet7s_size_at_its_widths_and_the_head_its_units(cfg, net, multinet7) -> None:
    assert abs(trainable(multinet7.stacks) - MULTINET7_ENCODER_PARAMS) / MULTINET7_ENCODER_PARAMS < 0.005
    x = torch.randn(1, encoder.n_dims(cfg), 4 * cfg["chunk_hops"])
    with torch.no_grad():
        y = net(x)
    assert y.shape == (1, encoder.n_classes(), x.shape[-1] // net.front.hop_stride)
    assert encoder.n_classes() == 45 and ctc_score.BLANK == 0


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


MULTINET7_RNNT_PARAMS = 580_000  # ADR-0013: its predictor and joiner, over 496 classes


def test_the_transducer_is_multinet7s_predictor_and_joiner_on_our_classes(cfg, net, multinet7) -> None:
    t, width = net.transducer, cfg["rnnt"]["width"]
    assert t.predictor.embed.weight.shape == (encoder.n_classes() + 1, width) and t.predictor.mix.kernel_size == (2,)
    assert t.joiner.frame_proj.in_features == cfg["model"]["width"] and t.joiner.out.out_features == encoder.n_classes()
    multinet7_rows = (496 - encoder.n_classes()) * (2 * width + 1)  # embedding and output rows of its extra classes
    assert (
        abs(trainable(multinet7.transducer) - width + multinet7_rows - MULTINET7_RNNT_PARAMS) / MULTINET7_RNNT_PARAMS
        < 0.01
    )


def test_a_prefix_never_reads_a_later_unit_and_starts_as_greedy_decoding_does(net) -> None:
    predictor = net.transducer.predictor
    units = torch.tensor([[ctc_score.BLANK, 3, 7, 9]])
    later = units.clone()
    later[0, 3] = 20
    with torch.no_grad():
        assert torch.equal(predictor(units)[:, :3], predictor(later)[:, :3])
        first = predictor(torch.tensor([[ctc_score.BLANK]]))[:, 0]
        assert torch.equal(first, predictor(torch.tensor([[predictor.pad, ctc_score.BLANK]]))[:, -1])


def lookahead_cfg(cfg: dict, frames: int) -> dict:
    ahead = copy.deepcopy(cfg)
    ahead["model"]["lookahead_frames"] = frames
    return ahead


def test_a_net_given_a_lookahead_starts_as_the_net_without_one(cfg, net) -> None:
    ahead = encoder.build(lookahead_cfg(cfg, 12)).eval()
    fit = ahead.load_state_dict(net.state_dict(), strict=False)
    assert fit.missing_keys == ["lookahead.conv.weight"] and not fit.unexpected_keys
    x = torch.randn(1, encoder.n_dims(cfg), 8 * cfg["chunk_hops"])
    with torch.no_grad():
        assert torch.equal(ahead(x), net(x))


def test_the_lookahead_reads_its_frames_ahead_and_none_further() -> None:
    torch.manual_seed(2)
    ahead = encoder.Lookahead(4, 3)
    torch.nn.init.normal_(ahead.conv.weight)
    x = torch.randn(1, 4, 20)
    near, far = x.clone(), x.clone()
    near[..., 10 + 3] += 1.0
    far[..., 10 + 4] += 1.0
    with torch.no_grad():
        assert not torch.equal(ahead(near)[..., 10], ahead(x)[..., 10])
        assert torch.equal(ahead(far)[..., : 10 + 1], ahead(x)[..., : 10 + 1])


def test_a_causal_convolution_with_the_lookahead_weights_gives_its_frames_that_many_late() -> None:
    torch.manual_seed(3)
    ahead = encoder.Lookahead(4, 3)
    torch.nn.init.normal_(ahead.conv.weight)
    late = encoder.CausalConv(4, 4, 4, groups=4, bias=False)
    late.conv.weight.data.copy_(ahead.conv.weight.data)
    x = torch.randn(1, 4, 20)
    with torch.no_grad():
        torch.testing.assert_close(late(x)[..., 3:], ahead(x)[..., :-3])


def test_the_encoder_under_a_lookahead_still_never_reads_a_later_chunk(cfg) -> None:
    torch.manual_seed(1)
    ahead = encoder.build(lookahead_cfg(cfg, 12)).eval()
    torch.nn.init.normal_(ahead.lookahead.conv.weight)
    x = torch.randn(1, encoder.n_dims(cfg), 8 * cfg["chunk_hops"])
    later = x.clone()
    later[..., 4 * cfg["chunk_hops"] :] += 5.0
    frames = 4 * cfg["chunk_hops"] // ahead.front.hop_stride
    with torch.no_grad():
        assert torch.equal(ahead.encode(x)[..., :frames], ahead.encode(later)[..., :frames])
        assert not torch.equal(ahead(x)[..., :frames], ahead(later)[..., :frames])
