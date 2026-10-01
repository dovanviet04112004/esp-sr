"""srpipe.tasks.ns.model: the designs' sizes, causality, one hop at a time with state equal to the whole sequence, and
257 gains in 0..1 from the slot's power for every candidate."""

from __future__ import annotations

import pytest

from srpipe.core.config import load_yaml
from srpipe.generated import grid
from srpipe.tasks import ns

torch = pytest.importorskip("torch")

from srpipe.tasks.ns import model  # noqa: E402

CFG = load_yaml(ns.CONFIG)
PARAMETERS = {"rnnoise16k": 82_603, "nsnet16k_s": 161_248, "nsnet16k_m": 264_064, "nsnet16k_l": 324_688}
NAMES = model.names(CFG)


def slot_power(batch: int, hops: int, seed: int = 0) -> torch.Tensor:
    g = torch.Generator().manual_seed(seed)
    return 10.0 ** (torch.rand(batch, hops, grid.N_BINS, generator=g) * 6.0 - 9.0)


def built(name: str) -> torch.nn.Module:
    torch.manual_seed(0)
    return model.build(CFG, name).eval()


def test_the_run_trains_every_candidate_of_the_design() -> None:
    assert list(PARAMETERS) == NAMES
    with pytest.raises(ValueError):
        model.build(CFG, "nsnet16k_xl")


@pytest.mark.parametrize("name", NAMES)
def test_parameter_counts_are_the_designs(name: str) -> None:
    assert sum(p.numel() for p in built(name).parameters()) == PARAMETERS[name]


@pytest.mark.parametrize("name", NAMES)
def test_no_output_reads_a_later_hop(name: str) -> None:
    net, x = built(name), slot_power(2, 40)
    later = x.clone()
    later[:, 25:] *= 100.0
    with torch.no_grad():
        (a, logit_a), (b, logit_b) = net(x), net(later)
    assert torch.equal(a[:, :25], b[:, :25]) and not torch.equal(a[:, 25:], b[:, 25:])
    if logit_a is not None:
        assert torch.equal(logit_a[:, :25], logit_b[:, :25])


@pytest.mark.parametrize("name", NAMES)
def test_stepping_one_hop_at_a_time_equals_the_whole_sequence(name: str) -> None:
    net = built(name)
    with torch.no_grad():
        x = net.normalised(slot_power(2, 30))
        whole, whole_logit, _ = net.net(x)
        state, hops, logits = None, [], []
        for t in range(x.shape[1]):
            g, logit, state = net.net(x[:, t : t + 1], state)
            hops.append(g)
            logits.append(logit)
    torch.testing.assert_close(torch.cat(hops, dim=1), whole, rtol=1e-5, atol=1e-6)
    if whole_logit is not None:
        torch.testing.assert_close(torch.cat(logits, dim=1), whole_logit, rtol=1e-5, atol=1e-5)


@pytest.mark.parametrize("name", NAMES)
def test_gains_stay_in_0_1_and_every_candidate_takes_the_slot_power(name: str) -> None:
    net, p = built(name), slot_power(3, 20)
    p[0] = 0.0
    with torch.no_grad():
        gains, logit = net(p)
    assert gains.shape == (3, 20, grid.N_BINS) and torch.isfinite(gains).all()
    assert gains.min() >= 0.0 and gains.max() <= 1.0 + 1e-6
    if name == model.RNNOISE:
        assert logit.shape == (3, 20) and torch.isfinite(logit).all()
    else:
        assert logit is None and torch.equal(gains[..., CFG["nsnet"]["bins"] :], gains[..., -2:-1])
