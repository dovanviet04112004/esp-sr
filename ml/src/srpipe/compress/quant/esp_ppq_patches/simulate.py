"""Float32 mirrors of esp-dl's S3 kernels for ESP-PPQ's simulator: RMSNorm, LayerNorm and Softmax rounded where the
chip rounds."""

from __future__ import annotations

import contextlib
import functools
import math
from collections.abc import Callable, Iterator

import numpy as np

SQRT_NEWTON_STOP = 1e-5  # dl_math.hpp EN, esp-dl 3.3.11


def rms_like_espdl(sum_sq, n: int, s_in, s_out, eps: float):
    """1 / sqrt(mean square + eps) times the input step over the output step, from a row's integer sum of squares over
    n values, as the S3 build of esp-dl's RMSNormalization takes it under -ffast-math: the mean square through
    (1.0f / n) * s_in^2 and fused with eps by madd.s, then sqrtf and a float32 division."""
    import torch

    # Tensor by tensor: torch divides a CUDA tensor by a Python number through its reciprocal.
    per_value = torch.ones_like(s_in) / torch.full_like(s_in, n) * (s_in * s_in)
    root = torch.sqrt(fma32(sum_sq, per_value, torch.full_like(sum_sq, eps)))
    return torch.ones_like(sum_sq) / root * (s_in / s_out)


def rmsnorm_like_espdl(op, values: list, ctx=None, *, simulate: Callable, platforms: frozenset, **kwargs):
    """op's output as esp-dl's S3 kernel computes it once op, on one of platforms, has its input and output quantised,
    each float32 step in the kernel's order; simulate's float output otherwise. The gradient is simulate's."""
    import torch
    from esp_ppq.core import QuantizationStates
    from esp_ppq.IR.quantize import QuantableOperation

    floating = simulate(op, values, ctx, **kwargs)
    if not isinstance(op, QuantableOperation) or op.platform not in platforms:
        return floating
    given, out = op.input_quant_config[0], op.output_quant_config[0]
    if not all(QuantizationStates.is_activated(c.dominated_by.state) for c in (given, out)):
        return floating
    with torch.no_grad():
        x, weight = values[0].to(floating.device), values[1].to(floating.device)
        s_in, s_out = given.scale.to(floating.device), out.scale.to(floating.device)
        axes = list(range(x.ndim - weight.ndim, x.ndim))
        q = x / s_in
        sum_sq = (q.double() ** 2).sum(axes, keepdim=True).float()
        n = math.prod(x.shape[a] for a in axes)
        rms = rms_like_espdl(sum_sq, n, s_in, s_out, op.attributes.get("epsilon", 1e-5))
        y = torch.clamp(torch.floor(q * rms * weight + 0.5), out.quant_min, out.quant_max) * s_out
    return floating - floating.detach() + y


@contextlib.contextmanager
def rmsnorm_as_espdl() -> Iterator[None]:
    """ESP-PPQ simulates RMSNormalization as x / sqrt(mean(x^2) + eps) * scale rounded at its output; esp-dl's S3 kernel
    multiplies the integers by 1 / sqrtf of the mean square, already scaled between the grids, then by the float scale,
    and rounds half up, its mean square fused with eps under -ffast-math. Board B, command_ctc qat: 2 of 198 Gate 3
    windows scored a step off the simulation.
    """
    from esp_ppq.api.espdl_interface import get_target_platform
    from esp_ppq.executor.base import OPERATION_FORWARD_TABLE

    from srpipe.compress.quant.ptq_espdl import BITS, TARGET, WIDE_BITS

    platforms = frozenset(get_target_platform(TARGET, bits) for bits in (BITS, WIDE_BITS))
    # Every esp-dl platform shares one table, so the patched forward checks the platform itself.
    tables = list({id(t): t for t in (OPERATION_FORWARD_TABLE[p] for p in platforms)}.values())
    simulated = [table["RMSNormalization"] for table in tables]
    for table, simulate in zip(tables, simulated, strict=True):
        table["RMSNormalization"] = functools.partial(rmsnorm_like_espdl, simulate=simulate, platforms=platforms)
    try:
        yield
    finally:
        for table, simulate in zip(tables, simulated, strict=True):
            table["RMSNormalization"] = simulate


def sqrt_newton(x):
    """esp-dl's dl::math::sqrt_newton on a float32 tensor: Newton steps from x until one moves by at most 1e-5."""
    root, moving = x.clone(), x != 0
    root[~moving] = 0
    while moving.any():
        step = (root[moving] + x[moving] / root[moving]) * 0.5
        still = (step - root[moving]).abs() > SQRT_NEWTON_STOP
        root[moving] = step
        moving[moving.clone()] = still
    return root


def fma32(a, b, c):
    """a * b + c on float32 tensors rounded once to float32, as the S3's madd.s gives it: the exact product summed in
    float64 and rounded to odd there, which the one rounding to float32 then leaves as the fused result."""
    import torch

    product, addend = a.double() * b.double(), c.double()
    total = product + addend
    back = total - product
    error = (product - (total - back)) + (addend - back)
    toward = torch.where(error > 0, torch.full_like(total, math.inf), torch.full_like(total, -math.inf))
    even = (total.view(torch.int64) & 1) == 0
    return torch.where((error != 0) & even, torch.nextafter(total, toward), total).float()


