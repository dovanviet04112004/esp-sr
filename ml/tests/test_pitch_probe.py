"""The pitch probe's window record: the input then every output of a two-output graph, each at its exponent in the
layout esp-dl holds it in, the values the simulation gives, over the frames the graph was quantised on or fewer;
refused when the stored test values are not those. Its streaming record holds the same over the whole window, each
output with the frames it trails the input, for a graph exported to stream a few frames a call."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from torch import nn  # noqa: E402

from esp_ppq_cases import RUNGS, TAIL_FRAMES, Tail, tail_calib  # noqa: E402
from srpipe.compress.quant import export_espdl, ptq_espdl  # noqa: E402
from srpipe.tasks.pitch import probe, quant  # noqa: E402

SPECTRA, BANDS, FRAMES = 3, 8, 16
ROW = "kl"
CFG = {"esp_ppq_patches": [], "probe": {"runs": 3, "cuts": 1}}
STEP = 8
STREAM_CFG = {"esp_ppq_patches": ["aligned_channels", "band_stem", "band_head"], "probe": {"runs": 3, "cuts": 1}}


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


@pytest.mark.parametrize("frames", [FRAMES, FRAMES // 2])
def test_the_record_holds_the_input_then_each_output_as_the_simulation_gives_them(
    run: Path, tmp_path: Path, frames: int
) -> None:
    spectra = np.random.default_rng(1).normal(0, 1, (1, SPECTRA, BANDS, frames)).astype(np.float32)
    image, record = probe.probe(CFG, run, ROW, tmp_path, spectra)
    graph = export_espdl.load_native(run / quant.INT8_DIR / ROW / quant.GRAPH_FILE)
    (port_in,), ports_out = ptq_espdl.ports_of(graph)
    x_int8 = ptq_espdl.to_int8(spectra, port_in.exponent)
    ys = ptq_espdl.Simulator(graph).run(x_int8.astype(np.float32) * np.float32(2.0**port_in.exponent))
    head, found = ports(record.read_bytes())
    assert head == (probe.MAGIC, 3, frames, 3)
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


@pytest.fixture(scope="module")
def stream_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, np.ndarray]:
    torch.manual_seed(17)
    calib = tail_calib(np.random.default_rng(43))
    folder = tmp_path_factory.mktemp("stream_run")
    work = folder / quant.INT8_DIR / ROW
    graph = ptq_espdl.quantize(Tail().eval(), calib, work, RUNGS | {"calibration": "minmax"})
    export_espdl.save_native(graph, work / quant.GRAPH_FILE)
    return folder, calib[0].numpy()


def stream_ports(record: bytes) -> tuple[tuple, list[tuple[str, int, int, bytes]]]:
    head = probe.HEAD.unpack_from(record)
    at, found = probe.HEAD.size, []
    for _ in range(head[3]):
        name, count, exponent, delay = probe.STREAM_PORT.unpack_from(record, at)
        at += probe.STREAM_PORT.size
        found.append((name.rstrip(b"\0").decode(), exponent, delay, record[at : at + count]))
        at += count + (-count % 4)
    assert at == len(record)
    return head, found


def test_the_stream_record_holds_the_window_and_each_output_with_the_frames_it_trails(
    stream_run: tuple[Path, np.ndarray], tmp_path: Path
) -> None:
    folder, spectra = stream_run
    image, record = probe.probe_stream(STREAM_CFG, folder, ROW, tmp_path, spectra, STEP)
    graph = export_espdl.load_native(folder / quant.INT8_DIR / ROW / quant.GRAPH_FILE)
    (port_in,), ports_out = ptq_espdl.ports_of(graph)
    x_int8 = ptq_espdl.to_int8(spectra, port_in.exponent)
    x = x_int8.astype(np.float32) * np.float32(2.0**port_in.exponent)
    ys = ptq_espdl.Simulator(graph, STREAM_CFG["esp_ppq_patches"]).run(x)
    head, found = stream_ports(record.read_bytes())
    assert head == (probe.STREAM_MAGIC, 3, TAIL_FRAMES, 3)
    laid_x = np.ascontiguousarray(x_int8.transpose(0, 2, 3, 1)).tobytes()
    assert found[0] == (port_in.name, port_in.exponent, 0, laid_x)
    by_name = {name: (exponent, delay, raw) for name, exponent, delay, raw in found[1:]}
    assert [by_name[port.name][1] for port in ports_out] == [2, 3]
    for port, y in zip(ports_out, ys, strict=True):
        exponent, _, raw = by_name[port.name]
        assert (exponent, raw) == (port.exponent, ptq_espdl.to_int8(y, port.exponent).tobytes())
    assert image.stat().st_size > 0


def test_a_stream_window_of_part_of_a_call_is_refused(stream_run: tuple[Path, np.ndarray], tmp_path: Path) -> None:
    folder, spectra = stream_run
    with pytest.raises(ValueError, match="whole number"):
        probe.probe_stream(STREAM_CFG, folder, ROW, tmp_path, spectra[..., : TAIL_FRAMES - 1], STEP)
