"""Streaming export of a residual TCN: each cache sits ahead of its convolution only, so a residual Add keeps the
current hop, and a convolution that pads ahead in time is refused rather than streamed wrong. A native graph reads
back as the same int8 graph."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from torch import nn  # noqa: E402

from srpipe.compress.quant import export_espdl, ptq_espdl  # noqa: E402
from srpipe.tasks.wake.model.tcn import Tcn  # noqa: E402

BANDS, HOPS = 8, 40
RUNGS = ptq_espdl.ladder("wake")


def calib(rng: np.random.Generator) -> list:
    return [torch.from_numpy(rng.normal(0, 1, (1, BANDS, HOPS)).astype(np.float32)) for _ in range(4)]


def test_each_cache_feeds_its_convolution_and_the_residual_add_the_current_hop(tmp_path: Path) -> None:
    torch.manual_seed(0)
    graph = ptq_espdl.quantize(Tcn(BANDS, 8, 3, [1, 2]), calib(np.random.default_rng(0)), tmp_path, RUNGS)
    out = export_espdl.export(graph, tmp_path / "net.espdl", streaming_input_shape=[1, BANDS, 1])
    info = out.with_suffix(".info").read_text()
    caches = set(re.findall(r"%(\S+) = StreamingCache\[", info))
    assert len(caches) == 2
    for line in re.findall(r"= Add\[.*", info):
        assert not caches & set(re.findall(r"%([^,)\s]+)", line.split("](", 1)[1]))
    for line in re.findall(r"= Conv\[.*kernel_shape = \[3\].*", info):
        assert "pads = [0, 0]" in line
        assert re.search(r"\]\(%([^,]+),", line).group(1) in caches


def test_a_convolution_that_pads_ahead_is_refused(tmp_path: Path) -> None:
    model = nn.Sequential(nn.Conv1d(BANDS, 4, 3, padding=1))
    graph = ptq_espdl.quantize(model, calib(np.random.default_rng(1)), tmp_path, RUNGS)
    with pytest.raises(ValueError, match="pads ahead"):
        export_espdl.export(graph, tmp_path / "net.espdl", streaming_input_shape=[1, BANDS, 1])


def test_a_native_graph_reads_back_as_the_same_int8_graph(tmp_path: Path) -> None:
    torch.manual_seed(2)
    graph = ptq_espdl.quantize(Tcn(BANDS, 8, 3, [1, 2]), calib(np.random.default_rng(2)), tmp_path, RUNGS)
    back = export_espdl.load_native(export_espdl.save_native(graph, tmp_path / "graph.native"))
    x = np.random.default_rng(3).normal(0, 1, (1, BANDS, HOPS)).astype(np.float32)
    assert ptq_espdl.io_of(back) == ptq_espdl.io_of(graph)
    assert np.array_equal(ptq_espdl.Simulator(back)(x), ptq_espdl.Simulator(graph)(x))
