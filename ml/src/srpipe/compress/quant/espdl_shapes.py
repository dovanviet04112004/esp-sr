"""Step 4's check before flashing (KEHOACH 3.14): esp-dl 3.3's get_output_shape replayed over the .info ESP-PPQ writes
beside an .espdl. esp-dl broadcasts by the larger size of each axis without checking, takes a grouped convolution's
output channels from its input and asserts on a non-positive dimension while it builds the model, so a graph ESP-PPQ
simulates well can come out wrong or not load on the chip. Ops the replay does not know leave their outputs unknown.
"""

from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

ELEMENTWISE = {"Add", "Sub", "Mul", "Div", "Pow"}
SAME_SHAPE = {
    "Relu", "Gelu", "Tanh", "Sigmoid", "Swish", "Sqrt", "Clip", "LUT", "Softmax", "LayerNormalization",
    "QuantizeLinear", "DequantizeLinear", "RequantizeLinear", "Identity",
}  # fmt: skip
REDUCE = {"ReduceMean", "ReduceSum", "ReduceMax", "ReduceMin"}
SHAPE_PARAMS_MAX = 8  # longer values are weights, never shapes or axes
NODE = re.compile(r"^  %(?P<outs>[^ ]+(?:, %[^ ]+)*) = (?P<op>[A-Za-z]+)\[(?P<attrs>.*?)\]\((?P<ins>.*)\)$")
DECLARED = re.compile(r"^  %(?P<name>[^\[]+)\[(?P<dtype>[A-Z0-9]+), (?P<shape>[0-9x]*)\]")
VALUE_HEAD = re.compile(r"^%(?P<name>[^,]+), shape: \[(?P<shape>[^\]]*)\]")
VALUE = re.compile(r"value: array\((\[[^\]]*\])")


@dataclass
class Info:
    """The parts of an .info the replay reads: graph inputs and initializers by shape, small initializers by value."""

    inputs: dict[str, list[int]] = field(default_factory=dict)
    declared: dict[str, list[int]] = field(default_factory=dict)
    values: dict[str, list] = field(default_factory=dict)
    nodes: list[tuple[str, str, list[str], list[str]]] = field(default_factory=list)


def dims(text: str, sep: str) -> list[int]:
    return [int(v) for v in text.split(sep) if v.strip()]


def parse(text: str) -> Info:
    """The graph of an .info as ESP-PPQ's exporter prints it."""
    info, lines, section = Info(), text.splitlines(), None
    for i, line in enumerate(lines):
        if line.startswith("graph "):
            section = "inputs"
        elif line.startswith(") initializers"):
            section = "declared"
        elif line.startswith(") {"):
            section = "nodes"
        elif section in ("inputs", "declared") and (m := DECLARED.match(line)):
            (info.inputs if section == "inputs" else info.declared)[m["name"]] = dims(m["shape"], "x")
        elif section == "nodes" and (m := NODE.match(line)):
            outs = [o.strip().lstrip("%") for o in m["outs"].split(",")]
            ins = [x.strip().lstrip("%") for x in m["ins"].split(",")]
            info.nodes.append((m["op"], m["attrs"], ins, outs))
        elif (
            (m := VALUE_HEAD.match(line))
            and (count := math.prod(dims(m["shape"], ","))) <= SHAPE_PARAMS_MAX
            and (v := VALUE.search(" ".join(lines[i : i + 3])))
        ):
            info.values[m["name"]] = ast.literal_eval(re.sub(r"\s+", "", v.group(1)).replace(",]", "]"))[:count]
    return info


def attribute(attrs: str, key: str, default=None):
    m = re.search(rf"\b{key} = (\[[^\]]*\]|'[^']*'|[-0-9.e]+)", attrs)
    return ast.literal_eval(m.group(1)) if m else default


def broadcast(a: list[int], b: list[int]) -> tuple[list[int], bool]:
    """esp-dl's shape of an elementwise pair, the larger size of each right-aligned axis, and whether ONNX allows it."""
    rank = max(len(a), len(b))
    pa, pb = [1] * (rank - len(a)) + a, [1] * (rank - len(b)) + b
    return [max(x, y) for x, y in zip(pa, pb, strict=True)], all(
        x == y or 1 in (x, y) for x, y in zip(pa, pb, strict=True)
    )


