"""esp_ppq_patches.ops.band_stem: SwiftF0's stem after aligned_channels as BandStem, with esp-dl's integers."""

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

BANDS, FRAMES = 12, 8
STEM = re.compile(r"^  %(?P<out>\S+) = BandStem\[(?P<attrs>.*)\]\((?P<ins>.*)\)$", re.M)


class Stem(nn.Module):
    """SwiftF0's stem in small: a 3x3 convolution from three spectra to 12 channels with ReLU, read by one more."""

    def __init__(self) -> None:
        super().__init__()
        self.stem = nn.Conv2d(3, 12, 3, padding=1)
        self.after = nn.Conv2d(12, 4, 3, padding=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.after(torch.relu(self.stem(x)))


def band_stem_espdl(x: np.ndarray, w: np.ndarray, b: np.ndarray, shift: int) -> np.ndarray:
    """The stem's map [B, T, 16] from x [B, T, 3] as BandStem sums it, zero past the bands and frames."""
    bands, frames, _ = x.shape
    padded = np.pad(x.astype(np.int64), [(1, 1), (1, 1), (0, 0)])
    acc = np.tile(b.astype(np.int64), (bands, frames, 1))
    for i in range(3):
        for j in range(3):
            acc += padded[j : j + bands, i : i + frames] @ w[i, j].astype(np.int64)
    return np.maximum(round_shift(acc, shift), 0)


def test_band_stem_runs_the_stem_on_the_band_kernel_giving_its_integers(tmp_path: Path) -> None:
    torch.manual_seed(14)
    rng = np.random.default_rng(51)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 3, BANDS, FRAMES)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(Stem().eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    with esp_ppq_patches.applied(["aligned_channels", "band_stem"]):
        path = export_espdl.export(graph, tmp_path / "stem.espdl")
    _, tensors = espdl_graph(path)
    info = path.with_suffix(".info").read_text()
    (found,) = STEM.finditer(info)
    attrs = found["attrs"]
    assert attribute(attrs, "lanes") == 3 and attribute(attrs, "quant_type") == "S8"
    x_name, w_name, b_name = [n.strip().lstrip("%") for n in found["ins"].split(",")]
    w, b = tensors[w_name], tensors[b_name]
    assert w.shape == (3, 3, 3, 16) and b.shape == (16,)
    names = [x_name, found["out"]]
    values = TorchExecutor(graph, device="cpu").forward(inputs=calib[0], output_names=names)
    x, want = (
        torch.round(v / 2.0 ** exponent(info, n)).to(torch.int64).numpy()[0].transpose(1, 2, 0)
        for n, v in zip(names, values, strict=True)
    )
    shift = attribute(attrs, "shift")
    assert np.array_equal(band_stem_espdl(x, w, b, shift)[..., :12], want)
    assert not np.array_equal(band_stem_espdl(x, w, b, shift + 1)[..., :12], want)
