"""Fixes to ESP-PPQ 1.3.11's quantisation, simulation and esp-dl export, in force wherever the branch whose config names
them runs ESP-PPQ (KEHOACH 3.14); a branch that names none runs exactly as ESP-PPQ does.

Each fix states the bug, what board B showed without it and the test that pins it (tests/test_esp_ppq_patches_*.py);
when the ESP-PPQ pin moves, re-check each one upstream.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Sequence

from .base import CHANNEL_STEP, DATA_CACHE_BYTES
from .layout import (
    aligned_channels,
    aligned_pointwise,
    cache_sized_convolutions,
    chain_as_one,
    channel_scales_as_mul,
    copies_by_broadcast,
    identity_convolutions_dropped,
    lean_quantizers,
    lean_transposes,
    unsqueeze_axes_positive,
)
from .ops.attention import attention_core
from .ops.band_block import band_block
from .ops.band_chain import band_chain
from .ops.band_head import band_head
from .ops.band_stem import band_stem
from .ops.band_tail import band_stream
from .ops.conv_block import conv_block
from .ops.global_stats import global_stats
from .ops.group_norm_s8 import group_norm_s8
from .ops.layernorm_s8 import layernorm_s8
from .ops.matmul_s8 import matmul_s8
from .ops.pointwise_s8 import pointwise_s8
from .ops.softmax_quant import products_by_byte, softmax_quant, softmax_scale
from .ops.stage_sum import stage_sum
from .ops.stem_conv import stem_conv
from .ops.stride_depthwise import stride_depthwise
from .ops.time_depthwise import time_depthwise
from .ops.weighted_stats import weighted_stats
from .quantise import (
    conv_caches_along_time,
    fuse_erf_gelu,
    fuse_passive_ops_on_graph_inputs,
    fuse_tanh_gelu,
    requantise_graph_inputs,
    simplify_without_bn_fusion,
)
from .simulate import (
    exp_table_s3,
    expf_s3,
    fma32,
    layernorm_as_espdl,
    rms_like_espdl,
    rmsnorm_as_espdl,
    rmsnorm_like_espdl,
    softmax_as_espdl,
    sqrt_newton,
    word_of,
)

__all__ = [
    "CHANNEL_STEP",
    "DATA_CACHE_BYTES",
    "PATCHES",
    "applied",
    "chain_as_one",
    "exp_table_s3",
    "expf_s3",
    "fma32",
    "products_by_byte",
    "rms_like_espdl",
    "rmsnorm_like_espdl",
    "sqrt_newton",
    "word_of",
]

PATCHES = {
    "requantise_graph_inputs": requantise_graph_inputs,
    "conv_caches_along_time": conv_caches_along_time,
    "fuse_passive_ops_on_graph_inputs": fuse_passive_ops_on_graph_inputs,
    "rmsnorm_as_espdl": rmsnorm_as_espdl,
    "simplify_without_bn_fusion": simplify_without_bn_fusion,
    "fuse_erf_gelu": fuse_erf_gelu,
    "layernorm_as_espdl": layernorm_as_espdl,
    "softmax_as_espdl": softmax_as_espdl,
    "fuse_tanh_gelu": fuse_tanh_gelu,
    "lean_transposes": lean_transposes,
    "lean_quantizers": lean_quantizers,
    "unsqueeze_axes_positive": unsqueeze_axes_positive,
    "aligned_pointwise": aligned_pointwise,
    "aligned_channels": aligned_channels,
    "copies_by_broadcast": copies_by_broadcast,
    "cache_sized_convolutions": cache_sized_convolutions,
    "identity_convolutions_dropped": identity_convolutions_dropped,
    "channel_scales_as_mul": channel_scales_as_mul,
    "softmax_quant": softmax_quant,
    "softmax_scale": softmax_scale,
    "layernorm_s8": layernorm_s8,
    "group_norm_s8": group_norm_s8,
    "stage_sum": stage_sum,
    "conv_block": conv_block,
    "matmul_s8": matmul_s8,
    "attention_core": attention_core,
    "stem_conv": stem_conv,
    "stride_depthwise": stride_depthwise,
    "time_depthwise": time_depthwise,
    "weighted_stats": weighted_stats,
    "global_stats": global_stats,
    "pointwise_s8": pointwise_s8,
    "band_block": band_block,
    "band_head": band_head,
    "band_stem": band_stem,
    "band_chain": band_chain,
    "band_stream": band_stream,
}


@contextlib.contextmanager
def applied(names: Sequence[str]) -> Iterator[None]:
    """The named patches, in force for the length of the block."""
    unknown = sorted(set(names) - set(PATCHES))
    if unknown:
        raise ValueError(f"unknown ESP-PPQ patches {unknown}; known: {sorted(PATCHES)}")
    with contextlib.ExitStack() as stack:
        for name in names:
            stack.enter_context(PATCHES[name]())
        yield
