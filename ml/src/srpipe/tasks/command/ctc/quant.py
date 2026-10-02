"""Quantise and export the ctc net (KEHOACH 3.12, 3.14, ADR-0013). Run: python -m srpipe.tasks.command.ctc.quant
probe [--run <run>] | export <run>. probe writes what ai_engine/test_apps/unit streams on board B (E11-T12): the first
stack's layer and the net, seeded random or a trained run, with each step's int8 input and output from the whole-
sequence simulation. export compares a trained run quantised on rung 1 with each calibration of rung 2 against float,
on the test set's unit error rate and Gate 3 over the board sessions.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
from pathlib import Path

import numpy as np
import torch
import yaml
from torch import nn

from srpipe.compress.quant import esp_ppq_patches, ptq_espdl
from srpipe.core import screen, splits
from srpipe.core.config import ML_ROOT, apply_overrides, data_paths, load_yaml
from srpipe.export import pack_models
from srpipe.tasks import command
from srpipe.tasks.command import ctc
from srpipe.tasks.command import eval as gate
from srpipe.tasks.command.ctc import train
from srpipe.tasks.command.ctc.model import encoder
from srpipe.tasks.command.ctc.postproc import ctc_score
from srpipe.tasks.wake.data import sentence_units

PROBE_DIR = ML_ROOT.parent / "firmware" / "components" / "ai_engine" / "test_apps" / "unit" / "main" / "probe"
MODELS_FILE, STREAMS_FILE, DECIDE_FILE = "ctc_models.bin", "ctc_streams.bin", "ctc_decide.bin"
LADDER = "command_ctc"
LAYER_ENTRY, NET_ENTRY = "ctc_lay", "ctc_net"
# Count; then a record a net: entry name, hops a step, input dims, outputs a step, steps, input and output exponents,
# then each step's int8 input, then each step's int8 output, in esp-dl's layout of each, padded to four bytes.
STREAMS_HEAD = struct.Struct("<4sI")
STREAMS_MAGIC = b"SRCT"
RECORD = struct.Struct("<8sIIIIii")
# ESP-PPQ's helper.save heads an .espdl with "EDL2", the encryption flag, the length and four pad bytes.
ESPDL_HEAD_BYTES = 16


def stored_test(espdl: Path) -> tuple[tuple[tuple[int, ...], bytes], tuple[tuple[int, ...], bytes]]:
    """The test input and output ESP-PPQ stored in an .espdl for model->test(), as dims and int8 bytes, both in the
    layout esp-dl holds the tensors in."""
    from esp_ppq.parser.espdl.FlatBuffers.Dl.Model import Model

    graph = Model.GetRootAs(espdl.read_bytes()[ESPDL_HEAD_BYTES:], 0).Graph()

    def dims_and_bytes(tensor) -> tuple[tuple[int, ...], bytes]:
        dims = tuple(int(d) for d in tensor.DimsAsNumpy())
        raw = b"".join(tensor.RawData(j).BytesAsNumpy().tobytes() for j in range(tensor.RawDataLength()))
        return dims, raw[: math.prod(dims)]

    return dims_and_bytes(graph.TestInputsValue(0)), dims_and_bytes(graph.TestOutputsValue(0))


def step_perm(channels: int, per_step: int, dims: tuple[int, ...]) -> tuple[int, ...]:
    """The axes that take a step from ONNX's (1, channels, time) to esp-dl's dims of it: kept, or time first."""
    if channels == per_step and channels > 1:
        raise ValueError(f"a step of {per_step} x {channels} reads the same in both layouts")
    for perm in ((0, 1, 2), (0, 2, 1)):
        if tuple((1, channels, per_step)[a] for a in perm) == dims:
            return perm
    raise ValueError(f"esp-dl holds a (1, {channels}, {per_step}) step as {dims}")


def steps_in_layout(x: np.ndarray, per_step: int, perm: tuple[int, ...]) -> bytes:
    """x (1, channels, time) as the int8 of one step after another, each with its axes in perm's order."""
    steps = (x[..., s : s + per_step] for s in range(0, x.shape[-1], per_step))
    return b"".join(np.ascontiguousarray(step.transpose(perm)).tobytes() for step in steps)


