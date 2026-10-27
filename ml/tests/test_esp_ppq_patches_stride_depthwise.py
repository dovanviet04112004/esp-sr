"""esp_ppq_patches.ops.stride_depthwise: frame-stride depthwise convolutions as StrideDepthwise, with esp-dl's
integers."""

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


class FrameStride(nn.Module):
    """A convolution and a depthwise one over k frames at a stride of k, as ReDimNet2's stages shorten time."""

    def __init__(self, c: int, k: int) -> None:
        super().__init__()
        self.front = nn.Conv2d(c, c, 1)
        self.stride = nn.Conv2d(c, c, (k, 1), stride=(k, 1), groups=c)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.stride(self.front(x))


@pytest.mark.parametrize(("c", "k"), [(24, 2), (48, 4)])
def test_stride_depthwise_runs_a_frame_stride_as_one_op_giving_its_integers(tmp_path: Path, c: int, k: int) -> None:
    torch.manual_seed(c)
    rng = np.random.default_rng(c + k)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, c, 8, 6)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(FrameStride(c, k).eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    with esp_ppq_patches.applied(["stride_depthwise"]):
        path = export_espdl.export(graph, tmp_path / "stride.espdl")
    nodes, tensors = espdl_graph(path)
    ((_, (x_name, taps, bias)),) = [node for node in nodes.values() if node[0] == "StrideDepthwise"]
    cp = -(-c // 16) * 16
    assert tensors[taps].shape == (cp // 16, k, 16) and tensors[bias].shape == (cp,)
    info = path.with_suffix(".info").read_text()
    shift = int(re.search(r"= StrideDepthwise\[.*shift = (\d+)", info).group(1))
    (front,) = [op for op in graph.operations.values() if op.type == "Conv" and op.attributes.get("group", 1) == 1]
    values = TorchExecutor(graph, device="cpu").forward(inputs=calib[0], output_names=[front.outputs[0].name])
    x = torch.round(values[0] / 2.0 ** exponent(info, x_name)).to(torch.int64).numpy()[0].transpose(1, 2, 0)
    io = ptq_espdl.io_of(graph)
    want = ptq_espdl.Simulator(graph)(calib[0].numpy())[0].transpose(1, 2, 0) / 2.0**io.output_exponent
    weights = tensors[taps].transpose(1, 0, 2).reshape(k, cp)[:, :c].astype(np.int64)

    def strided(s: int) -> np.ndarray:
        acc = sum(x[i::k][: x.shape[0] // k] * weights[i] for i in range(k)) + tensors[bias][:c].astype(np.int64)
        return round_shift(acc, s)

    assert np.array_equal(strided(shift), np.round(want).astype(np.int64))
    assert not np.array_equal(strided(shift + 1), np.round(want).astype(np.int64))
