"""esp_ppq_patches.ops.layernorm_s8: the op's export, its float mean and its one-step root against esp-dl's
Newton."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from esp_ppq_cases import export_info, layernorm_graph  # noqa: E402
from srpipe.compress.quant import esp_ppq_patches  # noqa: E402


def test_layernorm_s8_exports_an_int8_norm_as_its_own_op(tmp_path: Path) -> None:
    graph, _ = layernorm_graph(tmp_path)
    renamed = export_info(graph, tmp_path / "renamed.espdl", ["layernorm_s8"])
    as_is = export_info(graph, tmp_path / "as_is.espdl", [])
    (attrs,) = re.findall(r"= LayerNormS8\[(.*?)\]", renamed)
    assert not re.findall(r"= LayerNormalization\[", renamed)
    assert re.findall(r"= LayerNormalization\[(.*?)\]", as_is) == [attrs]


@pytest.mark.parametrize("n", [1, 2, 3, 6, 8, 12, 16, 24, 48, 96, 192])
def test_layernorm_s8_takes_the_mean_by_float_division_where_it_equals_espdls_double_one(n: int) -> None:
    """LayerNormS8 takes the float32 quotient when n's odd part is 1 or 3, as a product by fl(1 / n) corrected by
    one FMA; esp-dl takes every int8 sum through double."""
    sums = np.arange(-128 * n, 127 * n + 1)
    through_double = (sums.astype(np.float64) * (1.0 / n)).astype(np.float32)
    assert np.array_equal(sums.astype(np.float32) / np.float32(n), through_double)
    s = torch.from_numpy(sums.astype(np.float32))
    inverse, count = torch.full_like(s, 1.0 / np.float32(n)), torch.full_like(s, n)
    product = s * inverse
    corrected = esp_ppq_patches.fma32(esp_ppq_patches.fma32(-product, count, s), inverse, product)
    assert np.array_equal(corrected.numpy(), through_double)
    assert n % 3 or not np.array_equal(product.numpy(), through_double)


ROOT_BINADES = range(-3, 12)  # kRootLow to kRootHigh of espdl_layernorm_s8.cpp
ROOT_WINDOW = 2.0**-7


def one_step_root(x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """Roots of x as layernorm_s8_esp32s3.S takes them, each step rounded once: s0 = sqrtf(x), fl(x / s0) from the
    exact remainder against h = s0 * ulp(s0) / 2, one Newton step; flagged for esp-dl's own Newton where the
    remainder lies within ROOT_WINDOW * h under +-h, or s0 is a power of 2."""
    s0 = torch.sqrt(x)
    rem = esp_ppq_patches.fma32(-s0, s0, x)
    bits = s0.view(torch.int32)
    half_ulp = ((bits & 0x7F800000) - (24 << 23)).view(torch.float32)
    h = s0 * half_ulp
    ulp = half_ulp + half_ulp
    q = torch.where(h < rem, s0 + ulp, s0)
    q = torch.where(rem < -h, s0 - ulp, q)
    window = h * ROOT_WINDOW
    flagged = (((rem + window).abs() - h).abs() <= window) | ((bits & 0x7FFFFF) == 0)
    return (q + s0) * 0.5, flagged


@pytest.mark.parametrize("binade", ROOT_BINADES)
def test_layernorm_s8_roots_by_one_newton_step_from_sqrtf_as_espdls_newton_does(binade: int) -> None:
    """Every float32 of [2^binade, 2^(binade + 1)): the unflagged roots are sqrt_newton's, under 1% flagged; sqrtf's
    own roots are not sqrt_newton's."""
    x = (torch.arange(2**23, dtype=torch.int32) | ((binade + 127) << 23)).view(torch.float32)
    root, flagged = one_step_root(x)
    want = esp_ppq_patches.sqrt_newton(x)
    assert torch.equal(root[~flagged], want[~flagged])
    assert flagged.float().mean() < 0.01
    assert not torch.equal(torch.sqrt(x), want)