def layernorm_like_espdl(op, values: list, ctx=None, *, simulate: Callable, platforms: frozenset, **kwargs):
    """op's output as esp-dl's LayerNormalization computes it once op, on one of platforms, has its input and output
    quantised: each float32 step as GCC builds forward_template for the S3 under esp-dl's -ffast-math -O3, which
    reorders the source's steps and fuses its multiply-adds into madd.s; simulate's output otherwise."""
    import torch
    from esp_ppq.core import QuantizationStates
    from esp_ppq.IR.quantize import QuantableOperation

    floating = simulate(op, values, ctx, **kwargs)
    if not isinstance(op, QuantableOperation) or op.platform not in platforms:
        return floating
    given, out = op.input_quant_config[0], op.output_quant_config[0]
    if not all(QuantizationStates.is_activated(c.dominated_by.state) for c in (given, out)):
        return floating
    with torch.no_grad():
        x, gamma = values[0].to(floating.device), values[1].to(floating.device)
        s_in, s_out = given.scale.to(floating.device).float(), out.scale.to(floating.device).float()
        q = torch.round(x / s_in)
        n = q.shape[-1]
        mean = (q.double().sum(-1, keepdim=True) * (1.0 / n)).float()
        squares = torch.zeros_like(mean)
        for j in range(n):
            squares = fma32(q[..., j : j + 1] - mean, q[..., j : j + 1] - mean, squares)
        per_value = torch.ones_like(s_in) / torch.full_like(s_in, n) * (s_in * s_in)
        eps = torch.full_like(squares, op.attributes.get("epsilon", 1e-5))
        inv_std = torch.ones_like(squares) / sqrt_newton(fma32(per_value, squares, eps))
        result = gamma * (inv_std * s_in) * (q - mean)
        if len(values) > 2:
            result = result + values[2].to(floating.device)
        y = torch.clamp(torch.floor(result * (torch.ones_like(s_out) / s_out) + 0.5), out.quant_min, out.quant_max)
    return floating - floating.detach() + y * s_out


@contextlib.contextmanager
def layernorm_as_espdl() -> Iterator[None]:
    """ESP-PPQ simulates LayerNormalization with torch's layer_norm rounded at its output; esp-dl's kernel, built with
    -ffast-math, takes the mean through a reciprocal, sums the squares with fused multiply-adds and scales by gamma
    before the centred value, so an output on a half step can round the other way. ReDimNet2 b0 (KEHOACH 3.17) on board
    B: the stem's norm left the simulation on speech-calibrated grids, as stage0's first did without the patch.
    """
    from esp_ppq.api.espdl_interface import get_target_platform
    from esp_ppq.executor.base import OPERATION_FORWARD_TABLE

    from srpipe.compress.quant.ptq_espdl import BITS, TARGET, WIDE_BITS

    platforms = frozenset(get_target_platform(TARGET, bits) for bits in (BITS, WIDE_BITS))
    # Every esp-dl platform shares one table, so the patched forward checks the platform itself.
    tables = list({id(t): t for t in (OPERATION_FORWARD_TABLE[p] for p in platforms)}.values())
    simulated = [table["LayerNormalization"] for table in tables]
    for table, simulate in zip(tables, simulated, strict=True):
        table["LayerNormalization"] = functools.partial(layernorm_like_espdl, simulate=simulate, platforms=platforms)
    try:
        yield
    finally:
        for table, simulate in zip(tables, simulated, strict=True):
            table["LayerNormalization"] = simulate


def float_of(word: int) -> np.float32:
    return np.array([word], dtype=np.uint32).view(np.float32)[0]


def word_of(x: np.float32) -> int:
    return int(np.array([x], dtype=np.float32).view(np.int32)[0])


# The constants of picolibc's sf_exp.c as its object for the S3 holds them; P2 and P4 stored positive, subtracted.
EXPF_LN2_HI = (float_of(0x3F317180), float_of(0xBF317180))

EXPF_LN2_LO = (float_of(0x3717F7D1), float_of(0xB717F7D1))

EXPF_HALF = (np.float32(0.5), np.float32(-0.5))

EXPF_INVLN2, EXPF_HUGE, EXPF_TWOM100 = float_of(0x3FB8AA3B), float_of(0x7149F2CA), float_of(0x0D800000)

EXPF_P1, EXPF_P2, EXPF_P3 = float_of(0x3E2AAAAB), float_of(0x3B360B61), float_of(0x388AB355)

EXPF_P4, EXPF_P5 = float_of(0x35DDEA0E), float_of(0x3331BB4C)


