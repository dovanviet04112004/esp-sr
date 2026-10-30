"""esp_ppq_patches.simulate: each norm and softmax simulated as the S3 build of esp-dl rounds it."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from esp_ppq.executor.torch import TorchExecutor  # noqa: E402
from torch import nn  # noqa: E402

from esp_ppq_cases import LN_HOPS, LN_WIDTH, RUNGS, fused, layernorm_graph  # noqa: E402
from srpipe.compress.quant import esp_ppq_patches, ptq_espdl  # noqa: E402

NORM_HOPS, NORM_WIDTH, NORM_EPS = 4096, 256, 1e-5


class ChannelNorm(nn.Module):
    """A norm over the last axis with a float scale a channel, in the form ESP-PPQ fuses into RMSNormalization."""

    def __init__(self) -> None:
        super().__init__()
        self.scale = nn.Parameter(torch.linspace(0.5, 1.5, NORM_WIDTH))

    def forward(self, x):
        return x / torch.sqrt(x.pow(2).mean(dim=-1, keepdim=True) + NORM_EPS) * self.scale


def norm_graph(tmp_path: Path):
    rng = np.random.default_rng(8)
    calib = [torch.from_numpy(rng.normal(0, 2, (1, NORM_HOPS, NORM_WIDTH)).astype(np.float32)) for _ in range(2)]
    with esp_ppq_patches.applied(["rmsnorm_as_espdl"]):
        graph = ptq_espdl.quantize(ChannelNorm(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    (op,) = [op for op in graph.operations.values() if op.type == "RMSNormalization"]
    return graph, op


def espdl_rmsnorm(q: np.ndarray, scale: np.ndarray, e_in: int, e_out: int) -> np.ndarray:
    """esp-dl's S3 int8 RMSNormalization of q over its last axis as the S3 build runs it (-ffast-math): the row's
    scale as espdl_rms has it, then each value as dl_tie728_rmsnorm_s8 takes it."""
    s_in, s_out = np.float32(2.0**e_in), np.float32(2.0**e_out)
    sum_sq = (q.astype(np.int64) ** 2).sum(-1, keepdims=True).astype(np.float32)
    rms = espdl_rms(sum_sq, q.shape[-1], s_in, s_out)
    return np.clip(np.floor(q.astype(np.float32) * rms * scale + np.float32(0.5)), -128, 127)


def espdl_rms(sum_sq: np.ndarray, n: int, s_in: np.float32, s_out: np.float32) -> np.ndarray:
    """A row's scale as the S3 build of dl_module_rms_normalization.hpp computes it: madd.s of the sum of squares,
    (1.0f / n) * s_in^2 and eps, then 1 / sqrtf times the steps' ratio."""
    f = np.float32
    per_value = (f(1) / f(n)) * (s_in * s_in)
    fused_sum = fused(sum_sq, np.full_like(sum_sq, per_value), np.full_like(sum_sq, f(NORM_EPS)))
    return f(1) / np.sqrt(fused_sum) * (s_in / s_out)


def test_rms_like_espdl_fuses_the_mean_square_with_eps_as_madd_s_does() -> None:
    """Over 160 values, ctc's width, 1/160 is not a float32: the fused scale is not the one of a division and a sum."""
    rng = np.random.default_rng(3)
    sum_sq = rng.integers(0, 160 * 128 * 128, (20000, 1)).astype(np.float32)
    s_in, s_out = np.float32(2.0**-5), np.float32(2.0**-4)
    got = esp_ppq_patches.rms_like_espdl(
        torch.from_numpy(sum_sq), 160, torch.tensor(s_in), torch.tensor(s_out), NORM_EPS
    ).numpy()
    assert np.array_equal(got, espdl_rms(sum_sq, 160, s_in, s_out))
    divided = np.float32(1) / np.sqrt(sum_sq * (s_in * s_in) / np.float32(160) + np.float32(NORM_EPS)) * (s_in / s_out)
    assert not np.array_equal(got, divided)


