"""The board probe of SwiftF0's network (KEHOACH 3.11 steps 2 and 3.2, E11-T37): a row of <run>/int8/ladder.yaml
in a model image for ai_engine/test_apps/unit on board B, its graph with probe.cuts cuts, or with --stream exported
to stream that many frames a call; and one window, quant.CHUNK_HOPS frames or the first --frames, from the loudest
chunk of a val session simulated again, with every output the simulation gives, each in esp-dl's layout. Run: python
-m srpipe.tasks.pitch.probe --run <run> --row <row> [--frames <n>] [--cut-range <a> <b>] [--cuts <n>] [--stream <n>]"""

from __future__ import annotations

import argparse
import itertools
import math
import struct
from pathlib import Path

import numpy as np

from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl
from srpipe.core.config import ML_ROOT, data_paths, load_yaml
from srpipe.dsp.spec import swift_front
from srpipe.export import pack_models
from srpipe.tasks.command.ctc.probe import ESPDL_HEAD_BYTES
from srpipe.tasks.pitch import quant
from srpipe.tasks.speaker.probe import cut_points, truncated

PROBE_DIR = ML_ROOT.parent / "firmware" / "components" / "ai_engine" / "test_apps" / "unit" / "main" / "probe"
MODELS_FILE, WINDOW_FILE = "pitch_models.bin", "pitch_window.bin"
ENTRY = "pitch"
CUT_ENTRY = "pitch_c{}"
PROBE_STREAM = 3  # rng stream of the drawn session, beside quant's
# Timed runs, frames of the window, ports; then each port, the input first: graph name, int8 values, exponent, then
# the values in esp-dl's layout, padded to four bytes.
HEAD = struct.Struct("<4sIII")
MAGIC = b"SRPP"
PORT = struct.Struct("<32sIi")
PORT_NAME_BYTES = 32
# The streaming record: the same head, then each port as PORT followed by the frames it trails the input.
STREAM_MAGIC = b"SRPS"
STREAM_PORT = struct.Struct("<32sIii")


def window(cfg: dict, run: Path, paths: dict, frames: int = quant.CHUNK_HOPS) -> np.ndarray:
    """(1, SPECTRA, BANDS, frames) spectra: the first frames of the loudest chunk of a val session of quant.build,
    drawn with the seed and simulated again."""
    q = cfg["quant"]
    rng = np.random.default_rng([q["seed"], PROBE_STREAM])
    (clean,) = quant.drawn_cleans(paths, q["build"], ["val"], 1, rng)
    windows, bands = quant.front_tables(run / quant.MODEL_FILE)
    loudest = max(quant.chunks(swift_front.spectra(clean, windows, bands)), key=lambda c: float(c.mean()))
    return loudest[None, ..., :frames]


def stored_ports(espdl: Path) -> list[tuple[str, tuple[int, ...], bytes]]:
    """Each test input, then each test output, ESP-PPQ stored in an .espdl for model->test(): graph name, dims and
    int8 bytes, in the layout esp-dl holds the tensor in."""
    from esp_ppq.parser.espdl.FlatBuffers.Dl.Model import Model

    graph = Model.GetRootAs(espdl.read_bytes()[ESPDL_HEAD_BYTES:], 0).Graph()

    def port(tensor) -> tuple[str, tuple[int, ...], bytes]:
        dims = tuple(int(d) for d in tensor.DimsAsNumpy())
        raw = b"".join(tensor.RawData(j).BytesAsNumpy().tobytes() for j in range(tensor.RawDataLength()))
        return tensor.Name().decode("ascii"), dims, raw[: math.prod(dims)]

    tensors = [graph.TestInputsValue(j) for j in range(graph.TestInputsValueLength())]
    return [port(t) for t in tensors + [graph.TestOutputsValue(j) for j in range(graph.TestOutputsValueLength())]]


def layout_of(values: np.ndarray, dims: tuple[int, ...], raw: bytes) -> tuple[int, ...] | None:
    """The order of values' axes in which raw holds them as esp-dl's dims, None when none does."""
    for p in itertools.permutations(range(values.ndim)):
        if values.transpose(p).shape == dims and np.ascontiguousarray(values.transpose(p)).tobytes() == raw:
            return p
    return None


