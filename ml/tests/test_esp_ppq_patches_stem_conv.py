"""esp_ppq_patches.ops.stem_conv: the stem's copies and their depthwise as StemConv, with esp-dl's integers."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from torch import nn  # noqa: E402

from esp_ppq_cases import RUNGS, CopiedStem, espdl_graph, exponent, export_info, round_shift  # noqa: E402
from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl  # noqa: E402


@pytest.mark.parametrize("c", [12, 24])
def test_stem_conv_runs_the_copies_and_their_depthwise_as_one_op(tmp_path: Path, c: int) -> None:
    torch.manual_seed(4)
    rng = np.random.default_rng(17)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 1, 6, 8)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(CopiedStem(c).eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    with esp_ppq_patches.applied(["copies_by_broadcast", "stem_conv"]):
        path = export_espdl.export(graph, tmp_path / "stem.espdl")
    nodes, tensors = espdl_graph(path)
    ((_, (_, taps, bias)),) = [node for node in nodes.values() if node[0] == "StemConv"]
    assert not {"Add", "Conv", "Concat", "Transpose"} & {op for op, _ in nodes.values()}
    vectors = -(-c // 16)
    assert tensors[taps].shape == (vectors * 9, 16) and tensors[bias].shape == (vectors * 16,)
    shift = int(re.search(r"= StemConv\[.*shift = (\d+)", path.with_suffix(".info").read_text()).group(1))
    io = ptq_espdl.io_of(graph)
    x = ptq_espdl.to_int8(calib[0].numpy(), io.input_exponent)
    want = ptq_espdl.Simulator(graph)(x.astype(np.float32) * np.float32(2.0**io.input_exponent))
    want = np.round(want[0].transpose(1, 2, 0) / 2.0**io.output_exponent).astype(np.int64)
    padded = np.pad(x[0, 0].astype(np.int64), 1)
    laid = tensors[taps].reshape(vectors, 9, 16).transpose(1, 0, 2).reshape(9, vectors * 16)
    weights, offsets = laid[:, :c].astype(np.int64), tensors[bias][:c].astype(np.int64)

    def stem(s: int) -> np.ndarray:
        acc = sum(padded[i : i + 6, j : j + 8, None] * weights[3 * i + j] for i in range(3) for j in range(3))
        return round_shift(acc + offsets, s)

    assert np.array_equal(stem(shift), want)
    assert not np.array_equal(stem(shift + 1), want)


class NormedStem(CopiedStem):
    """CopiedStem with a LayerNorm over its channels after it, as ReDimNet2's stem norms its map."""

    def __init__(self, c: int = 12) -> None:
        super().__init__(c)
        self.norm = nn.LayerNorm(c)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.norm(super().forward(x).permute(0, 2, 3, 1)).permute(0, 3, 1, 2)


def test_stem_conv_takes_the_layernorm_reading_it(tmp_path: Path) -> None:
    torch.manual_seed(4)
    rng = np.random.default_rng(17)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 1, 6, 8)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(NormedStem(24).eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    patches = ["lean_transposes", "copies_by_broadcast", "layernorm_s8"]
    apart = export_info(graph, tmp_path / "apart.espdl", patches)
    joined = export_info(graph, tmp_path / "joined.espdl", [*patches, "stem_conv"])
    (normed_in,) = re.findall(r"= LayerNormS8\[.*?\]\(%([^,)\s]+)", apart)
    assert not re.findall(r"= LayerNormS8\[", joined)
    attrs, inputs = re.search(r"= StemConv\[(.*?)\]\((.*?)\)", joined).groups()
    assert "normed = 1" in attrs and len(inputs.split(",")) == 5
    assert int(re.search(r"conv_exponent = (-?\d+)", attrs).group(1)) == exponent(apart, normed_in)
