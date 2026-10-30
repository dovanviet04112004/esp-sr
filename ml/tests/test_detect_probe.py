"""The detect probe's expectations as ai_engine's test app reads them: records the sizes its static_assert holds, each
output channels last on its producer's grid, and the tap of a block its last op's output."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("numba")

from srpipe.tasks.detect import probe  # noqa: E402


def test_records_have_the_sizes_test_detect_probe_cpp_asserts() -> None:
    assert (probe.HEAD.size, probe.OUTPUT.size, probe.PICTURE.size) == (16, 44, 16)


def test_outputs_of_lays_each_output_channels_last_on_its_producers_grid() -> None:
    value = torch.arange(-6, 6, dtype=torch.float32).reshape(1, 3, 2, 2) * 0.25
    var = SimpleNamespace()
    var.source_op = SimpleNamespace(outputs=[var], output_quant_config=[SimpleNamespace(scale=0.25, num_of_bits=16)])
    graph = SimpleNamespace(outputs={"head": var})
    executor = SimpleNamespace(forward=lambda x: [value])
    ((name, q, exponent),) = probe.outputs_of(executor, graph, np.zeros((4, 4, 3), np.uint8))
    assert name == "head" and exponent == -2 and q.dtype == np.int16
    assert np.array_equal(q, np.arange(-6, 6).reshape(3, 2, 2).transpose(1, 2, 0))


def test_block_output_is_the_last_op_of_the_block_in_graph_order() -> None:
    names = ("/model.1/a", "/model.10/b", "/model.1/c")
    ops = [SimpleNamespace(name=n, outputs=[SimpleNamespace(name=f"{n}_out")]) for n in names]
    graph = SimpleNamespace(topological_sort=lambda: ops)
    assert probe.block_output(graph, 1) == "/model.1/c_out"
    with pytest.raises(ValueError):
        probe.block_output(graph, 2)