def laid_out(values: np.ndarray, dims: tuple[int, ...], raw: bytes) -> bool:
    """raw holds values with their axes in some order that gives esp-dl's dims."""
    return layout_of(values, dims, raw) is not None


def probe(
    cfg: dict, run: Path, row: str, out: Path, spectra: np.ndarray, span: tuple[int, int] | None = None
) -> tuple[Path, Path]:
    """Write out/pitch_models.bin and out/pitch_window.bin of the run's row over spectra, one window of any frames,
    with the cuts of probe.cuts within span among the image's entries, all exported under the branch's esp_ppq_patches;
    the .espdl files stay beside the row's graph. Refused when ESP-PPQ stored other values for model->test() than the
    simulation gives, or a port's name outgrows its field."""
    folder, patches = run / quant.INT8_DIR / row, cfg["esp_ppq_patches"]
    graph = export_espdl.load_native(folder / quant.GRAPH_FILE)
    (port_in,), ports_out = ptq_espdl.ports_of(graph)
    quant.framed(graph, spectra.shape[-1])
    x = ptq_espdl.to_int8(spectra, port_in.exponent).astype(np.float32) * np.float32(2.0**port_in.exponent)
    ys = ptq_espdl.Simulator(graph, patches).run(x)
    with esp_ppq_patches.applied(patches):
        espdl = export_espdl.export(graph, folder / f"{ENTRY}.espdl", test_input=x)
    wanted = {p.name: (p, v) for p, v in [(port_in, x), *zip(ports_out, ys, strict=True)]}
    stored = stored_ports(espdl)
    if sorted(name for name, _, _ in stored) != sorted(wanted) or stored[0][0] != port_in.name:
        raise ValueError(f"{espdl}: test values of {[name for name, _, _ in stored]}, the graph's {list(wanted)}")
    body = b""
    for name, dims, raw in stored:
        port, value = wanted[name]
        values = ptq_espdl.to_int8(value, port.exponent)
        if not np.array_equal(values.astype(np.float32) * np.float32(2.0**port.exponent), value):
            raise ValueError(f"{name}: the simulation is not on the int8 grid of its exponent")
        if not laid_out(values, dims, raw):
            raise ValueError(f"{name}: ESP-PPQ stored other values for model->test() than the simulation gives")
        if len(name.encode("ascii")) >= PORT_NAME_BYTES:
            raise ValueError(f"{name}: longer than the {PORT_NAME_BYTES - 1} bytes the board reads")
        body += PORT.pack(name.encode("ascii"), len(raw), port.exponent) + raw + b"\0" * (-len(raw) % 4)
    entries = [pack_models.Entry(ENTRY, "espdl", espdl.read_bytes())]
    for k, (index, name) in enumerate(cut_points(graph, cfg["probe"]["cuts"], span)):
        with esp_ppq_patches.applied(patches):
            part = export_espdl.export(truncated(graph, name), folder / f"{CUT_ENTRY.format(k)}.espdl", test_input=x)
        entries.append(pack_models.Entry(CUT_ENTRY.format(k), "espdl", part.read_bytes()))
        print(f"{CUT_ENTRY.format(k)}: op {index}, {name}, {part.stat().st_size} bytes of .espdl")
    out.mkdir(parents=True, exist_ok=True)
    image = out / MODELS_FILE
    image.write_bytes(pack_models.pack(entries))
    record = out / WINDOW_FILE
    record.write_bytes(HEAD.pack(MAGIC, cfg["probe"]["runs"], spectra.shape[-1], len(stored)) + body)
    print(f"{ENTRY}: {espdl.stat().st_size} bytes of .espdl")
    return image, record


