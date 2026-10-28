"""esp_ppq_patches.ops.band_chain: a run of single-frame BandBlocks as one BandChain, with esp-dl's integers; blocks
whose middle map another op also reads stay as BandBlocks."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from esp_ppq.executor.torch import TorchExecutor  # noqa: E402
from torch import nn  # noqa: E402

from esp_ppq_cases import RUNGS, espdl_graph, exponent  # noqa: E402
from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl  # noqa: E402
from srpipe.compress.quant.espdl_shapes import attribute  # noqa: E402
from test_esp_ppq_patches_band_block import band_block_espdl  # noqa: E402

BANDS, FRAMES, CHANNELS = 24, 6, 12
PATCHES = ["aligned_channels", "band_block", "band_chain"]
CHAIN = re.compile(r"^  %(?P<out>\S+) = BandChain\[(?P<attrs>.*)\]\((?P<ins>.*)\)$", re.M)
SHAPES = ((5, 3), (3, 7))  # band taps and dilation of each block


class Chain(nn.Module):
    """A stem, then two single-frame residual blocks along the bands, then a head; with branch a second head reads
    the first block's output too."""

    def __init__(self, branch: bool = False) -> None:
        super().__init__()
        self.stem = nn.Conv2d(3, CHANNELS, 3, padding=1)
        self.convs = nn.ModuleList(
            nn.Conv2d(CHANNELS, CHANNELS, (k, 1), padding=(d * (k // 2), 0), dilation=(d, 1))
            for k, d in SHAPES
            for _ in range(2)
        )
        self.head = nn.Conv2d(CHANNELS, 4, 3, padding=1)
        self.other = nn.Conv2d(CHANNELS, 4, 3, padding=1)
        self.branch = branch

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = torch.relu(self.stem(x))
        x = torch.relu(x + self.convs[1](torch.relu(self.convs[0](x))))
        first = x
        x = torch.relu(x + self.convs[3](torch.relu(self.convs[2](x))))
        y = self.head(x[:, :, : BANDS - 4])
        return y + self.other(first[:, :, : BANDS - 4]) if self.branch else y


def exported(net: nn.Module, tmp_path: Path):
    rng = np.random.default_rng(61)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 3, BANDS, FRAMES)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(net.eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    with esp_ppq_patches.applied(PATCHES):
        path = export_espdl.export(graph, tmp_path / "chain.espdl")
    return graph, calib[0], path


def test_band_chain_runs_both_blocks_as_one_op_giving_their_integers(tmp_path: Path) -> None:
    torch.manual_seed(15)
    graph, x_in, path = exported(Chain(), tmp_path)
    nodes, tensors = espdl_graph(path)
    info = path.with_suffix(".info").read_text()
    (found,) = CHAIN.finditer(info)
    assert "BandBlock" not in {op for op, _ in nodes.values()}
    attrs = found["attrs"]
    assert attribute(attrs, "band_taps") == [k for k, _ in SHAPES]
    assert attribute(attrs, "dilations") == [d for _, d in SHAPES]
    x_name, *params = [n.strip().lstrip("%") for n in found["ins"].split(",")]
    names = [x_name, found["out"]]
    values = TorchExecutor(graph, device="cpu").forward(inputs=x_in, output_names=names)
    x, want = (
        torch.round(v / 2.0 ** exponent(info, n)).to(torch.int64).numpy()[0].transpose(1, 2, 0)
        for n, v in zip(names, values, strict=True)
    )
    shifts = attribute(attrs, "shifts")
    m = np.pad(x, [(0, 0), (0, 0), (0, 16 - CHANNELS)])
    for k, (_, d) in enumerate(SHAPES):
        m = band_block_espdl(m, [tensors[n] for n in params[4 * k : 4 * k + 4]], d, shifts[3 * k : 3 * k + 3])
    assert np.array_equal(m[..., :CHANNELS], want)


def test_band_chain_leaves_blocks_whose_middle_map_another_op_reads(tmp_path: Path) -> None:
    torch.manual_seed(15)
    _, _, path = exported(Chain(branch=True), tmp_path)
    nodes, _ = espdl_graph(path)
    assert [op for op, _ in nodes.values()].count("BandBlock") == 2
    assert not list(CHAIN.finditer(path.with_suffix(".info").read_text()))
