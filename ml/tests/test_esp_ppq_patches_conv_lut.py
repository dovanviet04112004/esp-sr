"""esp_ppq_patches.ops.conv_lut: a convolution and the table activation alone reading it exported as one ConvLut,
its weights, bias and table those of the pair, and a convolution another op also reads left as it is."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from torch import nn  # noqa: E402

from esp_ppq_cases import RUNGS, espdl_graph  # noqa: E402
from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl  # noqa: E402


class Joinable(nn.Module):
    """A 3 x 3 convolution into Swish, then a 1 x 1 one whose map both Swish and an Add read."""

    def __init__(self) -> None:
        super().__init__()
        self.first = nn.Conv2d(16, 32, 3, padding=1)
        self.second = nn.Conv2d(32, 32, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        z = self.second(nn.functional.silu(self.first(x)))
        return nn.functional.silu(z) + z


def exported(tmp_path: Path, rungs: dict) -> tuple:
    """The plain export and the conv_lut one of Joinable: each one's nodes, initializers and .info."""
    torch.manual_seed(3)
    rng = np.random.default_rng(31)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 16, 8, 8)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(Joinable().eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"} | rungs)
    found = []
    for name, patches in (("plain", []), ("joined", ["conv_lut"])):
        with esp_ppq_patches.applied(patches):
            path = export_espdl.export(graph, tmp_path / f"{name}.espdl")
        found.append((*espdl_graph(path), path.with_suffix(".info").read_text(encoding="utf-8")))
    return found


@pytest.mark.parametrize("rungs", [{}, {"int16_ops": ["/first/Conv", "/first/Conv/Swish"]}], ids=["int8", "int16"])
def test_conv_lut_joins_the_convolution_its_activation_alone_reads(tmp_path: Path, rungs: dict) -> None:
    (plain_nodes, plain, plain_info), (nodes, tensors, info) = exported(tmp_path, rungs)
    ops = [op for op, _ in nodes.values()]
    assert ops.count("ConvLut") == 1 and ops.count("Conv") == 1 and len(nodes) == len(plain_nodes) - 1
    swish = re.search(r"^  %(\S+) = \w+\[.*lut = '(\w+)'.*\]\(%/first/Conv_output_0\)$", plain_info, re.MULTILINE)
    joined = re.search(r"^  %(\S+) = ConvLut\[(.*)\]\((.*)\)$", info, re.MULTILINE)
    exponent = re.search(r"^%/first/Conv_output_0\[\w+, [0-9x]+\], exponents: \[(-?\d+)\]", plain_info, re.MULTILINE)
    assert joined.group(1) == swish.group(1)
    assert f"lut = '{swish.group(2)}'" in joined.group(2)
    assert f"conv_exponent = {exponent.group(1)}" in joined.group(2)
    for name in [*(v.strip().lstrip("%") for v in joined.group(3).split(",")[1:]), swish.group(2)]:
        assert np.array_equal(tensors[name], plain[name])
