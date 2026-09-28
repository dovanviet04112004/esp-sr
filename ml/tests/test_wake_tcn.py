"""The wake TCN is causal with the receptive field its dilations give, the premise of streaming it hop by hop."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from srpipe.core.config import load_yaml  # noqa: E402
from srpipe.tasks.wake import quant  # noqa: E402


@pytest.fixture
def net():
    torch.manual_seed(1)
    return quant.build(load_yaml(quant.CONFIG)).eval()


def test_no_output_reads_a_later_hop(net) -> None:
    x = torch.randn(1, net.inp.in_channels, 300)
    later = x.clone()
    later[..., 200:] += 5.0
    with torch.no_grad():
        assert torch.equal(net(x)[..., :200], net(later)[..., :200])
        assert not torch.equal(net(x)[..., 200:], net(later)[..., 200:])


def test_an_output_reads_exactly_its_receptive_field(net) -> None:
    rf = net.receptive_field_hops
    assert rf == 127
    x = torch.randn(1, net.inp.in_channels, 300)
    t = 299
    inside, outside = x.clone(), x.clone()
    inside[..., t - rf + 1] += 5.0
    outside[..., t - rf] += 5.0
    with torch.no_grad():
        assert net(inside)[..., t] != net(x)[..., t]
        assert torch.equal(net(outside)[..., t], net(x)[..., t])