def test_rmsnorm_as_espdl_simulates_each_norm_as_the_chip_rounds_it(tmp_path: Path) -> None:
    """ESP-PPQ as is rounds about one output in a few million a step off the chip; four batches of this seed hold
    two such."""
    graph, op = norm_graph(tmp_path)
    e_in, e_out = (ptq_espdl.exponent_of(c) for c in (op.input_quant_config[0], op.output_quant_config[0]))
    scale = op.inputs[1].value.detach().numpy().astype(np.float32)
    patched, as_is = ptq_espdl.Simulator(graph, ["rmsnorm_as_espdl"]), ptq_espdl.Simulator(graph)
    rng, step, flips = np.random.default_rng(11), np.float32(2.0**e_out), 0
    for _ in range(4):
        q = rng.normal(0, 40, (1, NORM_HOPS, NORM_WIDTH)).round().clip(-128, 127)
        x, want = q.astype(np.float32) * np.float32(2.0**e_in), espdl_rmsnorm(q, scale, e_in, e_out)
        assert np.array_equal(patched(x) / step, want)
        off = as_is(x) / step - want
        assert np.abs(off).max() <= 1
        flips += np.count_nonzero(off)
    assert flips > 0


def espdl_layernorm(q: np.ndarray, gamma: np.ndarray, beta: np.ndarray, s_in, s_out, eps) -> np.ndarray:
    """esp-dl's int8 LayerNormalization of q over its last axis as the S3 build runs it (-ffast-math -O3): the mean
    through a double reciprocal, squares summed by madd.s, 1.0f / n times the squared input step fused with eps,
    sqrt_newton as dl_math.hpp has it, then gamma times the inverse deviation times the centred value, plus beta."""
    f, n = np.float32, q.shape[-1]
    mean = (q.astype(np.float64).sum(-1, keepdims=True) * (1.0 / n)).astype(f)
    squares = np.zeros_like(mean)
    for j in range(n):
        squares = fused(q[..., j : j + 1].astype(f) - mean, q[..., j : j + 1].astype(f) - mean, squares)
    x = fused(np.full_like(squares, (f(1.0) / f(n)) * (f(s_in) * f(s_in))), squares, np.full_like(squares, eps))
    root, moving = x.copy(), np.ones(x.shape, dtype=bool)
    while moving.any():
        step = (root + x / root) * f(0.5)
        root, moving = np.where(moving, step, root), moving & (np.abs(step - root) > f(1e-5))
    result = gamma * ((f(1.0) / root) * f(s_in)) * (q.astype(f) - mean) + beta
    return np.clip(np.floor(result * (f(1.0) / f(s_out)) + f(0.5)), -128, 127)


def test_fma32_rounds_a_product_and_its_sum_once() -> None:
    """(1 + 2^-12)^2 - 1 keeps the product's 2^-24 that a rounded product drops; 1 + 2^-23 - 2^-24 + 2^-70 rounds up,
    though its float64 sum sits on the float32 half step between 1 and 1 + 2^-23 and would round to even."""
    f = np.float32
    cases = [
        ((f(1 + 2**-12), f(1 + 2**-12), f(-1)), f(2**-11 + 2**-24), f(f(1 + 2**-12) * f(1 + 2**-12)) + f(-1)),
        ((f(2**-12 * (1 + 2**-23)), f(-(2**-12) * (1 - 2**-23)), f(1 + 2**-23)), f(1 + 2**-23), f(1)),
    ]
    for (a, b, c), want, unfused in cases:
        args = [np.array([v]) for v in (a, b, c)]
        assert fused(*args)[0] == want != unfused
        assert esp_ppq_patches.fma32(*(torch.from_numpy(v) for v in args))[0].item() == want


