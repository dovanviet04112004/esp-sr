"""esp_ppq_patches.ops.softmax_quant: each Softmax with its quantiser, and attention's scaled scores, as
SoftmaxQuant."""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from esp_ppq.executor.torch import TorchExecutor  # noqa: E402

from esp_ppq_cases import (  # noqa: E402
    BANDS,
    HOPS,
    RUNGS,
    ScaledScores,
    SoftmaxReadTwice,
    espdl_graph,
    exponent,
    export_info,
)
from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl  # noqa: E402


def quantiser_exponent(info: str) -> int:
    """The exponent of the int8 variable the one Softmax-reading QuantizeLinear of info writes."""
    (out,) = re.findall(r"^  %([^\s=]+) = QuantizeLinear\[.*Softmax", info, re.MULTILINE)
    return exponent(info, out)


def test_softmax_quant_exports_each_softmax_and_its_quantiser_as_one_op(tmp_path: Path) -> None:
    torch.manual_seed(2)
    rng = np.random.default_rng(15)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, HOPS, BANDS)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(SoftmaxReadTwice().eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    lean = export_info(graph, tmp_path / "lean.espdl", ["lean_quantizers"])
    joined = export_info(graph, tmp_path / "joined.espdl", ["lean_quantizers", "softmax_quant"])
    (out,) = re.findall(r"^  %([^\s=]+) = SoftmaxQuant\[", joined, re.MULTILINE)
    assert not re.findall(r"= (?:Softmax|QuantizeLinear)\[", joined)
    assert exponent(joined, out) == quantiser_exponent(lean)
    assert len(re.findall(rf"\(%{re.escape(out)}[,)]|, %{re.escape(out)}[,)]", joined)) == 2


def test_softmax_scale_looks_each_score_up_as_esp_dls_mul_scales_it(tmp_path: Path) -> None:
    torch.manual_seed(2)
    rng = np.random.default_rng(15)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, HOPS, BANDS)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(ScaledScores().eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    patches = ["lean_quantizers", "softmax_quant"]
    assert len(re.findall(r"= Mul\[", export_info(graph, tmp_path / "plain.espdl", patches))) == 1
    with esp_ppq_patches.applied([*patches, "softmax_scale"]):
        nodes, tensors = espdl_graph(export_espdl.export(graph, tmp_path / "scaled.espdl"))
    ((_, (_, table)),) = [node for node in nodes.values() if node[0] == "SoftmaxQuant"]
    assert "Mul" not in {op for op, _ in nodes.values()}

    (mul,) = [op for op in graph.operations.values() if op.type == "Mul"]
    scores = next(v for v in mul.inputs if not v.is_parameter)
    constant = next(v for v in mul.inputs if v.is_parameter)
    s_in, s_c = (float(mul.input_quant_config[mul.inputs.index(v)].scale) for v in (scores, constant))
    s_out = float(mul.output_quant_config[0].scale)
    values = TorchExecutor(graph, device="cpu").forward(
        inputs=calib[0], output_names=[scores.name, mul.outputs[0].name]
    )
    given, made = (
        torch.round(v / s).to(torch.int64).numpy().ravel() for v, s in zip(values, (s_in, s_out), strict=True)
    )
    bytes_in = given.astype(np.int8).view(np.uint8)
    assert np.array_equal(tensors[table][bytes_in], made)
    c, shift = round(float(constant.value) / s_c), round(math.log2(s_out / (s_in * s_c)))
    assert not np.array_equal(esp_ppq_patches.products_by_byte(c, shift + 1)[bytes_in], made)


def test_softmax_quant_leaves_a_softmax_two_quantisers_read(tmp_path: Path) -> None:
    torch.manual_seed(2)
    rng = np.random.default_rng(15)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, HOPS, BANDS)).astype(np.float32)) for _ in range(2)]
    graph = ptq_espdl.quantize(SoftmaxReadTwice().eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    info = export_info(graph, tmp_path / "twice.espdl", ["softmax_quant"])
    assert len(re.findall(r"= Softmax\[", info)) == 1
    assert not re.findall(r"= SoftmaxQuant\[", info)
    assert len(re.findall(r"= QuantizeLinear\[.*Softmax", info)) == 2
