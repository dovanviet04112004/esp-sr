"""Quantise and export the ns candidates' step graphs (KEHOACH 3.9, 3.14). Run: python -m srpipe.tasks.ns.quant probe

probe builds RNNoise-16k and each NSNet-16k size with seeded random weights as a step graph whose GRU states are
inputs and outputs, quantises each with ESP-PPQ, and writes what ai_engine/test_apps/unit streams on board B (E9-T10):
one model image and, a record a net, the int8 input and output of every hop, simulated with the states fed back.
"""

from __future__ import annotations

import argparse
import math
import struct
from pathlib import Path

import numpy as np
import torch
from torch import nn

from srpipe.compress.quant import esp_ppq_patches, ptq_espdl
from srpipe.core.config import ML_ROOT, load_yaml
from srpipe.export import pack_models
from srpipe.tasks import ns
from srpipe.tasks.ns import model

PROBE_DIR = ML_ROOT.parent / "firmware" / "components" / "ai_engine" / "test_apps" / "unit" / "main" / "probe"
MODELS_FILE, STREAMS_FILE = "ns_models.bin", "ns_streams.bin"
LADDER = "ns"
ENTRIES = {"rnnoise16k": "ns_rn", "nsnet16k_s": "ns_nss", "nsnet16k_m": "ns_nsm", "nsnet16k_l": "ns_nsl"}
INPUT, OUTPUT = "x", "y"
# Count; then a record a net: entry name, hops, input dims, outputs a hop, GRU states, their values a hop, input and
# output exponents; then every hop's int8 input, output and states one after another, padded to four bytes.
STREAMS_HEAD = struct.Struct("<4sI")
STREAMS_MAGIC = b"SRNS"
RECORD = struct.Struct("<8sIIIIIii")
# ESP-PPQ's helper.save heads an .espdl with "EDL2", the encryption flag, the length and four pad bytes.
ESPDL_HEAD_BYTES = 16


class Step(nn.Module):
    """One hop of a candidate: normalised features and each GRU's state in; the net's output, then each state after
    the hop, out. RNNoise-16k's output is its band gains, then its speech probability."""

    def __init__(self, net: nn.Module) -> None:
        super().__init__()
        self.net = net

    def forward(self, x: torch.Tensor, *state: torch.Tensor) -> tuple[torch.Tensor, ...]:
        gains, logit, after = self.net.net(x, list(state))
        y = gains if logit is None else torch.cat([gains, torch.sigmoid(logit).unsqueeze(-1)], dim=-1)
        return (y, *after)


def state_sizes(net: nn.Module) -> list[int]:
    """Hidden units of each GRU, in the order net() takes their states."""
    return [m.hidden_size for m in net.modules() if isinstance(m, nn.GRU)]


def state_names(count: int) -> tuple[list[str], list[str]]:
    """The graph's names of each state going in and coming out: h<k> and h<k>_next."""
    return [f"h{k}" for k in range(count)], [f"h{k}_next" for k in range(count)]


def step_graph(name: str, cfg: dict, work: Path):
    """A candidate's quantised step graph, calibrated on N(0, 1) feature hops with the float net's states fed back,
    its ports, its state sizes and its feature dims; every state goes in and out at one exponent, as the board copies
    it as int8."""
    p = cfg["probe"]
    torch.manual_seed(p["seed"])
    net = model.build(cfg, name).eval()
    step, sizes, dims = Step(net).eval(), state_sizes(net), net.mean.numel()
    rng = np.random.default_rng(p["seed"])
    calib, state = [], [torch.zeros(1, 1, h) for h in sizes]
    with torch.no_grad():
        for x in torch.from_numpy(rng.standard_normal((p["calib_hops"], 1, 1, dims)).astype(np.float32)):
            calib.append((x, *state))
            _, *state = step(x, *state)
    ins, outs = state_names(len(sizes))
    graph = ptq_espdl.quantize_named(step, calib, work / name, ptq_espdl.ladder(LADDER), [INPUT, *ins], [OUTPUT, *outs])
    inputs, outputs = ptq_espdl.ports_of(graph)
    if [q.name for q in inputs] != [INPUT, *ins] or [q.name for q in outputs] != [OUTPUT, *outs]:
        raise ValueError(f"{name}: ESP-PPQ kept ports {inputs} and {outputs}")
    for into, out in zip(inputs[1:], outputs[1:], strict=True):
        if into.exponent != out.exponent:
            raise ValueError(f"{name}: {into.name} goes in at 2^{into.exponent} and out at 2^{out.exponent}")
    return graph, inputs, outputs, sizes, dims