def test_layernorm_as_espdl_simulates_each_norm_as_the_chip_rounds_it(tmp_path: Path) -> None:
    """ESP-PPQ as is rounds a half step now and then the other way from the chip; this seed holds such outputs."""
    graph, op = layernorm_graph(tmp_path)
    s_in, s_out = (float(c.scale) for c in (op.input_quant_config[0], op.output_quant_config[0]))
    gamma, beta = (op.inputs[i].value.detach().numpy().astype(np.float32) for i in (1, 2))
    gamma = np.clip(np.floor(gamma / float(op.input_quant_config[1].scale) + 0.5), -128, 127)
    gamma = (gamma * float(op.input_quant_config[1].scale)).astype(np.float32)
    beta = np.clip(np.floor(beta / float(op.input_quant_config[2].scale) + 0.5), -128, 127)
    beta = (beta * float(op.input_quant_config[2].scale)).astype(np.float32)
    patched, as_is = ptq_espdl.Simulator(graph, ["layernorm_as_espdl"]), ptq_espdl.Simulator(graph)
    rng, flips = np.random.default_rng(12), 0
    for _ in range(4):
        q = rng.normal(0, 30, (1, LN_HOPS, LN_WIDTH)).round().clip(-128, 127)
        x = q.astype(np.float32) * np.float32(s_in)
        want = espdl_layernorm(q, gamma, beta, s_in, s_out, op.attributes.get("epsilon", 1e-5))
        assert np.array_equal(patched(x) / np.float32(s_out), want)
        off = as_is(x) / np.float32(s_out) - want
        assert np.abs(off).max() <= 1
        flips += np.count_nonzero(off)
    assert flips > 0


SOFTMAX_ROWS, SOFTMAX_WIDTH = 512, 148


def espdl_softmax(q: np.ndarray, s_in: float) -> np.ndarray:
    """esp-dl's int8 Softmax of q over its last axis as the S3 build runs forward_lut (-ffast-math): exp from the S3's
    table of the input's grid, the row summed one value after another in float32, each value times the sum's
    reciprocal."""
    table = esp_ppq_patches.exp_table_s3(s_in)
    e = table[q.astype(np.int64) + 128]
    total = np.zeros((*e.shape[:-1], 1), np.float32)
    for i in range(e.shape[-1]):
        total = total + e[..., i : i + 1]
    return e * (np.float32(1) / total)


def test_expf_s3_keeps_the_s3s_own_roundings_within_an_ulp_of_exp() -> None:
    """picolibc's float32 expf, which board B's Softmax tables are built with, rounds these grid points the other way
    from the correctly rounded exp (KEHOACH 3.17), and stays within an ulp of it everywhere else."""
    pinned = {-6.375: 0x3ADF4BFE, -5.875: 0x3B3813C0, -5.75: 0x3B509634, -5.1875: 0x3BB70A62}
    for x, word in pinned.items():
        got = esp_ppq_patches.word_of(esp_ppq_patches.expf_s3(x))
        assert got == word and got != esp_ppq_patches.word_of(np.float32(math.exp(x)))
    xs = np.random.default_rng(3).uniform(-100.0, 88.0, 4000).astype(np.float32)
    exact = np.exp(xs.astype(np.float64)).astype(np.float32)
    got = np.array([esp_ppq_patches.expf_s3(x) for x in xs], dtype=np.float32)
    assert np.abs(got.view(np.int32).astype(np.int64) - exact.view(np.int32).astype(np.int64)).max() <= 1
    assert esp_ppq_patches.expf_s3(np.inf) == np.inf and esp_ppq_patches.expf_s3(-np.inf) == 0


