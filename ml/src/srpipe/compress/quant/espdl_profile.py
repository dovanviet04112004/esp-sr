"""Step 2.2 of KEHOACH 3.14's procedure for a faster branch on the chip: esp-dl's per-module profile from a board log,
joined to the .espdl that ran and the .info written beside it, each module's time ranked and each slow path of esp-dl's
S3 kernels tagged with the export patch of esp_ppq_patches that handles it.
Run: python -m srpipe.compress.quant.espdl_profile --log <board log> --espdl <model.espdl> [--top N]"""

from __future__ import annotations

import argparse
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from srpipe.compress.quant import espdl_shapes
from srpipe.compress.quant.esp_ppq_patches import CHANNEL_STEP, DATA_CACHE_BYTES

CPU_MHZ = 240  # CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ of the firmware apps
MATMUL_STEP = 16  # dl_base_matmul.cpp: vector path, 16 output columns
LAYOUT_OPS = frozenset({"Transpose"})
GRID_OPS = frozenset({"QuantizeLinear", "RequantizeLinear", "DequantizeLinear"})
FLOAT_KERNEL_OPS = frozenset({"LayerNormalization", "Softmax"})
PROFILE_ROW = re.compile(r"\|\s*(?P<name>\S+)\s*\|\s*(?P<op>\w+)\s*\|\s*(?P<us>\d+)us\s*\|")
VALUE_INFO = re.compile(r"^%(?P<name>[^\[\s]+)\[(?P<dtype>[A-Z0-9]+), (?P<shape>[0-9x]*)\]", re.MULTILINE)


@dataclass
class Module:
    """One module of a profiled run: its op, time on the chip, and the .info's view of its variables."""

    name: str
    op: str
    us: int
    inputs: list[str]
    outputs: list[str]
    attrs: str


def profile(text: str) -> dict[str, tuple[str, int]]:
    """Each module's (op, µs) in the last profile esp-dl's profile_module printed into text."""
    return {m["name"]: (m["op"], int(m["us"])) for m in PROFILE_ROW.finditer(text)}


def nodes(espdl: Path) -> dict[str, tuple[str, list[str], list[str]]]:
    """Each node of an .espdl by name: (op, inputs, outputs)."""
    from esp_ppq.parser.espdl.FlatBuffers.Dl import Model

    graph = Model.Model.GetRootAs(espdl.read_bytes()[16:], 0).Graph()
    found = {}
    for i in range(graph.NodeLength()):
        node = graph.Node(i)
        ins = [node.Input(j).decode() for j in range(node.InputLength())]
        found[node.Name().decode()] = (
            node.OpType().decode(),
            ins,
            [node.Output(j).decode() for j in range(node.OutputLength())],
        )
    return found


def shapes(info_text: str, info: espdl_shapes.Info) -> dict[str, list[int]]:
    """Every variable's shape as the chip lays it, from the .info's value infos, inputs and initializers."""
    found = {m["name"]: espdl_shapes.dims(m["shape"], "x") for m in VALUE_INFO.finditer(info_text)}
    return found | info.inputs | info.declared


def conv_tags(module: Module, laid: dict[str, list[int]]) -> tuple[list[str], float | None]:
    """The slow paths of a convolution and its cycles a multiply-add, None when its shapes are unknown."""
    x, w, y = (laid.get(name) for name in (*module.inputs[:2], module.outputs[0]))
    if x is None or w is None or y is None:
        return [], None
    group, kernel = (
        espdl_shapes.attribute(module.attrs, "group", 1),
        espdl_shapes.attribute(module.attrs, "kernel_shape"),
    )
    c_in, c_out, taps = x[-1], y[-1], math.prod(kernel or [1])
    macs = math.prod(y) * (c_in // group) * taps
    tags = []
    if c_in % CHANNEL_STEP or c_out % CHANNEL_STEP:
        strides = espdl_shapes.attribute(module.attrs, "strides", [1] * len(kernel or [1]))
        columns = kernel is not None and set(kernel[:-1]) <= {1} and strides[-1] == kernel[-1]
        fix = "aligned_pointwise" if taps == 1 or (columns and group == 1) else "no export patch"
        tags.append(f"unaligned {c_in} -> {c_out} channels ({fix})")
    weight_bytes = math.prod(w)
    if group == 1 and weight_bytes > DATA_CACHE_BYTES and math.prod(y[:-1]) > 1:
        tags.append(f"{weight_bytes // 1024} KB of weights outgrow the data cache (cache_sized_convolutions)")
    return tags, module.us * CPU_MHZ / max(macs, 1)


def tags_of(
    module: Module, laid: dict[str, list[int]], readers: dict[str, list[str]]
) -> tuple[list[str], float | None]:
    """The slow paths module takes on esp-dl's S3 kernels, each with the patch that handles it, and its cycles a
    multiply-add when it is a convolution."""
    if module.op == "Conv":
        return conv_tags(module, laid)
    tags = []
    if "quant_type = 'F32'" in module.attrs:
        tags.append("runs in float")
    if module.op in FLOAT_KERNEL_OPS:
        tags.append("esp-dl's int8 kernel works in float32 per value (no export patch)")
    if module.op == "MatMul" and (y := laid.get(module.outputs[0])) and y[-1] < MATMUL_STEP:
        tags.append(f"{y[-1]} output columns run in C (lay the product transposed)")
    if module.op in LAYOUT_OPS:
        tags.append("layout copy (lean_transposes)")
    if module.op in GRID_OPS:
        tags.append("grid change (lean_quantizers when repeated)")
    if module.op == "Concat" and len(set(module.inputs)) == 1 and readers.get(module.outputs[0], []) == ["Transpose"]:
        tags.append("copies of one map (copies_by_broadcast)")
    return tags, None


def report(log_text: str, espdl: Path, top: int) -> str:
    """The profile in log_text of the model espdl as text: time by op type, the slowest modules with their tags."""
    info_text = espdl.with_suffix(".info").read_text(encoding="utf-8")
    graph, timed = nodes(espdl), profile(log_text)
    laid = shapes(info_text, espdl_shapes.parse(info_text))
    attrs = {outs[0]: a for _, a, _, outs in espdl_shapes.parse(info_text).nodes}
    readers = defaultdict(list)
    for op, ins, _ in graph.values():
        for name in ins:
            readers[name].append(op)
    modules = [
        Module(name, op, us, graph[name][1], graph[name][2], attrs.get(graph[name][2][0], ""))
        for name, (op, us) in timed.items()
        if name in graph
    ]
    by_op = defaultdict(lambda: [0, 0])
    for m in modules:
        by_op[m.op][0] += 1
        by_op[m.op][1] += m.us
    lines = [f"{sum(m.us for m in modules) / 1000:.1f} ms over {len(modules)} modules of {espdl.name}", "by op type:"]
    lines += [
        f"  {op:22s} {n:4d} {us / 1000:8.1f} ms" for op, (n, us) in sorted(by_op.items(), key=lambda kv: -kv[1][1])
    ]
    lines.append(f"slowest {top} modules:")
    for m in sorted(modules, key=lambda m: -m.us)[:top]:
        tags, rate = tags_of(m, laid, readers)
        cycles = f"{rate:5.2f} c/MAC " if rate is not None else ""
        lines.append(f"  {m.us / 1000:7.1f} ms {m.op:18s} {cycles}{m.name}" + "".join(f"\n      - {t}" for t in tags))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--espdl", type=Path, required=True)
    parser.add_argument("--top", type=int, default=40)
    args = parser.parse_args(argv)
    print(report(args.log.read_text(encoding="utf-8", errors="replace"), args.espdl, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