def probe_net(
    model: nn.Module, name: str, step_hops: int, cfg: dict, work: Path, calib: list[torch.Tensor], probe_x: np.ndarray
) -> tuple[bytes, bytes]:
    """The .espdl of model quantised on calib and streamed step_hops at a time, and its record: every step's int8
    input and output over probe_x (1, dims, hops)."""
    dims = probe_x.shape[1]
    shape = probe_x.shape
    graph = ptq_espdl.quantize(model.eval(), calib, work / name, ptq_espdl.ladder(LADDER))
    norms = sum(isinstance(m, encoder.ScalarRmsNorm) for m in model.modules())
    fused = sum(op.type == "RMSNormalization" for op in graph.operations.values())
    if fused != norms:
        raise ValueError(f"{name}: ESP-PPQ fused {fused} of {norms} norms into esp-dl's RMSNormalization")
    io = ptq_espdl.io_of(graph)
    x_int8 = ptq_espdl.to_int8(probe_x, io.input_exponent)
    x = x_int8.astype(np.float32) * np.float32(2.0**io.input_exponent)
    y = ptq_espdl.Simulator(graph)(x)
    y_int8 = ptq_espdl.to_int8(y, io.output_exponent)
    if not np.array_equal(y_int8.astype(np.float32) * np.float32(2.0**io.output_exponent), y):
        raise ValueError(f"{name}: the simulated output is not on the int8 grid of its exponent")
    steps = shape[2] // step_hops
    frames_per_step = y.shape[2] // steps
    with esp_ppq_patches.applied(cfg["esp_ppq_patches"]):
        espdl = ptq_espdl.export(
            graph,
            work / name / f"{name}.espdl",
            streaming_input_shape=[1, dims, step_hops],
            test_input=x[..., :step_hops],
        )
    (in_dims, in_test), (out_dims, out_test) = stored_test(espdl)
    x_steps = steps_in_layout(x_int8, step_hops, step_perm(dims, step_hops, in_dims))
    y_steps = steps_in_layout(y_int8, frames_per_step, step_perm(y.shape[1], frames_per_step, out_dims))
    # ESP-PPQ ran the first step alone for model->test(): equal bytes prove the layout and the int8 graph's causality.
    if x_steps[: len(in_test)] != in_test or y_steps[: len(out_test)] != out_test:
        raise ValueError(f"{name}: the first step differs from the test values ESP-PPQ stored for model->test()")
    outputs = frames_per_step * y.shape[1]
    head = RECORD.pack(name.encode("ascii"), step_hops, dims, outputs, steps, io.input_exponent, io.output_exponent)
    body = x_steps + y_steps
    return espdl.read_bytes(), head + body + b"\0" * (-len(body) % 4)


def draw_norm_scales(model: nn.Module, low: float, high: float) -> nn.Module:
    """Norm scales drawn as training leaves them: a scale of exactly one drops out of the ONNX graph, and ESP-PPQ then
    leaves the norm an int8 chain instead of fusing it."""
    for norm in (m for m in model.modules() if isinstance(m, encoder.ScalarRmsNorm)):
        nn.init.uniform_(norm.scale, low, high)
    return model


def drawn(cfg: dict, dims: int, count: int) -> list[np.ndarray]:
    """count random inputs (1, dims, probe hops) of the probe's seed, as normalised features stand."""
    p = cfg["probe"]
    rng = np.random.default_rng(p["seed"])
    return [rng.normal(p["input_mean"], p["input_std"], (1, dims, p["hops"])).astype(np.float32) for _ in range(count)]


def trained_net(cfg: dict, run: Path) -> tuple[nn.Module, list[torch.Tensor], np.ndarray]:
    """A trained run's net, its calibration as export draws it, and one test sentence it streams in the probe."""
    spec, paths = cfg["quant"], data_paths()
    net = gate.load_ctc(run)
    root = paths["processed"] / "command" / net.cfg["split"]["version"]
    listing = sorted((root / "test").glob("*.items.jsonl"))[0]
    item = json.loads(listing.read_text(encoding="utf-8").splitlines()[0])
    stem = str(listing).removesuffix(".items.jsonl")
    first, n = item["frame_offset"], min(item["n_frames"], spec["hops"])
    x = np.concatenate(
        [np.load(stem + s, mmap_mode="r")[first : first + n] for s in (".features.npy", ".pitch.npy")], 1
    )
    return net.model, calibration(net.cfg, spec, net.mean, net.std, root), padded(x, spec["hops"], net.mean, net.std)


