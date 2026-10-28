"""esp_ppq_patches.ops.band_block: SwiftF0's residual blocks after aligned_channels as BandBlock, with esp-dl's
integers; a block whose middle map is a graph output stays as it is."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from esp_ppq.executor.torch import TorchExecutor  # noqa: E402
from torch import nn  # noqa: E402

from esp_ppq_cases import RUNGS, espdl_graph, exponent, round_shift  # noqa: E402
from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl  # noqa: E402
from srpipe.compress.quant.espdl_shapes import attribute  # noqa: E402

BANDS, FRAMES, CHANNELS = 24, 10, 12
PATCHES = ["aligned_channels", "band_block"]
BLOCK = re.compile(r"^  %(?P<out>\S+) = BandBlock\[(?P<attrs>.*)\]\((?P<ins>.*)\)$", re.M)


class Blocks(nn.Module):
    """SwiftF0's trunk in small: a stem from three spectra, a block of 7 x 3 kernels, one of 5 x 1 kernels dilated 4
    along the bands, then a head on the lowest bands; middle_out gives the first block's middle map too."""

    def __init__(self, middle_out: bool = False) -> None:
        super().__init__()
        self.stem = nn.Conv2d(3, CHANNELS, 3, padding=1)
        wide = [nn.Conv2d(CHANNELS, CHANNELS, (7, 3), padding=(3, 1)) for _ in range(2)]
        tall = [nn.Conv2d(CHANNELS, CHANNELS, (5, 1), padding=(8, 0), dilation=(4, 1)) for _ in range(2)]
        self.convs = nn.ModuleList([*wide, *tall])
        self.head = nn.Conv2d(CHANNELS, 4, 3, padding=1)
        self.middle_out = middle_out

    def forward(self, x: torch.Tensor) -> torch.Tensor | tuple[torch.Tensor, torch.Tensor]:
        x = torch.relu(self.stem(x))
        h = torch.relu(self.convs[0](x))
        x = torch.relu(x + self.convs[1](h))
        x = torch.relu(x + self.convs[3](torch.relu(self.convs[2](x))))
        y = self.head(x[:, :, : BANDS - 4])
        return (y, h) if self.middle_out else y


def band_conv(m: np.ndarray, w: np.ndarray, b: np.ndarray, d: int, shift: int) -> np.ndarray:
    """A convolution of m [B, T, 16] by w [time tap][band tap][inputs][16] with bias b, as BandBlock's kernel sums
    the taps within the map and esp-dl rounds them."""
    kt, kb, inputs, _ = w.shape
    bands, frames, _ = m.shape
    acc = np.tile(b.astype(np.int64), (bands, frames, 1))
    for i in range(kt):
        for j in range(kb):
            db, dt = d * (j - kb // 2), i - kt // 2
            src = np.zeros((bands, frames, inputs), np.int64)
            src[max(0, -db) : min(bands, bands - db), max(0, -dt) : min(frames, frames - dt)] = m[
                max(0, db) : min(bands, bands + db), max(0, dt) : min(frames, frames + dt), :inputs
            ]
            acc += src @ w[i, j].astype(np.int64)
    return round_shift(acc, shift)


def band_block_espdl(x: np.ndarray, params: list[np.ndarray], d: int, shifts: list[int]) -> np.ndarray:
    """A block's output [B, T, 16] from x [B, T, 16] as esp-dl's convolutions, RequantizeLinear, Add and ReLU give
    it."""
    w1, b1, w2, b2 = params
    h = np.maximum(band_conv(x, w1, b1, d, shifts[0]), 0)
    s = band_conv(h, w2, b2, d, shifts[1])
    return np.maximum(np.clip(s + round_shift(x.astype(np.int64), shifts[2]), -128, 127), 0)


def exported(net: nn.Module, tmp_path: Path):
    rng = np.random.default_rng(31)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 3, BANDS, FRAMES)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(net.eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    with esp_ppq_patches.applied(PATCHES):
        path = export_espdl.export(graph, tmp_path / "blocks.espdl")
    return graph, calib[0], path


def test_band_block_runs_each_residual_block_as_one_op_giving_its_integers(tmp_path: Path) -> None:
    torch.manual_seed(12)
    graph, x_in, path = exported(Blocks(), tmp_path)
    nodes, tensors = espdl_graph(path)
    info = path.with_suffix(".info").read_text()
    blocks = list(BLOCK.finditer(info))
    assert len(blocks) == 2 and not any(op in ("Add", "Relu") for op, _ in nodes.values())
    ends = [b["out"] for b in blocks] + [b["ins"].split(",")[0].strip().lstrip("%") for b in blocks]
    names = list(dict.fromkeys(ends))
    values = TorchExecutor(graph, device="cpu").forward(inputs=x_in, output_names=names)
    as_int = {
        n: torch.round(v / 2.0 ** exponent(info, n)).to(torch.int64).numpy()[0]
        for n, v in zip(names, values, strict=True)
    }
    for found, (kt, kb, d) in zip(blocks, [(3, 7, 1), (1, 5, 4)], strict=True):
        attrs = found["attrs"]
        assert attribute(attrs, "kernel") == [kb, kt] and attribute(attrs, "dilation") == d
        assert attribute(attrs, "in_channels") == CHANNELS
        x_name, *param_names = [n.strip().lstrip("%") for n in found["ins"].split(",")]
        params = [tensors[n] for n in param_names]
        assert params[0].shape == params[2].shape == (kt, kb, CHANNELS, 16) and params[1].shape == (16,)
        x = np.pad(as_int[x_name].transpose(1, 2, 0), [(0, 0), (0, 0), (0, 16 - CHANNELS)])
        want = as_int[found["out"]].transpose(1, 2, 0)
        shifts = attribute(attrs, "shifts")
        assert np.array_equal(band_block_espdl(x, params, d, shifts)[..., :CHANNELS], want)
        off = band_block_espdl(x, params, d, [shifts[0] + 1, *shifts[1:]])[..., :CHANNELS]
        assert not np.array_equal(off, want)


def test_band_block_leaves_a_block_whose_middle_map_is_a_graph_output(tmp_path: Path) -> None:
    torch.manual_seed(12)
    _, _, path = exported(Blocks(middle_out=True), tmp_path)
    blocks = list(BLOCK.finditer(path.with_suffix(".info").read_text()))
    assert [attribute(b["attrs"], "dilation") for b in blocks] == [4]
