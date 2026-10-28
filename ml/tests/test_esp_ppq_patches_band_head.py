"""esp_ppq_patches.ops.band_head: SwiftF0's head after aligned_channels as one BandHead of two outputs, with esp-dl's
integers; a head whose middle map is a graph output stays as it is."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from esp_ppq.executor.torch import TorchExecutor  # noqa: E402
from torch import nn  # noqa: E402
from torch.nn import functional  # noqa: E402

from esp_ppq_cases import RUNGS, espdl_graph, exponent, round_shift  # noqa: E402
from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl  # noqa: E402
from srpipe.compress.quant.espdl_shapes import attribute  # noqa: E402

BANDS, FRAMES, SLICED = 24, 10, 20
PATCHES = ["aligned_channels", "band_head"]
HEAD = re.compile(r"^  %(?P<outs>\S+, %\S+) = BandHead\[(?P<attrs>.*)\]\((?P<ins>.*)\)$", re.M)


class Head(nn.Module):
    """SwiftF0's head in small: a stem to 12 channels, then on its lowest bands a 3x3 convolution to 4 channels padded
    one band below, its 5x1 spread's maximum over the bands and its 1x1 pitch convolution; middle_out gives the 3x3
    convolution's map too."""

    def __init__(self, middle_out: bool = False) -> None:
        super().__init__()
        self.stem = nn.Conv2d(3, 12, 3, padding=1)
        self.head = nn.Conv2d(12, 4, 3)
        self.spread = nn.Conv2d(4, 4, (5, 1), padding=(2, 0))
        self.pitch = nn.Conv2d(4, 1, 1)
        self.middle_out = middle_out

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, ...]:
        x = torch.relu(self.stem(x))
        h = torch.relu(self.head(functional.pad(x[:, :, :SLICED], (1, 1, 1, 0))))
        outs = (self.pitch(h)[:, 0], self.spread(h).amax(dim=2))
        return (*outs, h) if self.middle_out else outs


def band_conv(m: np.ndarray, w: np.ndarray, b: np.ndarray, bands: int, below: int, shift: int) -> np.ndarray:
    """bands output bands of m [B, T, 16] convolved by w [time tap][band tap][12][16] with bias b, output band o
    reading input band o - below + tap of m where it has one, as band_s8_esp32s3.S sums them."""
    kt, kb, inputs, _ = w.shape
    frames = m.shape[1]
    acc = np.tile(b.astype(np.int64), (bands, frames, 1))
    for i in range(kt):
        dt = i - kt // 2
        src = np.zeros((m.shape[0], frames, inputs), np.int64)
        src[:, max(0, -dt) : min(frames, frames - dt)] = m[:, max(0, dt) : min(frames, frames + dt), :inputs]
        for j in range(kb):
            for o in range(bands):
                if 0 <= o - below + j < m.shape[0]:
                    acc[o] += src[o - below + j] @ w[i, j].astype(np.int64)
    return round_shift(acc, shift)


def band_head_espdl(x: np.ndarray, params: list[np.ndarray], bands: int, shifts: list[int], outputs: int):
    """(logits [bands - 1, T], peaks [T, outputs]) from x [bands, T, 16] as esp-dl's head gives them."""
    hw, hb, sw, sb, pw, pb = params
    h = np.maximum(band_conv(x[:bands], hw, hb, bands - 1, 1, shifts[0]), 0)
    s = band_conv(h, sw, sb, bands - 1, 2, shifts[1])
    logits = round_shift(h[..., :outputs].astype(np.int64) @ pw.reshape(-1) + int(pb.reshape(-1)[0]), shifts[2])
    return logits, s[..., :outputs].max(axis=0)


def exported(net: nn.Module, tmp_path: Path):
    rng = np.random.default_rng(41)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 3, BANDS, FRAMES)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(net.eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    with esp_ppq_patches.applied(PATCHES):
        path = export_espdl.export(graph, tmp_path / "head.espdl")
    return graph, calib[0], path


def test_band_head_runs_the_head_as_one_op_giving_both_outputs_integers(tmp_path: Path) -> None:
    torch.manual_seed(13)
    graph, x_in, path = exported(Head(), tmp_path)
    nodes, tensors = espdl_graph(path)
    info = path.with_suffix(".info").read_text()
    (found,) = HEAD.finditer(info)
    assert not any(op in ("Slice", "ReduceMax") for op, _ in nodes.values())
    bands, shifts = attribute(found["attrs"], "bands"), attribute(found["attrs"], "shifts")
    assert bands == SLICED and attribute(found["attrs"], "quant_type") == "S8"
    x_name, *param_names = [n.strip().lstrip("%") for n in found["ins"].split(",")]
    params = [tensors[n] for n in param_names]
    assert [p.shape for p in params] == [(3, 3, 12, 16), (16,), (1, 5, 12, 16), (16,), (1, 4), (1,)]
    outs = [o.strip().lstrip("%") for o in found["outs"].split(",")]
    values = TorchExecutor(graph, device="cpu").forward(inputs=x_in, output_names=[x_name, *outs])
    ints = [
        torch.round(v / 2.0 ** exponent(info, n)).to(torch.int64).numpy()[0]
        for n, v in zip([x_name, *outs], values, strict=True)
    ]
    x = np.pad(ints[0].transpose(1, 2, 0), [(0, 0), (0, 0), (0, 4)])
    logits, peaks = band_head_espdl(x, params, bands, shifts, 4)
    assert np.array_equal(logits, ints[1][0]) and np.array_equal(peaks, ints[2].T)
    off, _ = band_head_espdl(x, params, bands, [shifts[0] + 1, *shifts[1:]], 4)
    assert not np.array_equal(off, ints[1][0])


def test_band_head_leaves_a_head_whose_middle_map_is_a_graph_output(tmp_path: Path) -> None:
    torch.manual_seed(13)
    _, _, path = exported(Head(middle_out=True), tmp_path)
    assert not list(HEAD.finditer(path.with_suffix(".info").read_text()))