def probe(cfg: dict, out: Path, work: Path, run: Path | None = None) -> tuple[Path, Path, Path]:
    """Write out/ctc_models.bin and out/ctc_streams.bin: the first stack's layer a frame a step, the net a chunk a
    step, work keeping each ONNX and .espdl; and out/ctc_decide.bin, the decision of the default commands (E11-T13).
    With run, the net is that trained run on its calibration of export, streaming a test sentence."""
    if cfg["probe"]["hops"] % cfg["chunk_hops"] or cfg["quant"]["hops"] % cfg["chunk_hops"]:
        raise ValueError(f"probe and quant hops must be multiples of chunk_hops {cfg['chunk_hops']}")
    scales, p = cfg["probe"]["norm_scale"], cfg["probe"]
    torch.manual_seed(p["seed"])
    first = cfg["model"]["stacks"][0]
    one_layer = draw_norm_scales(encoder.layer(cfg, first["kernel"]), *scales)
    *layer_calib, layer_x = drawn(cfg, cfg["model"]["width"], p["calib_sequences"] + 1)
    if run is None:
        torch.manual_seed(p["seed"])
        net = draw_norm_scales(encoder.build(cfg), *scales)
        *net_calib, net_x = drawn(cfg, encoder.n_dims(cfg), p["calib_sequences"] + 1)
        net_calib = [torch.from_numpy(c) for c in net_calib]
    else:
        net, net_calib, net_x = trained_net(cfg, run)
    built = [
        (
            LAYER_ENTRY,
            probe_net(one_layer, LAYER_ENTRY, 1, cfg, work, [torch.from_numpy(c) for c in layer_calib], layer_x),
        ),
        (NET_ENTRY, probe_net(net, NET_ENTRY, cfg["chunk_hops"], cfg, work, net_calib, net_x)),
    ]
    out.mkdir(parents=True, exist_ok=True)
    image = out / MODELS_FILE
    image.write_bytes(pack_models.pack([pack_models.Entry(name, "espdl", espdl) for name, (espdl, _) in built]))
    streams = out / STREAMS_FILE
    streams.write_bytes(STREAMS_HEAD.pack(STREAMS_MAGIC, len(built)) + b"".join(record for _, (_, record) in built))
    for name, (espdl, _) in built:
        print(f"{name}: {len(espdl)} bytes of .espdl")
    decide = out / DECIDE_FILE
    decide.write_bytes(ctc_score.probe_record(cfg))
    return image, streams, decide


