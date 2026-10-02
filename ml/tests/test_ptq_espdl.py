"""The rungs of quant.yaml reach ESP-PPQ's setting, a branch overrides the default ones, and the quantised graph's
int8 sits on the power-of-two grid of its exponents."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from srpipe.compress.quant import ptq_espdl  # noqa: E402
from srpipe.tasks.wake.model.tcn import Tcn  # noqa: E402

BANDS, HOPS = 8, 40
RUNGS = ptq_espdl.ladder("wake")


def calib(rng: np.random.Generator) -> list:
    return [torch.from_numpy(rng.normal(0, 1, (1, BANDS, HOPS)).astype(np.float32)) for _ in range(4)]


def test_the_simulated_output_sits_on_the_int8_grid_of_its_exponent(tmp_path: Path) -> None:
    torch.manual_seed(0)
    graph = ptq_espdl.quantize(Tcn(BANDS, 8, 3, [1, 2]), calib(np.random.default_rng(0)), tmp_path, RUNGS)
    io = ptq_espdl.io_of(graph)
    x = ptq_espdl.to_int8(np.random.default_rng(1).normal(0, 1, (1, BANDS, HOPS)), io.input_exponent)
    y = ptq_espdl.Simulator(graph)(x.astype(np.float32) * np.float32(2.0**io.input_exponent))
    on_grid = ptq_espdl.to_int8(y, io.output_exponent).astype(np.float32) * np.float32(2.0**io.output_exponent)
    assert np.array_equal(on_grid, y) and (tmp_path / "graph.onnx").is_file()


def test_the_ladder_reaches_the_esp_ppq_setting() -> None:
    setting = ptq_espdl.setting_of({**RUNGS, "int16_ops": ["/out/Conv"]})
    eq = RUNGS["equalization"]
    assert setting.equalization and setting.bias_correct == RUNGS["bias_correction"]
    assert (setting.equalization_setting.iterations, setting.equalization_setting.value_threshold) == (
        eq["iterations"],
        eq["value_threshold"],
    )
    assert setting.quantize_activation_setting.calib_algorithm == RUNGS["calibration"]
    assert "/out/Conv" in setting.dispatching_table.dispatchings
    assert not ptq_espdl.setting_of({**RUNGS, "equalization": None}).equalization


def test_a_branch_overrides_the_default_rungs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ladder = tmp_path / "quant.yaml"
    ladder.write_text(
        "default: {equalization: null, bias_correction: true, calibration: kl, int16_ops: []}\n"
        "branches: {wake: {calibration: mse}, ns: {}}\n"
    )
    monkeypatch.setattr(ptq_espdl, "LADDER", ladder)
    assert ptq_espdl.ladder("wake")["calibration"] == "mse"
    assert ptq_espdl.ladder("ns")["calibration"] == "kl"