def test_softmax_as_espdl_simulates_each_row_as_the_chip_sums_it(tmp_path: Path) -> None:
    """ESP-PPQ as is takes the row's maximum out before exp and sums otherwise, so its float probabilities are not the
    chip's; quantised at the next op's grid they stay within a step, apart only on a half step."""
    net = nn.Sequential(nn.Linear(SOFTMAX_WIDTH, SOFTMAX_WIDTH), nn.Softmax(dim=-1), nn.Linear(SOFTMAX_WIDTH, 8))
    rng = np.random.default_rng(13)
    calib = [torch.from_numpy(rng.normal(0, 2, (1, SOFTMAX_ROWS, SOFTMAX_WIDTH)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(net.eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    (op,) = [op for op in graph.operations.values() if op.type == "Softmax"]
    (reader,) = op.outputs[0].dest_ops
    s_in, s_next = float(op.input_quant_config[0].scale), np.float32(reader.input_quant_config[0].scale)
    executor, name = TorchExecutor(graph, device="cpu"), op.outputs[0].name
    for _ in range(4):
        x = torch.from_numpy(rng.normal(0, 2, (1, SOFTMAX_ROWS, SOFTMAX_WIDTH)).astype(np.float32))
        q = np.round(executor.forward(inputs=[x], output_names=[op.inputs[0].name])[0].numpy() / s_in)
        want = espdl_softmax(q, s_in)
        with esp_ppq_patches.applied(["softmax_as_espdl"]):
            patched = executor.forward(inputs=[x], output_names=[name])[0].numpy()
        as_is = executor.forward(inputs=[x], output_names=[name])[0].numpy()
        assert np.array_equal(patched, want)
        assert not np.array_equal(as_is, want)
        steps = [np.floor(p * (np.float32(1) / s_next) + np.float32(0.5)) for p in (as_is, want)]
        assert np.abs(steps[0] - steps[1]).max() <= 1


def test_rmsnorm_as_espdl_passes_the_gradient_of_the_float_norm(tmp_path: Path) -> None:
    from esp_ppq.executor.op.torch.default import RMSNormalization_forward

    _, op = norm_graph(tmp_path)
    e_in = ptq_espdl.exponent_of(op.input_quant_config[0])
    q = np.random.default_rng(10).normal(0, 40, (1, 8, NORM_WIDTH)).round().clip(-128, 127)
    x = torch.from_numpy(q.astype(np.float32) * np.float32(2.0**e_in))
    grads, outs = [], []
    patched = {"simulate": RMSNormalization_forward, "platforms": frozenset({op.platform})}
    elsewhere = patched | {"platforms": frozenset()}
    for kwargs in ({}, patched, elsewhere):
        forward = esp_ppq_patches.rmsnorm_like_espdl if kwargs else RMSNormalization_forward
        xs, scale = x.clone().requires_grad_(), op.inputs[1].value.detach().clone().requires_grad_()
        outs.append(forward(op, [xs, scale], **kwargs))
        (outs[-1] * torch.linspace(-1, 1, NORM_WIDTH)).sum().backward()
        grads.append((xs.grad, scale.grad))
    assert all(torch.equal(a, b) and torch.equal(a, c) for a, b, c in zip(*grads, strict=True))
    assert torch.equal(outs[2], outs[0]) and not torch.equal(outs[1], outs[0])


CONV16_CHANNELS, CONV16_SIDE = 64, 32


class WideConv(nn.Module):
    """A 3 x 3 convolution over CONV16_CHANNELS channels: at 16 bits its sums pass float32's 24."""

    def __init__(self) -> None:
        super().__init__()
        self.conv = nn.Conv2d(CONV16_CHANNELS, CONV16_CHANNELS, 3, padding=1)

    def forward(self, x):
        return self.conv(x)


def wide_conv_graph(tmp_path: Path):
    rng = np.random.default_rng(21)
    shape = (1, CONV16_CHANNELS, CONV16_SIDE, CONV16_SIDE)
    calib = [torch.from_numpy(rng.normal(0, 2, shape).astype(np.float32)) for _ in range(2)]
    rungs = RUNGS | {"calibration": "minmax", "int16_ops": ["/conv/Conv"]}
    graph = ptq_espdl.quantize(WideConv().eval(), calib, tmp_path, rungs)
    (op,) = [op for op in graph.operations.values() if op.type == "Conv"]
    return graph, op, rng


def on_grid(value: torch.Tensor, config) -> np.ndarray:
    """value's integers on config's grid, rounded half up as the S3's quantisers round."""
    step = float(config.scale)
    q = np.floor(value.detach().double().numpy() / step + 0.5)
    return np.clip(q, config.quant_min, config.quant_max).astype(np.int64)


def espdl_conv_s16(q: np.ndarray, w: np.ndarray, b: np.ndarray, shift: int) -> np.ndarray:
    """esp-dl's S3 int16 3 x 3 convolution, padded by one: QACC from the bias, each product added exactly, then rounded
    half up and saturated as tie728_s16_vector_round_result takes QACC to int16."""
    side = q.shape[-1]
    padded = np.pad(q[0], ((0, 0), (1, 1), (1, 1)))
    acc = np.repeat(np.repeat(b[:, None, None], side, 1), side, 2)
    for dy in range(3):
        for dx in range(3):
            acc = acc + np.einsum("oc,chw->ohw", w[:, :, dy, dx], padded[:, dy : dy + side, dx : dx + side])
    acc = ((acc >> (shift - 1)) + 1) >> 1
    return np.clip(acc, -32768, 32767)[None]


def test_conv_s16_as_espdl_sums_each_int16_convolution_as_the_chip_does(tmp_path: Path) -> None:
    """ESP-PPQ as is sums an int16 convolution in float32, so near a half step of the output's grid it rounds apart from
    the chip's exact sums, a step at most; patched, every value is the chip's."""
    from esp_ppq.api.espdl_interface import get_target_platform

    graph, op, rng = wide_conv_graph(tmp_path)
    assert op.platform == get_target_platform(ptq_espdl.TARGET, ptq_espdl.WIDE_BITS)
    given, weight, bias = op.input_quant_config[:3]
    out = op.output_quant_config[0]
    shift = ptq_espdl.exponent_of(out) - ptq_espdl.exponent_of(given) - ptq_espdl.exponent_of(weight)
    assert float(bias.scale) == float(given.scale) * float(weight.scale) and shift > 0
    w, b = on_grid(op.inputs[1].value, weight), on_grid(op.inputs[2].value, bias)
    executor, name, apart = TorchExecutor(graph, device="cpu"), op.outputs[0].name, 0
    for _ in range(4):
        x = torch.from_numpy(rng.normal(0, 2, (1, CONV16_CHANNELS, CONV16_SIDE, CONV16_SIDE)).astype(np.float32))
        want = espdl_conv_s16(on_grid(x, given), w, b, shift)
        with esp_ppq_patches.applied(["conv_s16_as_espdl"]):
            patched = on_grid(executor.forward(inputs=[x], output_names=[name])[0], out)
        as_is = on_grid(executor.forward(inputs=[x], output_names=[name])[0], out)
        assert np.array_equal(patched, want)
        assert np.abs(as_is - want).max() <= 1
        apart += int((as_is != want).sum())
    assert apart > 0


def test_conv_s16_as_espdl_passes_the_gradient_of_the_float_convolution(tmp_path: Path) -> None:
    from esp_ppq.executor.op.torch.default import Conv_forward

    _, op, rng = wide_conv_graph(tmp_path)
    given = op.input_quant_config[0]
    q = rng.normal(0, 4000, (1, CONV16_CHANNELS, 8, 8)).round().clip(-32768, 32767)
    x = torch.from_numpy(q.astype(np.float32) * np.float32(given.scale))
    w, b = (v.detach().clone() for v in (op.inputs[1].value, op.inputs[2].value))
    grads, outs = [], []
    patched = {"simulate": Conv_forward, "platforms": frozenset({op.platform})}
    for kwargs in ({}, patched):
        forward = esp_ppq_patches.conv_s16_like_espdl if kwargs else Conv_forward
        xs = x.clone().requires_grad_()
        outs.append(forward(op, [xs, w, b], **kwargs))
        (outs[-1] * torch.linspace(-1, 1, 8)).sum().backward()
        grads.append(xs.grad)
    assert torch.equal(grads[0], grads[1])
    step = float(op.output_quant_config[0].scale)
    assert torch.equal(outs[1], torch.round(outs[1] / step) * step) and not torch.equal(outs[1], outs[0])