def probe_stream(cfg: dict, run: Path, row: str, out: Path, spectra: np.ndarray, step: int) -> tuple[Path, Path]:
    """Write out/pitch_models.bin with the run's row exported to stream step frames a call, under the branch's
    esp_ppq_patches and band_stream, and out/pitch_window.bin with spectra, a window of a whole number of calls, and
    every output the simulation of that window gives, each in esp-dl's layout with the frames it trails the input.
    Refused when ESP-PPQ stored other values for model->test() than the simulation gives, which the layouts come
    from, or a port's name outgrows its field."""
    folder, patches = run / quant.INT8_DIR / row, cfg["esp_ppq_patches"]
    frames = spectra.shape[-1]
    if frames % step:
        raise ValueError(f"{frames} frames are not a whole number of calls of {step}")
    graph = export_espdl.load_native(folder / quant.GRAPH_FILE)
    (port_in,), ports_out = ptq_espdl.ports_of(graph)
    quant.framed(graph, frames)
    x = ptq_espdl.to_int8(spectra, port_in.exponent).astype(np.float32) * np.float32(2.0**port_in.exponent)
    ys = ptq_espdl.Simulator(graph, patches).run(x)
    quant.framed(graph, step)
    first = x[..., :step]
    firsts = ptq_espdl.Simulator(graph, patches).run(first)
    with esp_ppq_patches.applied([*patches, quant.STREAM_PATCH]):
        espdl = export_espdl.export(graph, folder / f"{ENTRY}.espdl", test_input=first)
    stored = {name: (dims, raw) for name, dims, raw in stored_ports(espdl)}
    delays = quant.output_delays(espdl.with_suffix(".info").read_text(), [p.name for p in ports_out])
    body = b""
    for port, value, value_first, delay in [(port_in, x, first, 0), *zip(ports_out, ys, firsts, delays, strict=True)]:
        dims, raw = stored[port.name]
        order = layout_of(ptq_espdl.to_int8(value_first, port.exponent), dims, raw)
        if order is None:
            raise ValueError(f"{port.name}: ESP-PPQ stored other values for model->test() than the simulation gives")
        if len(port.name.encode("ascii")) >= PORT_NAME_BYTES:
            raise ValueError(f"{port.name}: longer than the {PORT_NAME_BYTES - 1} bytes the board reads")
        laid = np.ascontiguousarray(ptq_espdl.to_int8(value, port.exponent).transpose(order)).tobytes()
        body += STREAM_PORT.pack(port.name.encode("ascii"), len(laid), port.exponent, delay)
        body += laid + b"\0" * (-len(laid) % 4)
    out.mkdir(parents=True, exist_ok=True)
    image = out / MODELS_FILE
    image.write_bytes(pack_models.pack([pack_models.Entry(ENTRY, "espdl", espdl.read_bytes())]))
    record = out / WINDOW_FILE
    record.write_bytes(HEAD.pack(STREAM_MAGIC, cfg["probe"]["runs"], frames, 1 + len(ports_out)) + body)
    print(f"{ENTRY}: {espdl.stat().st_size} bytes of .espdl, {step} frames a call, outputs {delays} frames behind")
    return image, record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, required=True, help="a run of python -m srpipe.tasks.pitch.quant import")
    parser.add_argument("--row", required=True, help="the row of <run>/int8/ladder.yaml whose graph the board runs")
    parser.add_argument("--cut-range", type=int, nargs=2, help="topological op indices the cuts are spread between")
    parser.add_argument("--cuts", type=int, help="cuts of the graph in the image; probe.cuts otherwise")
    parser.add_argument("--frames", type=int, default=quant.CHUNK_HOPS, help="frames of the window, its first ones")
    parser.add_argument("--stream", type=int, help="frames a call: export the graph to stream instead")
    parser.add_argument("--out", type=Path, default=PROBE_DIR)
    args = parser.parse_args(argv)
    cfg = load_yaml(quant.CONFIG)
    if args.cuts is not None:
        cfg = cfg | {"probe": cfg["probe"] | {"cuts": args.cuts}}
    span = tuple(args.cut_range) if args.cut_range else None
    spectra = window(cfg, args.run, data_paths(), args.frames)
    if args.stream:
        written = probe_stream(cfg, args.run, args.row, args.out, spectra, args.stream)
    else:
        written = probe(cfg, args.run, args.row, args.out, spectra, span)
    for path in written:
        print(f"{path}: {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