def output_shape(op: str, attrs: str, shapes: list, values: list) -> list[int] | None:
    """The output shape esp-dl's module computes for op, None when the replay does not know op or an input."""
    x = shapes[0]
    if x is None:
        return None
    if op in SAME_SHAPE:
        return list(x)
    if op == "Transpose":
        return [x[p] for p in attribute(attrs, "perm")]
    if op == "Slice" and None not in values[1:4]:
        out = list(x)
        steps = values[4] if len(values) > 4 and values[4] is not None else [1] * len(values[3])
        for axis, start, end, step in zip(values[3], values[1], values[2], steps, strict=True):
            a = axis % len(x)
            start, end = max(0, min(start, x[a])), min(end, x[a])
            out[a] = (end - start + step - 1) // step
        return out
    if op in REDUCE and values[1] is not None:
        axes = {a % len(x) for a in values[1]}
        if attribute(attrs, "keepdims", 1):
            return [1 if i in axes else d for i, d in enumerate(x)]
        return [d for i, d in enumerate(x) if i not in axes] or [1]
    if op == "Unsqueeze" and values[1] is not None:
        axes = {a + len(x) + 1 if a < 0 else a for a in values[1]}
        rest = iter(x)
        return [1 if i in axes else next(rest) for i in range(len(x) + len(axes))]
    if op == "Concat" and None not in shapes:
        axis = attribute(attrs, "axis") % len(x)
        return [sum(s[axis] for s in shapes) if i == axis else d for i, d in enumerate(x)]
    if op == "Conv" and shapes[1] is not None:
        w, pads, strides = shapes[1], attribute(attrs, "pads"), attribute(attrs, "strides")
        dilations, group = attribute(attrs, "dilations"), attribute(attrs, "group")
        out = list(x)
        out[1] = (x[1] + pads[0] + pads[1] - dilations[0] * (w[0] - 1) - 1) // strides[0] + 1
        if len(x) == 4:
            out[2] = (x[2] + pads[2] + pads[3] - dilations[1] * (w[1] - 1) - 1) // strides[1] + 1
        out[-1] = w[-1] if group == 1 else x[-1]
        return out
    if op == "Resize" and len(values) > 2 and values[2] is not None:
        out = list(x)
        out[1] = int(x[1] * values[2][2])
        if len(x) == 4:
            out[2] = int(x[2] * values[2][3])
        return out
    if op == "MatMul" and shapes[1] is not None:
        return broadcast(x[:-2], shapes[1][:-2])[0] + [x[-2], shapes[1][-1]]
    if op == "Gemm" and shapes[1] is not None:
        return [x[0], shapes[1][0] if attribute(attrs, "transB", 0) else shapes[1][-1]]
    return None


def problems(info: Info) -> list[str]:
    """Each place the chip's shapes break: a non-positive dimension, a broadcast ONNX refuses, a Reshape of another
    size."""
    shapes, found = dict(info.inputs), []
    for op, attrs, ins, outs in info.nodes:
        known = [shapes.get(n, info.declared.get(n)) for n in ins]
        given = [info.values.get(n) for n in ins]
        if op in ELEMENTWISE and None not in known[:2]:
            out, allowed = broadcast(known[0], known[1])
            if not allowed:
                found.append(f"{outs[0]}: {op} broadcasts {known[0]} with {known[1]}")
        elif op == "Reshape" and known[0] is not None and given[1] is not None:
            size, shape = math.prod(known[0]), [known[0][i] if d == 0 else d for i, d in enumerate(given[1])]
            if -1 in shape:
                shape[shape.index(-1)] = size // math.prod(d for d in shape if d != -1)
            out = shape
            if math.prod(shape) != size:
                found.append(f"{outs[0]}: Reshape of {known[0]} to {given[1]}")
        else:
            out = output_shape(op, attrs, known, given)
        if out is not None and any(d <= 0 for d in out):
            found.append(f"{outs[0]}: {op} gives {out}")
            out = None
        shapes[outs[0]] = out
    return found


def check(info_file: Path) -> None:
    """Refuse the exported graph whose .info is info_file when esp-dl cannot build it as ESP-PPQ simulates it."""
    found = problems(parse(info_file.read_text(encoding="utf-8")))
    if found:
        raise ValueError(f"{info_file}: esp-dl would build it otherwise: " + "; ".join(found[:5]))
