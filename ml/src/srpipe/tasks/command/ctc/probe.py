"""The board probe of the ctc net (E11-T12, E11-T19). Run: python -m srpipe.tasks.command.ctc.probe [--run <run>
--row <row>]. It writes what ai_engine/test_apps/unit runs on board B: the first stack's layer and the net, seeded
random or the graph of a row of a trained run's ladder (quant.py), with each step's int8 input and output from the
whole-sequence simulation; the decision of the default commands (E11-T13); and windows of raw features through the
command calls, each with the decision Python takes on its int8 simulation.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
from pathlib import Path

import numpy as np
import torch
from torch import nn

from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl
from srpipe.core.config import ML_ROOT, data_paths, load_yaml
from srpipe.export import pack_models
from srpipe.generated import listen
from srpipe.tasks.command import ctc
from srpipe.tasks.command import eval as gate
from srpipe.tasks.command.ctc import quant
from srpipe.tasks.command.ctc.model import encoder
from srpipe.tasks.command.ctc.postproc import ctc_score

PROBE_DIR = ML_ROOT.parent / "firmware" / "components" / "ai_engine" / "test_apps" / "unit" / "main" / "probe"
MODELS_FILE, STREAMS_FILE, DECIDE_FILE = "ctc_models.bin", "ctc_streams.bin", "ctc_decide.bin"
WINDOWS_FILE = "ctc_windows.bin"
LAYER_ENTRY, NET_ENTRY = "ctc_lay", quant.ENTRY
# Count; then a record a net: entry name, hops a step, input dims, outputs a step, steps, input and output exponents,
# then each step's int8 input, then each step's int8 output, in esp-dl's layout of each, padded to four bytes.
STREAMS_HEAD = struct.Struct("<4sI")
STREAMS_MAGIC = b"SRCT"
RECORD = struct.Struct("<16sIIIIii")
# Magic, features a hop, windows, reject, margin, commands, most variants, longest variant, hops a chunk; then the
# packed lexicon; then from a four-byte boundary each window's hops, its raw features and its DECISION_RECORD.
WINDOWS_HEAD = struct.Struct("<4sHHHHBBBB")
WINDOWS_MAGIC = b"SRCW"
GATE_FILE, GATE_LABELS = "ctc_gate.bin", "ctc_gate.json"
# WINDOWS_HEAD's fields and the input exponent; the mean then the std of each feature; the packed lexicon; then from a
# four-byte boundary each window's hops, its int8 input (hops, features) padded to four bytes, and its DECISION_RECORD.
GATE_HEAD = struct.Struct("<4sHHHHBBBBb3x")
GATE_MAGIC = b"SRCG"
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


def probe_net(graph, name: str, step_hops: int, cfg: dict, work: Path, probe_x: np.ndarray) -> tuple[bytes, bytes]:
    """The .espdl of graph streamed step_hops at a time, and its record: every step's int8 input and output over
    probe_x (1, dims, hops)."""
    dims = probe_x.shape[1]
    io = ptq_espdl.io_of(graph)
    x_int8 = ptq_espdl.to_int8(probe_x, io.input_exponent)
    x = x_int8.astype(np.float32) * np.float32(2.0**io.input_exponent)
    y = ptq_espdl.Simulator(graph, cfg["esp_ppq_patches"])(x)
    y_int8 = ptq_espdl.to_int8(y, io.output_exponent)
    if not np.array_equal(y_int8.astype(np.float32) * np.float32(2.0**io.output_exponent), y):
        raise ValueError(f"{name}: the simulated output is not on the int8 grid of its exponent")
    steps = probe_x.shape[2] // step_hops
    frames_per_step = y.shape[2] // steps
    with esp_ppq_patches.applied(cfg["esp_ppq_patches"]):
        espdl = export_espdl.export(
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


def random_windows(cfg: dict, mean: np.ndarray, std: np.ndarray) -> list[np.ndarray]:
    """Windows of random raw features with the probe's seed, standing as normalised ones: the longest LENH keeps, one
    hop, one chunk, and lengths drawn between."""
    p = cfg["probe"]
    rng = np.random.default_rng(p["seed"])
    longest = listen.WINDOW_HOPS
    lengths = [longest, 1, cfg["chunk_hops"], *rng.integers(2, longest, p["command"]["windows"] - 3)]
    return [rng.normal(mean, std, (int(n), len(mean))).astype(np.float32) for n in lengths]


def command_windows(cfg: dict, graph, model: encoder.CtcNet, norm: tuple, windows: list[np.ndarray]) -> bytes:
    """Raw feature windows (hops, features) as ai_engine_command_{begin,step,score} take them on board B, each with
    the decision Python takes on the int8 simulation of the window, laid out as WINDOWS_HEAD says."""
    mean, std = norm
    reject, margin = cfg["quant"]["reject"], cfg["eval"]["margin"]
    lexicon = ctc_score.default_lexicon()
    (commands, most, longest), packed = ctc_score.packed_lexicon(lexicon)
    int8 = quant.Int8Net(graph, cfg["quant"]["hops"], mean, std, model, cfg["esp_ppq_patches"])
    sizes = (commands, most, longest, cfg["chunk_hops"])
    body = WINDOWS_HEAD.pack(WINDOWS_MAGIC, len(mean), len(windows), reject, margin, *sizes)
    body += packed + b"\0" * (-(WINDOWS_HEAD.size + len(packed)) % 4)
    for x in windows:
        logits = int8(torch.from_numpy(((x - mean) / std).T[None].astype(np.float32))).numpy()[0]
        frames = -(-len(x) // model.front.hop_stride)
        q = ptq_espdl.to_int8(logits[:, :frames], int8.io.output_exponent)
        log_probs = ctc_score.frame_log_probs(q, int8.io.output_exponent)
        decision, _ = ctc_score.decide(log_probs, lexicon, reject, margin)
        body += struct.pack("<I", len(x)) + np.ascontiguousarray(x, dtype="<f4").tobytes()
        body += ctc_score.DECISION_RECORD.pack(*decision.tolist())
    return body


def gate_windows(cfg: dict, graph, model: encoder.CtcNet, norm: tuple, windows: list[np.ndarray]) -> bytes:
    """Every Gate 3 window as its int8 input, the normalised features on the graph's input grid that _step quantises
    raw features to, each with the decision Python takes on the int8 simulation, laid out as GATE_HEAD says; the test
    rebuilds raw features from it with the mean and std the record carries, so the chip sees the same int8."""
    mean, std = norm
    reject, margin = cfg["quant"]["reject"], cfg["eval"]["margin"]
    lexicon = ctc_score.default_lexicon()
    (commands, most, longest), packed = ctc_score.packed_lexicon(lexicon)
    int8 = quant.Int8Net(graph, cfg["quant"]["hops"], mean, std, model, cfg["esp_ppq_patches"])
    e = int8.io.input_exponent
    sizes = (commands, most, longest, cfg["chunk_hops"])
    body = GATE_HEAD.pack(GATE_MAGIC, len(mean), len(windows), reject, margin, *sizes, e)
    body += np.concatenate([mean, std]).astype("<f4").tobytes() + packed
    body += b"\0" * (-len(body) % 4)
    for x in windows:
        normalised = ((x - mean) / std).T[None].astype(np.float32)
        logits = int8(torch.from_numpy(normalised)).numpy()[0]
        frames = -(-len(x) // model.front.hop_stride)
        q = ptq_espdl.to_int8(logits[:, :frames], int8.io.output_exponent)
        decision, _ = ctc_score.decide(ctc_score.frame_log_probs(q, int8.io.output_exponent), lexicon, reject, margin)
        hops = np.ascontiguousarray(ptq_espdl.to_int8(normalised, e)[0].T).tobytes()
        body += struct.pack("<I", len(x)) + hops + b"\0" * (-len(hops) % 4)
        body += ctc_score.DECISION_RECORD.pack(*decision.tolist())
    return body


def probe(cfg: dict, out: Path, work: Path, run: Path | None = None, row: str | None = None) -> tuple[Path, ...]:
    """Write out/ctc_models.bin and out/ctc_streams.bin: the first stack's layer a frame a step, the net a chunk a
    step, work keeping each ONNX and .espdl; out/ctc_decide.bin, the decision of the default commands (E11-T13); and
    out/ctc_windows.bin, windows through the command calls. With run, the net is the graph of row in its ladder,
    streaming a test sentence, and the windows are the board's; out/ctc_gate.bin then holds every Gate 3 window and
    out/ctc_gate.json what each should get, for the on-chip Gate 3, and without run neither is left."""
    if cfg["probe"]["hops"] % cfg["chunk_hops"] or cfg["quant"]["hops"] % cfg["chunk_hops"]:
        raise ValueError(f"probe and quant hops must be multiples of chunk_hops {cfg['chunk_hops']}")
    scales, p, rungs = cfg["probe"]["norm_scale"], cfg["probe"], ptq_espdl.ladder(quant.LADDER)
    torch.manual_seed(p["seed"])
    one_layer = draw_norm_scales(encoder.layer(cfg, cfg["model"]["stacks"][0]["kernel"]), *scales)
    *layer_calib, layer_x = drawn(cfg, cfg["model"]["width"], p["calib_sequences"] + 1)
    if run is None:
        torch.manual_seed(p["seed"])
        net = draw_norm_scales(encoder.build(cfg), *scales)
        *net_calib, net_x = drawn(cfg, encoder.n_dims(cfg), p["calib_sequences"] + 1)
        dims = encoder.n_dims(cfg)
        norm = (np.full(dims, p["input_mean"], np.float32), np.full(dims, p["input_std"], np.float32))
        windows = random_windows(cfg, *norm)
    else:
        trained = gate.load_ctc(run)
        net, net_x, norm = trained.model, quant.test_sentence(cfg, trained), (trained.mean, trained.std)
        sessions = quant.board_windows(cfg, trained, data_paths())
        board = [x for scored in sessions for x in scored.decided]
        expected = [{"session": s.session, "expected": s.expected} for s in sessions for _ in s.decided]
        names = trained.names
        windows = board[:: max(1, len(board) // p["command"]["windows"])][: p["command"]["windows"]]
    fixes = cfg["esp_ppq_patches"]
    layer_graph = quant.quantized(
        one_layer, [torch.from_numpy(c) for c in layer_calib], work / LAYER_ENTRY, rungs, fixes
    )
    built = [(LAYER_ENTRY, probe_net(layer_graph, LAYER_ENTRY, 1, cfg, work, layer_x))]
    if run is None:
        net_graph = quant.quantized(net, [torch.from_numpy(c) for c in net_calib], work / NET_ENTRY, rungs, fixes)
    else:
        net_graph = export_espdl.load_native(run / "int8" / row / quant.GRAPH_FILE)
    built.append((NET_ENTRY, probe_net(net_graph, NET_ENTRY, cfg["chunk_hops"], cfg, work, net_x)))
    out.mkdir(parents=True, exist_ok=True)
    image = out / MODELS_FILE
    entries = [pack_models.Entry(name, "espdl", espdl) for name, (espdl, _) in built]
    entries.append(pack_models.Entry(NET_ENTRY, "norm", np.concatenate(norm).astype("<f4").tobytes()))
    image.write_bytes(pack_models.pack(entries))
    streams = out / STREAMS_FILE
    streams.write_bytes(STREAMS_HEAD.pack(STREAMS_MAGIC, len(built)) + b"".join(record for _, (_, record) in built))
    for name, (espdl, _) in built:
        print(f"{name}: {len(espdl)} bytes of .espdl")
    decide = out / DECIDE_FILE
    decide.write_bytes(ctc_score.probe_record(cfg))
    command = out / WINDOWS_FILE
    command.write_bytes(command_windows(cfg, net_graph, net, norm, windows))
    every, labels = out / GATE_FILE, out / GATE_LABELS
    if run is None:
        every.unlink(missing_ok=True)
        labels.unlink(missing_ok=True)
        return image, streams, decide, command
    every.write_bytes(gate_windows(cfg, net_graph, net, norm, board))
    labels.write_text(json.dumps({"names": names, "windows": expected}, ensure_ascii=False), encoding="utf-8")
    return image, streams, decide, command, every, labels


def gate_on_chip(log: Path, labels: Path) -> dict:
    """Gate 3 counted on the chip's own decisions, as the unit app prints one for each window of ctc_gate.bin."""
    meta = json.loads(labels.read_text(encoding="utf-8"))
    decided = {
        int(w): int(c) for w, c in re.findall(r"gate window (\d+): board (-?\d+)", log.read_text(errors="replace"))
    }
    if sorted(decided) != list(range(len(meta["windows"]))):
        raise ValueError(f"{log}: {len(decided)} gate windows, the record holds {len(meta['windows'])}")
    names = meta["names"]
    commands = [(w["expected"], decided[k]) for k, w in enumerate(meta["windows"]) if w["expected"] != gate.REJECT]
    others = [decided[k] for k, w in enumerate(meta["windows"]) if w["expected"] == gate.REJECT]
    right = sum(c != ctc_score.REJECTED and names[c] == e for e, c in commands)
    accepted = sum(c != ctc_score.REJECTED for c in others)
    return {"accepted_right": f"{right}/{len(commands)}", "false_accepts": f"{accepted}/{len(others)}"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=PROBE_DIR)
    parser.add_argument("--work", type=Path, default=ML_ROOT / "artifacts" / "command_ctc" / "probe")
    parser.add_argument("--run", type=Path, help="stream a row of this trained run's ladder instead of random weights")
    parser.add_argument("--row", help="the row of <run>/int8/ladder.yaml whose graph streams")
    parser.add_argument("--gate-log", type=Path, help="count Gate 3 on the chip's decisions in this unit app log")
    args = parser.parse_args(argv)
    if args.gate_log:
        print(gate_on_chip(args.gate_log, args.out / GATE_LABELS))
        return 0
    if (args.run is None) != (args.row is None):
        parser.error("--run and --row go together")
    for path in probe(load_yaml(ctc.CONFIG), args.out, args.work, args.run, args.row):
        print(f"{path}: {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
