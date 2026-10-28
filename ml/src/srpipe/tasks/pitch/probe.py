"""The board probe of SwiftF0's network (KEHOACH 3.11 step 2, E11-T37): a row of <run>/int8/ladder.yaml as
ai_engine/test_apps/unit runs it on board B over one fixed window of quant.CHUNK_HOPS frames, its graph and probe.cuts
cuts in a model image; the window is the loudest chunk of a val session simulated again, kept with every output the
simulation gives, each in esp-dl's layout. Run: python -m srpipe.tasks.pitch.probe --run <run> --row <row>
[--cut-range <a> <b>] [--cuts <n>]"""

from __future__ import annotations

import argparse
import itertools
import math
import struct
from pathlib import Path

import numpy as np

from srpipe.compress.quant import export_espdl, ptq_espdl
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


def window(cfg: dict, run: Path, paths: dict) -> np.ndarray:
    """(1, SPECTRA, BANDS, CHUNK_HOPS) spectra of the loudest chunk of a val session of quant.build, drawn with the
    seed and simulated again."""
    q = cfg["quant"]
    rng = np.random.default_rng([q["seed"], PROBE_STREAM])
    (clean,) = quant.drawn_cleans(paths, q["build"], ["val"], 1, rng)
    windows, bands = quant.front_tables(run / quant.MODEL_FILE)
    return max(quant.chunks(swift_front.spectra(clean, windows, bands)), key=lambda c: float(c.mean()))[None]


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


def laid_out(values: np.ndarray, dims: tuple[int, ...], raw: bytes) -> bool:
    """raw holds values with their axes in some order that gives esp-dl's dims."""
    return any(
        values.transpose(p).shape == dims and np.ascontiguousarray(values.transpose(p)).tobytes() == raw
        for p in itertools.permutations(range(values.ndim))
    )


def probe(
    cfg: dict, run: Path, row: str, out: Path, spectra: np.ndarray, span: tuple[int, int] | None = None
) -> tuple[Path, Path]:
    """Write out/pitch_models.bin and out/pitch_window.bin of the run's row over spectra, one window, with the cuts of
    probe.cuts within span among the image's entries; the .espdl files stay beside the row's graph. Refused when
    ESP-PPQ stored other values for model->test() than the simulation gives, or a port's name outgrows its field."""
    folder = run / quant.INT8_DIR / row
    graph = export_espdl.load_native(folder / quant.GRAPH_FILE)
    (port_in,), ports_out = ptq_espdl.ports_of(graph)
    x = ptq_espdl.to_int8(spectra, port_in.exponent).astype(np.float32) * np.float32(2.0**port_in.exponent)
    ys = ptq_espdl.Simulator(graph).run(x)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", type=Path, required=True, help="a run of python -m srpipe.tasks.pitch.quant import")
    parser.add_argument("--row", required=True, help="the row of <run>/int8/ladder.yaml whose graph the board runs")
    parser.add_argument("--cut-range", type=int, nargs=2, help="topological op indices the cuts are spread between")
    parser.add_argument("--cuts", type=int, help="cuts of the graph in the image; probe.cuts otherwise")
    parser.add_argument("--out", type=Path, default=PROBE_DIR)
    args = parser.parse_args(argv)
    cfg = load_yaml(quant.CONFIG)
    if args.cuts is not None:
        cfg = cfg | {"probe": cfg["probe"] | {"cuts": args.cuts}}
    span = tuple(args.cut_range) if args.cut_range else None
    for path in probe(cfg, args.run, args.row, args.out, window(cfg, args.run, data_paths()), span):
        print(f"{path}: {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
