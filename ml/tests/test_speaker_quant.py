"""ReDimNet2 b0 rewritten for esp-dl computes what the extractor computes: explicit pads, the norm folded into the head,
the pooling's context added per channel with no Expand left in the ONNX; a pad 'same' cannot split evenly is refused."""

from __future__ import annotations

from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
onnx = pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")

from torch import nn  # noqa: E402

from srpipe.compress.quant import onnx_export  # noqa: E402
from srpipe.tasks.speaker import quant  # noqa: E402

CHANNELS, BOTTLENECK, FRAMES = 6, 4, 19


class ContextAstp(nn.Module):
    """redimnet2/layers/poolings.py ASTP with global_context_att, at the pin: the context expanded over time."""

    def __init__(self) -> None:
        super().__init__()
        self.in_dim = CHANNELS
        self.global_context_att = True
        self.linear1 = nn.Conv1d(3 * CHANNELS, BOTTLENECK, kernel_size=1)
        self.linear2 = nn.Conv1d(BOTTLENECK, CHANNELS, kernel_size=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        context_mean = torch.mean(x, dim=-1, keepdim=True).expand_as(x)
        context_std = torch.sqrt(torch.var(x, dim=-1, keepdim=True) + 1e-7).expand_as(x)
        alpha = torch.tanh(self.linear1(torch.cat((x, context_mean, context_std), dim=1)))
        alpha = torch.softmax(self.linear2(alpha), dim=2)
        mean = torch.sum(alpha * x, dim=2)
        var = torch.sum(alpha * (x**2), dim=2) - mean**2
        return torch.cat([mean, torch.sqrt(var.clamp(min=1e-7))], dim=1)


def test_explicit_pads_give_what_same_gives() -> None:
    torch.manual_seed(0)
    model = nn.Sequential(
        nn.Conv2d(1, 3, (3, 5), padding="same"), nn.Conv2d(3, 2, 3, padding="same", dilation=(1, 2))
    ).eval()
    x = torch.randn(1, 1, 12, FRAMES)
    with torch.no_grad():
        want = model(x)
        got = quant.explicit_padding(model)(x)
    assert [m.padding for m in model] == [(1, 2), (1, 2)]
    torch.testing.assert_close(got, want, rtol=0.0, atol=0.0)


def test_explicit_pads_refuse_a_same_pad_that_cannot_split_evenly() -> None:
    with pytest.raises(ValueError, match="uneven"):
        quant.explicit_padding(nn.Conv1d(2, 2, 4, padding="same"))


def test_the_folded_head_gives_the_norm_then_the_linear() -> None:
    torch.manual_seed(1)
    bn, linear = nn.BatchNorm1d(2 * CHANNELS), nn.Linear(2 * CHANNELS, 5)
    bn.running_mean.uniform_(-1.0, 1.0)
    bn.running_var.uniform_(0.5, 2.0)
    nn.init.uniform_(bn.weight, 0.5, 1.5)
    nn.init.uniform_(bn.bias, -0.5, 0.5)
    bn.eval()
    x = torch.randn(3, 2 * CHANNELS)
    with torch.no_grad():
        torch.testing.assert_close(quant.folded_head(bn, linear)(x), linear(bn(x)))


def test_the_context_pool_gives_what_astp_gives_with_no_expand_in_its_onnx(tmp_path: Path) -> None:
    torch.manual_seed(2)
    astp = ContextAstp().eval()
    pool = quant.ContextPool(astp).eval()
    x = torch.randn(1, CHANNELS, FRAMES)
    with torch.no_grad():
        torch.testing.assert_close(pool(x), astp(x))

    def ops_of(model: nn.Module, name: str) -> set[str]:
        path = onnx_export.checked(model, (x.numpy(),), tmp_path / name, 1e-4)
        return {node.op_type for node in onnx.load(path).graph.node}

    assert "Expand" in ops_of(astp, "astp.onnx")
    assert "Expand" not in ops_of(pool, "pool.onnx")