def padded(x: np.ndarray, hops: int, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    """Raw features (n, dims) zero-padded at the end to hops, as training pads a batch, then normalised: (1, dims,
    hops)."""
    out = np.zeros((hops, x.shape[1]), dtype=np.float32)
    out[: len(x)] = x
    return ((out - mean) / std).T[None].astype(np.float32)


def calibration(trained: dict, spec: dict, mean: np.ndarray, std: np.ndarray, root: Path) -> list[torch.Tensor]:
    """spec's calib_sentences sentences of the run's train files under root, drawn with spec's seed among those within
    spec's hops, normalised and padded as the net reads them."""
    found = []
    for listing in sorted(root.glob("train_*/*.items.jsonl")):
        for line in listing.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if item["n_frames"] <= spec["hops"]:
                found.append((listing, item["frame_offset"], item["n_frames"]))
    picks = np.random.default_rng(spec["seed"]).choice(len(found), spec["calib_sentences"], replace=False)
    out = []
    for listing, first, n in (found[k] for k in sorted(picks)):
        stem = str(listing).removesuffix(".items.jsonl")
        mel = np.load(stem + ".features.npy", mmap_mode="r")[first : first + n]
        pitch = np.load(stem + ".pitch.npy", mmap_mode="r")[first : first + n]
        out.append(torch.from_numpy(padded(np.concatenate([mel, pitch], axis=1), spec["hops"], mean, std)))
    return out


class Int8Net:
    """A quantised graph called as the float net is, on normalised features (1, dims, hops) of at most the graph's
    hops: padded on as training pads, the input on its int8 grid as the chip takes it, logits as the chip gives
    them."""

    def __init__(self, graph, hops: int, mean: np.ndarray, std: np.ndarray, like: encoder.CtcNet) -> None:
        self.simulate, self.io, self.hops = ptq_espdl.Simulator(graph), ptq_espdl.io_of(graph), hops
        self.zero = (-mean / std).astype(np.float32)
        self.chunk_multiple, self.front = like.chunk_multiple, like.front

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        x = x.numpy()
        if x.shape[2] > self.hops:
            raise ValueError(f"{x.shape[2]} hops, the graph takes {self.hops}")
        whole = np.repeat(self.zero[None, :, None], self.hops, axis=2)
        whole[..., : x.shape[2]] = x
        exponent = np.float32(2.0**self.io.input_exponent)
        return torch.from_numpy(self.simulate(ptq_espdl.to_int8(whole, self.io.input_exponent) * exponent))


def unit_error_rate(net, data: train.Sentences, picks: np.ndarray, hops: int, mean: np.ndarray, std: np.ndarray):
    """Unit error rate of net's best path over the picked sentences, each padded to hops as the graph reads it."""
    errors = total = 0
    for k in picks:
        x = data.features[data.first[k] : data.first[k] + data.hops[k]]
        with torch.no_grad():
            logits = net(torch.from_numpy(padded(x, hops, mean, std)))
        frames = -(-int(data.hops[k]) // net.front.hop_stride)
        log_probs = logits.log_softmax(1)[0, :, :frames].numpy()
        errors += train.edit_distance(train.best_path(log_probs), data.units[k])
        total += len(data.units[k])
    return errors / total


def gate_row(net: gate.Ctc, windows: list[gate.Scored], reject: int, margin: int) -> dict:
    """Gate 3 of net on the board windows: utterances whose best command is right, those accepted right at reject and
    margin, and the false accepts among the rest."""
    heard = [(s.expected, gate.ctc_heard(net, x)) for s in windows for x in s.decided]
    commands = [(e, h) for e, h in heard if e != gate.REJECT]
    others = [h for e, h in heard if e == gate.REJECT]
    accepted = [(e, h) for e, h in commands if h.accepted(reject, margin)]
    return {
        "best_right": f"{sum(h.command == e for e, h in commands)}/{len(commands)}",
        "accepted_right": f"{sum(h.command == e for e, h in accepted)}/{len(commands)}",
        "false_accepts": f"{sum(h.accepted(reject, margin) for h in others)}/{len(others)}",
    }


def export(cfg: dict, run: Path) -> Path:
    """Rung 1 with each calibration of rung 2 against float: the test set's unit error rate and Gate 3 on the board
    sessions, into <run>/int8/ladder.yaml."""
    spec, paths = cfg["quant"], data_paths()
    net = gate.load_ctc(run)
    root = paths["processed"] / "command" / net.cfg["split"]["version"]
    calib = calibration(net.cfg, spec, net.mean, net.std, root)
    folder = paths["splits"] / "command" / net.cfg["split"]["version"]
    listed = {r.item for r in splits.read_split(folder / "test.txt")}
    units_of = sentence_units(
        screen.kept_clips(load_yaml(screen.CONFIG), paths, "speech"), listed, net.cfg["train"]["dialect"]
    )
    data = train.load_role([root / "test"], units_of, spec["hops"], "float32")
    rng = np.random.default_rng(spec["seed"])
    picks = np.sort(rng.choice(len(data.first), min(spec["test_sentences"], len(data.first)), replace=False))
    board_spec = load_yaml(command.CONFIG)["eval"]["board"]
    longest = round(cfg["window_s"] * gate.HOPS_PER_S)
    said = {c["id"]: c["text"] for c in json.loads(command.COMMANDS.read_text(encoding="utf-8"))["commands"]}

    def window_of(clean, features, spans, tracker):
        return gate.ctc_windows(clean, features, spans, longest, tracker)

    windows = gate.board(net.cfg, board_spec, paths, said, window_of)
    reject, margin = spec["reject"], cfg["eval"]["margin"]
    rows = {
        "float": {
            "unit_error_rate": round(unit_error_rate(net.model, data, picks, spec["hops"], net.mean, net.std), 4),
            **gate_row(net, windows, reject, margin),
        }
    }
    print(f"float: {rows['float']}", flush=True)
    rungs = ptq_espdl.ladder(LADDER)
    for name in spec["calibrations"]:
        graph = ptq_espdl.quantize(net.model, calib, run / "int8" / name, rungs | {"calibration": name})
        int8 = Int8Net(graph, spec["hops"], net.mean, net.std, net.model)
        int8_net = gate.Ctc(int8, net.mean, net.std, net.names, net.lexicon, net.cfg)
        rows[name] = {
            "unit_error_rate": round(unit_error_rate(int8, data, picks, spec["hops"], net.mean, net.std), 4),
            **gate_row(int8_net, windows, reject, margin),
        }
        print(f"{name}: {rows[name]}", flush=True)
    out = run / "int8" / "ladder.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    head = {"rungs": rungs, "quant": spec, "test_sentences": len(picks)}
    out.write_text(yaml.safe_dump(head | {"rows": rows}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("probe", help="write the streaming probe of E11-T12 for ai_engine/test_apps/unit")
    run.add_argument("--out", type=Path, default=PROBE_DIR)
    run.add_argument("--work", type=Path, default=ML_ROOT / "artifacts" / "command_ctc" / "probe")
    run.add_argument("--run", type=Path, help="stream this trained run's net instead of random weights")
    ladder_run = sub.add_parser("export", help="quantise a trained run on the ladder and compare it with float")
    ladder_run.add_argument("run", type=Path, help="a run directory of python -m srpipe.tasks.command.ctc.train")
    ladder_run.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    args = parser.parse_args(argv)
    if args.command == "export":
        print(export(apply_overrides(load_yaml(ctc.CONFIG), args.overrides), args.run))
        return 0
    for path in probe(load_yaml(ctc.CONFIG), args.out, args.work, args.run):
        print(f"{path}: {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
