"""esp_ppq_patches.ops.attention: each attention's core as AttentionCore."""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from esp_ppq.executor.torch import TorchExecutor  # noqa: E402
from torch import nn  # noqa: E402

from esp_ppq_cases import BANDS, HOPS, RUNGS, ScaledScores, espdl_graph, export_info, round_shift  # noqa: E402
from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl  # noqa: E402


class Attention(nn.Module):
    """Four heads of six over the hops in the form ReDimNet2 b0's attention exports in: scores of keys [H, T, D] by
    queries [H, D, T], a softmax over the keys, values [H, D, T] weighed by it, the heads laid back. The keys project
    a ReLU of the input, or onnxsim's fuse_qkv joins the three projections and trips on the graph."""

    def __init__(self) -> None:
        super().__init__()
        self.keys, self.queries, self.values = (nn.Linear(24, 24) for _ in range(3))
        self.out = nn.Linear(24, 24)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        t = x.shape[1]
        k = self.keys(torch.relu(x)).reshape(1, t, 4, 6).permute(0, 2, 1, 3).reshape(4, t, 6)
        q = self.queries(x).reshape(1, t, 4, 6).permute(0, 2, 3, 1).reshape(4, 6, t)
        v = self.values(x).reshape(1, t, 4, 6).permute(0, 2, 3, 1).reshape(4, 6, t)
        p = torch.softmax(torch.matmul(k, q) * 0.41, dim=1)
        return self.out(torch.matmul(v, p).reshape(1, 4, 6, t).permute(0, 3, 1, 2).reshape(1, t, 24))


def test_attention_core_joins_the_heads_scores_softmax_and_weighing(tmp_path: Path) -> None:
    torch.manual_seed(5)
    rng = np.random.default_rng(24)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, HOPS, 24)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(Attention().eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    patches = ["lean_quantizers", "softmax_quant", "softmax_scale", "matmul_s8"]
    with esp_ppq_patches.applied([*patches, "attention_core"]):
        path = export_espdl.export(graph, tmp_path / "attention.espdl")
    nodes = espdl_graph(path)[0]
    ops = [op for op, _ in nodes.values()]
    assert ops.count("AttentionCore") == 1 and not {"MatMul", "Transpose", "SoftmaxQuant"} & set(ops)
    info = path.with_suffix(".info").read_text()
    (attrs, ins) = re.search(r"= AttentionCore\[(.*?)\]\((.*?)\)", info).groups()
    assert "heads = 4" in attrs and len(ins.split(", ")) == 4
    made = dict(re.findall(r"%(\S+) = (\w+)\[", info))
    assert [made[v.strip().lstrip("%")] for v in ins.split(", ")[:3]] == ["MatMulS8"] * 3

    (scores_op, weigh) = [
        op for op in graph.topological_sort() if op.type == "MatMul" and not op.inputs[1].is_parameter
    ]
    k, q, v = scores_op.inputs[0], scores_op.inputs[1], weigh.inputs[0]
    names = [k.name, q.name, v.name, scores_op.outputs[0].name, weigh.outputs[0].name]
    values = TorchExecutor(graph, device="cpu").forward(inputs=calib[0], output_names=names)
    configs = [*scores_op.input_quant_config, weigh.input_quant_config[0], *scores_op.output_quant_config]
    configs.append(weigh.output_quant_config[0])
    ints = [torch.round(x / float(c.scale)).to(torch.int64).numpy() for x, c in zip(values, configs, strict=True)]
    shift = round(math.log2(float(configs[3].scale) / (float(configs[0].scale) * float(configs[1].scale))))
    assert np.array_equal(np.clip(round_shift(ints[0] @ ints[1], shift), -128, 127), ints[3])
    assert not np.array_equal(np.clip(round_shift(ints[0] @ ints[1], shift + 1), -128, 127), ints[3])


def test_attention_core_leaves_a_softmax_over_the_last_axis(tmp_path: Path) -> None:
    torch.manual_seed(2)
    rng = np.random.default_rng(15)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, HOPS, BANDS)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(ScaledScores().eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    patches = ["lean_quantizers", "softmax_quant", "softmax_scale", "attention_core"]
    info = export_info(graph, tmp_path / "last_axis.espdl", patches)
    assert not re.findall(r"= AttentionCore\[", info) and len(re.findall(r"= SoftmaxQuant\[", info)) == 1
