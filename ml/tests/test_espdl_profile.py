"""The speed procedure's profile report (KEHOACH 3.14) reads a board log's per-module times against the exported graph
and tags each of esp-dl's slow S3 paths with the patch that handles it, and nothing on a module that takes none."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from torch import nn  # noqa: E402

from srpipe.compress.quant import espdl_profile, export_espdl, ptq_espdl  # noqa: E402

FRAMES = 6


class SlowPaths(nn.Module):
    """A projection whose weights outgrow the data cache, a narrow pointwise convolution, a narrow 3-tap one, an
    aligned one, and a product with fewer than 16 output columns."""

    def __init__(self) -> None:
        super().__init__()
        self.wide, self.narrow = nn.Conv1d(576, 128, 1), nn.Conv1d(128, 24, 1)
        self.taps, self.aligned = nn.Conv1d(24, 32, 3, padding=1), nn.Conv1d(32, 32, 1)
        self.columns = nn.Linear(32, 8)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = self.aligned(self.taps(self.narrow(self.wide(x))))
        return self.columns(y.transpose(1, 2))


def tagged(report: str) -> dict[str, list[str]]:
    """Each of a report's slowest modules by name, with its tags."""
    found, name = {}, None
    for line in report.split("modules:\n")[-1].splitlines():
        if line.lstrip().startswith("- ") and name is not None:
            found[name].append(line.lstrip().removeprefix("- "))
        else:
            name = line.split()[-1]
            found[name] = []
    return found


def test_report_tags_each_slow_path_and_leaves_an_aligned_convolution_alone(tmp_path: Path) -> None:
    torch.manual_seed(0)
    rng = np.random.default_rng(0)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, 576, FRAMES)).astype(np.float32)) for _ in range(2)]
    rungs = ptq_espdl.ladder("speaker") | {"equalization": None, "bias_correction": False, "calibration": "minmax"}
    graph = ptq_espdl.quantize(SlowPaths().eval(), calib, tmp_path, rungs)
    espdl = export_espdl.export(graph, tmp_path / "slow.espdl")
    log = "\n".join(
        f"I (1) dl::Model: | {name:40s} | {op:12s} | {100 + i}us |"
        for i, (name, (op, *_)) in enumerate(espdl_profile.nodes(espdl).items())
    )
    found = tagged(espdl_profile.report(f"| /wide/Conv | Conv | 1us |\n{log}", espdl, top=50))
    assert any("outgrow the data cache" in t for t in found["/wide/Conv"])
    assert found["/narrow/Conv"] == ["unaligned 128 -> 24 channels (aligned_pointwise)"]
    assert found["/taps/Conv"] == ["unaligned 24 -> 32 channels (no export patch)"]
    assert found["/aligned/Conv"] == []
    assert ["8 output columns run in C (lay the product transposed)"] in found.values()


def test_profile_keeps_the_last_time_each_module_was_printed() -> None:
    log = "| /a/Conv | Conv | 5us |\n| /a/Conv | Conv | 7us |\n| /b/Add | Add | 3us |"
    assert espdl_profile.profile(log) == {"/a/Conv": ("Conv", 7), "/b/Add": ("Add", 3)}