def stream(
    graph, x_int8: np.ndarray, inputs: list, outputs: list, sizes: list[int], feed: bool
) -> tuple[np.ndarray, np.ndarray]:
    """Every hop's int8 output and int8 states after it (hops, values), from zero states, each hop's states fed to the
    next when feed, else zero again."""
    sim = ptq_espdl.Simulator(graph)
    state = [np.zeros((1, 1, h), dtype=np.float32) for h in sizes]
    ys, states = [], []
    for x in x_int8:
        out = sim.run(x.reshape(1, 1, -1).astype(np.float32) * np.float32(2.0 ** inputs[0].exponent), *state)
        held = []
        for value, port in zip(out, outputs, strict=True):
            q = ptq_espdl.to_int8(value, port.exponent)
            if not np.array_equal(q.astype(np.float32) * np.float32(2.0**port.exponent), value):
                raise ValueError(f"the simulated {port.name} is not on the int8 grid of its exponent")
            held.append(q.reshape(-1))
        ys.append(held[0])
        states.append(np.concatenate(held[1:]))
        state = out[1:] if feed else state
    return np.stack(ys), np.stack(states)


def stored_tests(espdl: Path) -> dict[str, bytes]:
    """The int8 test values ESP-PPQ stored in an .espdl for model->test(), inputs and outputs, by name."""
    from esp_ppq.parser.espdl.FlatBuffers.Dl.Model import Model

    graph = Model.GetRootAs(espdl.read_bytes()[ESPDL_HEAD_BYTES:], 0).Graph()
    tensors = [graph.TestInputsValue(i) for i in range(graph.TestInputsValueLength())]
    tensors += [graph.TestOutputsValue(i) for i in range(graph.TestOutputsValueLength())]
    out = {}
    for t in tensors:
        raw = b"".join(t.RawData(j).BytesAsNumpy().tobytes() for j in range(t.RawDataLength()))
        out[t.Name().decode()] = raw[: math.prod(int(d) for d in t.DimsAsNumpy())]
    return out


def probe_net(name: str, cfg: dict, work: Path) -> tuple[bytes, bytes]:
    """The .espdl of a candidate's step graph, and its record: every hop's int8 input and output, states fed back."""
    p = cfg["probe"]
    graph, inputs, outputs, sizes, dims = step_graph(name, cfg, work)
    x_int8 = ptq_espdl.to_int8(
        np.random.default_rng([p["seed"], 1]).standard_normal((p["hops"], dims)).astype(np.float32), inputs[0].exponent
    )
    y_int8, states = stream(graph, x_int8, inputs, outputs, sizes, feed=True)
    first = (x_int8[0].reshape(1, 1, -1).astype(np.float32) * np.float32(2.0 ** inputs[0].exponent),)
    with esp_ppq_patches.applied(p["esp_ppq_patches"][model.family(name)]):
        espdl = ptq_espdl.export(
            graph,
            work / name / f"{name}.espdl",
            test_input=first + tuple(np.zeros((1, 1, h), np.float32) for h in sizes),
        )
    stored = stored_tests(espdl)
    # ESP-PPQ ran the first hop from zero states for model->test(): equal bytes prove the layout the board reads.
    if stored[INPUT] != x_int8[0].tobytes() or stored[OUTPUT] != y_int8[0].tobytes():
        raise ValueError(f"{name}: the first hop differs from the test values ESP-PPQ stored for model->test()")
    head = RECORD.pack(
        ENTRIES[name].encode("ascii"),
        p["hops"],
        dims,
        y_int8.shape[1],
        len(sizes),
        states.shape[1],
        inputs[0].exponent,
        outputs[0].exponent,
    )
    body = x_int8.tobytes() + y_int8.tobytes() + states.tobytes()
    return espdl.read_bytes(), head + body + b"\0" * (-len(body) % 4)


def probe(cfg: dict, out: Path, work: Path) -> tuple[Path, Path]:
    """Write out/ns_models.bin and out/ns_streams.bin for every candidate of the run; work keeps each ONNX and
    .espdl."""
    built = [(ENTRIES[name], probe_net(name, cfg, work)) for name in model.names(cfg)]
    out.mkdir(parents=True, exist_ok=True)
    image = out / MODELS_FILE
    image.write_bytes(pack_models.pack([pack_models.Entry(entry, "espdl", espdl) for entry, (espdl, _) in built]))
    streams = out / STREAMS_FILE
    streams.write_bytes(STREAMS_HEAD.pack(STREAMS_MAGIC, len(built)) + b"".join(record for _, (_, record) in built))
    for entry, (espdl, _) in built:
        print(f"{entry}: {len(espdl)} bytes of .espdl")
    return image, streams


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("probe", help="write the stateful probe of E9-T10 for ai_engine/test_apps/unit")
    run.add_argument("--out", type=Path, default=PROBE_DIR)
    run.add_argument("--work", type=Path, default=ML_ROOT / "artifacts" / "ns" / "probe")
    args = parser.parse_args(argv)
    for path in probe(load_yaml(ns.CONFIG), args.out, args.work):
        print(f"{path}: {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
