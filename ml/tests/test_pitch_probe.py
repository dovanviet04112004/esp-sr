"""The pitch probe's window record: the input then every output of a two-output graph, each at its exponent in the
layout esp-dl holds it in, the values the simulation gives; refused when the stored test values are not those."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from torch import nn  # noqa: E402

from srpipe.compress.quant import export_espdl, ptq_espdl  # noqa: E402
from srpipe.tasks.pitch import probe, quant  # noqa: E402

SPECTRA, BANDS, FRAMES = 3, 8, 16
ROW = "kl"
CFG = {"probe": {"runs": 3, "cuts": 1}}


class TwoHeads(nn.Module):
    """Three spectra in; a map over bands and frames, and one value a frame, out, as SwiftF0's network gives them."""

    def __init__(self) -> None:
        super().__init__()
        self.conv = nn.Conv2d(SPECTRA, 4, 3, padding=1)
        self.pitch = nn.Conv2d(4, 1, 1)
        self.sure = nn.Conv1d(4, 1, 1)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        h = torch.relu(self.conv(x))
        return self.pitch(h)[:, 0], self.sure(h.amax(dim=2))[:, 0]


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    torch.manual_seed(0)
    rng = np.random.default_rng(0)
    calib = [torch.from_numpy(rng.normal(0, 1, (1, SPECTRA, BANDS, FRAMES)).astype(np.float32)) for _ in range(4)]
    folder = tmp_path_factory.mktemp("run")
    work = folder / quant.INT8_DIR / ROW
    graph = ptq_espdl.quantize(TwoHeads().eval(), calib, work, ptq_espdl.ladder(quant.LADDER))
    export_espdl.save_native(graph, work / quant.GRAPH_FILE)
    return folder


def ports(record: bytes) -> tuple[tuple, list[tuple[str, int, bytes]]]:
    head = probe.HEAD.unpack_from(record)
    at, found = probe.HEAD.size, []
    for _ in range(head[3]):
        name, count, exponent = probe.PORT.unpack_from(record, at)
        at += probe.PORT.size
        found.append((name.rstrip(b"\0").decode(), exponent, record[at : at + count]))
        at += count + (-count % 4)
    assert at == len(record)
    return head, found


def test_the_record_holds_the_input_then_each_output_as_the_simulation_gives_them(run: Path, tmp_path: Path) -> None:
    spectra = np.random.default_rng(1).normal(0, 1, (1, SPECTRA, BANDS, FRAMES)).astype(np.float32)
    image, record = probe.probe(CFG, run, ROW, tmp_path, spectra)
    graph = export_espdl.load_native(run / quant.INT8_DIR / ROW / quant.GRAPH_FILE)
    (port_in,), ports_out = ptq_espdl.ports_of(graph)
    x_int8 = ptq_espdl.to_int8(spectra, port_in.exponent)
    ys = ptq_espdl.Simulator(graph).run(x_int8.astype(np.float32) * np.float32(2.0**port_in.exponent))
    head, found = ports(record.read_bytes())
    assert head == (probe.MAGIC, 3, FRAMES, 3)
    assert found[0] == (port_in.name, port_in.exponent, np.ascontiguousarray(x_int8.transpose(0, 2, 3, 1)).tobytes())
    by_name = {name: (exponent, raw) for name, exponent, raw in found[1:]}
    for port, y in zip(ports_out, ys, strict=True):
        assert by_name[port.name] == (port.exponent, ptq_espdl.to_int8(y, port.exponent).tobytes())
    assert image.stat().st_size > 0


def test_stored_values_other_than_the_simulation_are_refused(
    run: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = probe.stored_ports

    def one_value_off(espdl: Path) -> list[tuple[str, tuple[int, ...], bytes]]:
        found = stored(espdl)
        name, dims, raw = found[-1]
        found[-1] = (name, dims, struct.pack("b", struct.unpack_from("b", raw)[0] ^ 1) + raw[1:])
        return found

    monkeypatch.setattr(probe, "stored_ports", one_value_off)
    spectra = np.random.default_rng(1).normal(0, 1, (1, SPECTRA, BANDS, FRAMES)).astype(np.float32)
    with pytest.raises(ValueError, match="other values"):
        probe.probe(CFG, run, ROW, tmp_path, spectra)
