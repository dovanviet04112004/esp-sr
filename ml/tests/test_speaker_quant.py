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


class ChannelsFirstNorm(nn.Module):
    """redimnet2/layers/layernorm.py LayerNorm with data_format channels_first, at the pin."""

    def __init__(self, channels: int) -> None:
        super().__init__()
        self.weight = nn.Parameter(torch.rand(channels) + 0.5)
        self.bias = nn.Parameter(torch.rand(channels) - 0.5)
        self.eps = 1e-6
        self.data_format = "channels_first"

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        u = x.mean(1, keepdim=True)
        s = (x - u).pow(2).mean(1, keepdim=True)
        x = (x - u) / torch.sqrt(s + self.eps)
        w, b = self.weight, self.bias
        for _ in range(x.ndim - 2):
            w, b = w.unsqueeze(-1), b.unsqueeze(-1)
        return w * x + b


def test_channel_norms_give_what_the_channels_first_norm_gives_as_one_layer_norm(tmp_path: Path) -> None:
    torch.manual_seed(4)
    for shape in ((1, CHANNELS, FRAMES), (1, CHANNELS, 5, FRAMES)):
        norm = ChannelsFirstNorm(CHANNELS).eval()
        x = torch.randn(*shape)
        model = quant.channel_norms(nn.Sequential(norm)).eval()
        with torch.no_grad():
            torch.testing.assert_close(model(x), norm(x))
    ops = {n.op_type for n in onnx.load(onnx_export.checked(model, (x.numpy(),), tmp_path / "n.onnx", 1e-4)).graph.node}
    plain = onnx.load(onnx_export.checked(norm, (x.numpy(),), tmp_path / "f.onnx", 1e-4)).graph.node
    assert "LayerNormalization" in ops and "Pow" not in ops
    assert "Pow" in {n.op_type for n in plain}


class StackedSum(nn.Module):
    """redimnet2/layers/redim_structural.py weigth1d, not sequential, at the pin."""

    def __init__(self, inputs: int) -> None:
        super().__init__()
        self.w = nn.Parameter(torch.randn(1, inputs, CHANNELS, 1))
        self.sequential = False

    def forward(self, xs: list[torch.Tensor]) -> torch.Tensor:
        w = torch.softmax(self.w, dim=1)
        return (w * torch.cat([t.unsqueeze(1) for t in xs], dim=1)).sum(dim=1)


def test_stage_sums_give_what_the_stacked_sum_gives_with_no_reduce_sum_in_the_onnx(tmp_path: Path) -> None:
    torch.manual_seed(5)
    stacked = StackedSum(3).eval()
    xs = [torch.randn(1, CHANNELS, FRAMES) for _ in range(3)]
    summed = quant.StageSum(stacked).eval()
    with torch.no_grad():
        torch.testing.assert_close(summed(xs), stacked(xs))

    class Three(nn.Module):
        def __init__(self, inner: nn.Module) -> None:
            super().__init__()
            self.inner = inner

        def forward(self, a: torch.Tensor, b: torch.Tensor, c: torch.Tensor) -> torch.Tensor:
            return self.inner([a, b, c])

    def ops_of(model: nn.Module, name: str) -> set[str]:
        path = onnx_export.checked(Three(model), tuple(x.numpy() for x in xs), tmp_path / name, 1e-4)
        return {node.op_type for node in onnx.load(path).graph.node}

    assert "ReduceSum" in ops_of(stacked, "stacked.onnx")
    assert "ReduceSum" not in ops_of(summed, "summed.onnx")


def test_a_swapped_convolution_on_time_major_maps_gives_the_transposed_output() -> None:
    torch.manual_seed(6)
    conv = nn.Conv2d(3, 5, (3, 1), stride=(2, 1), padding=(1, 0), groups=1).eval()
    x = torch.randn(1, 3, 8, FRAMES)
    with torch.no_grad():
        torch.testing.assert_close(quant.swapped(conv)(x.transpose(2, 3)), conv(x).transpose(2, 3))


def test_time_major_to1d_and_to2d_give_the_bands_major_ones() -> None:
    x = torch.randn(1, 4, 6, FRAMES)
    flat = x.permute(0, 2, 1, 3).reshape(1, 6 * 4, FRAMES)
    torch.testing.assert_close(quant.To1d()(x.transpose(2, 3)), flat)
    torch.testing.assert_close(quant.To2d(6, 4)(flat), x.transpose(2, 3))


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


def test_ungrouped_gives_what_the_grouped_convolutions_give_and_keeps_depthwise_ones() -> None:
    torch.manual_seed(3)
    model = nn.Sequential(
        nn.Conv2d(3, 6, (2, 1), stride=(2, 1), groups=3), nn.Conv2d(6, 6, 3, padding=1, groups=6), nn.Conv1d(4, 4, 1)
    ).eval()
    grouped = nn.Sequential(model[0], model[1]).eval()
    x = torch.randn(1, 3, 8, FRAMES)
    with torch.no_grad():
        want = grouped(x)
        quant.ungrouped(model)
        got = nn.Sequential(model[0], model[1])(x)
    assert [m.groups for m in model] == [1, 6, 1]
    torch.testing.assert_close(got, want)


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