def expf_s3(x: float) -> np.float32:
    """expf as the S3 runs it: picolibc's libm/math/sf_exp.c, fdlibm's algorithm, read off its machine code. Every
    step is one float32 operation rounded once, none fused, the division __divsf3's; it can differ from the correctly
    rounded exp by an ulp."""
    one, two = np.float32(1.0), np.float32(2.0)
    x = np.float32(x)
    sx = word_of(x)
    xsb, hx = int(sx < 0), sx & 0x7FFFFFFF
    if hx > 0x7F800000:
        return x + x
    if hx == 0x7F800000:
        return np.float32(0.0) if xsb else x
    if sx > 0x42B17217:
        return np.float32(np.inf)
    if xsb and hx > 0x42CFF1B5:
        return np.float32(0.0)
    k, hi, lo = 0, np.float32(0.0), np.float32(0.0)
    if hx > 0x3EB17218:
        if hx <= 0x3F851591:
            hi, lo, k = x - EXPF_LN2_HI[xsb], EXPF_LN2_LO[xsb], 1 - xsb - xsb
        else:
            k = int(x * EXPF_INVLN2 + EXPF_HALF[xsb])
            t = np.float32(k)
            hi, lo = x - t * EXPF_LN2_HI[0], t * EXPF_LN2_LO[0]
        x = hi - lo
    elif hx <= 0x33FFFFFF and EXPF_HUGE + x > one:
        return one + x
    t = x * x
    c = x - ((((t * EXPF_P5 - EXPF_P4) * t + EXPF_P3) * t - EXPF_P2) * t + EXPF_P1) * t
    xc = x * c
    if k == 0:
        return one - (xc / (c - two) - x)
    y = word_of(one - ((lo - xc / (two - c)) - hi))
    if k >= -125:
        return np.array([y + (k << 23)], dtype=np.int32).view(np.float32)[0]
    return np.array([y + ((k + 100) << 23)], dtype=np.int32).view(np.float32)[0] * EXPF_TWOM100


@functools.lru_cache(maxsize=64)
def exp_table_s3(scale: float) -> np.ndarray:
    """esp-dl's Softmax table at an input scale, as tool::gen_lut_8bit fills it on the S3: entry i is the S3's expf of
    scale times i - 128, a float32 product that is exact for esp-dl's power-of-two scales."""
    s = np.float32(scale)
    return np.array([expf_s3(s * np.float32(i - 128)) for i in range(256)], dtype=np.float32)


def softmax_like_espdl(op, values: list, ctx=None, *, simulate: Callable, platforms: frozenset, **kwargs):
    """op's output as esp-dl's Softmax computes it from an int8 input on one of platforms: each step's exp from its
    256-entry float32 table of the S3's expf, the row summed in float32 one value after another, each multiplied by
    the float32 reciprocal of that sum, as -ffast-math builds the source's division; simulate's output otherwise."""
    import torch
    from esp_ppq.core import QuantizationStates
    from esp_ppq.IR.quantize import QuantableOperation

    floating = simulate(op, values, ctx, **kwargs)
    if not isinstance(op, QuantableOperation) or op.platform not in platforms:
        return floating
    given = op.input_quant_config[0]
    if not QuantizationStates.is_activated(given.dominated_by.state) or given.num_of_bits != 8:
        return floating
    axis = op.attributes.get("axis", -1)
    with torch.no_grad():
        x, s = values[0].to(floating.device), given.scale.to(floating.device)
        q = torch.clamp(torch.round(x / s), -128, 127).long()
        table = torch.from_numpy(exp_table_s3(float(s.flatten()[0]))).to(floating.device)
        e = table[q + 128].movedim(axis, -1)
        total = torch.zeros_like(e[..., :1])
        for i in range(e.shape[-1]):
            total = total + e[..., i : i + 1]
        p = (e * (torch.ones_like(total) / total)).movedim(-1, axis)
    return floating - floating.detach() + p


@contextlib.contextmanager
def softmax_as_espdl() -> Iterator[None]:
    """ESP-PPQ simulates Softmax as torch's, the maximum taken out before exp; esp-dl's int8 kernel looks exp up in a
    table built at the input's exponent and sums the row in order, so a probability near a half step of the next op's
    grid can round the other way. ReDimNet2 b0 (KEHOACH 3.17) on board B: after layernorm_as_espdl, stage1's attention
    was the first op off the simulation, 6 of 87 616 probabilities a step apart.
    """
    from esp_ppq.api.espdl_interface import get_target_platform
    from esp_ppq.executor.base import OPERATION_FORWARD_TABLE

    from srpipe.compress.quant.ptq_espdl import BITS, TARGET, WIDE_BITS

    platforms = frozenset(get_target_platform(TARGET, bits) for bits in (BITS, WIDE_BITS))
    # Every esp-dl platform shares one table, so the patched forward checks the platform itself.
    tables = list({id(t): t for t in (OPERATION_FORWARD_TABLE[p] for p in platforms)}.values())
    simulated = [table["Softmax"] for table in tables]
    for table, simulate in zip(tables, simulated, strict=True):
        table["Softmax"] = functools.partial(softmax_like_espdl, simulate=simulate, platforms=platforms)
    try:
        yield
    finally:
        for table, simulate in zip(tables, simulated, strict=True):
            table["Softmax"] = simulate
