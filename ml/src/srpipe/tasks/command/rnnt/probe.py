"""The board probe of the rnnt track (E11-T20, KEHOACH 3.12). Run: python -m srpipe.tasks.command.rnnt.probe [--run
<run> --row <row>]. It writes what ai_engine/test_apps/unit runs on board B in its rnnt build: the three graphs of
rnnt/quant.py, seeded random or a row of a trained run, the frames graph streamed a chunk a step with each step's int8
input and output, the predictor and the joiner each with vectors from their simulation, and windows of raw features
through the command calls, each with the decision Python takes on the int8 simulation of the window.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

import numpy as np
import torch

from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl
from srpipe.core.config import ML_ROOT, data_paths, load_yaml
from srpipe.export import pack_models
from srpipe.generated import listen
from srpipe.tasks.command import ctc
from srpipe.tasks.command import eval as gate
from srpipe.tasks.command.ctc import probe as ctc_probe
from srpipe.tasks.command.ctc import quant as ctc_quant
from srpipe.tasks.command.ctc.model import encoder
from srpipe.tasks.command.ctc.postproc import ctc_score
from srpipe.tasks.command.rnnt import quant
from srpipe.tasks.command.rnnt.postproc import rnnt_search

MODELS_FILE, STREAMS_FILE = "rnnt_models.bin", "rnnt_streams.bin"
VECTORS_FILE, WINDOWS_FILE = "rnnt_vectors.bin", "rnnt_windows.bin"
# The image names a branch's entries after its backend (KEHOACH 6.3).
FRAMES_ENTRY, PREDICTOR_ENTRY, JOINER_ENTRY = "command_rnnt", "rnnt_predictor", "rnnt_joiner"
# Magic, contexts, pairs, context size, one-hot rows, width, classes, five exponents: predictor in, out, joiner frame,
# prefix, logits. Then a context's classes and prefix, then a pair's frame, prefix and logits, each padded to four.
VECTORS_HEAD = struct.Struct("<4sHHBBHHbbbbb3x")
VECTORS_MAGIC = b"SRRV"
WINDOWS_MAGIC = b"SRRW"
GATE_FILE, GATE_LABELS, GATE_MAGIC = "rnnt_gate.bin", "rnnt_gate.json", b"SRRG"
JOINER_PAIRS = 256  # a projected frame with a context's prefix


def padded(raw: bytes) -> bytes:
    return raw + b"\0" * (-len(raw) % 4)


def vectors(sim: quant.Int8Rnnt, contexts: list[tuple[int, ...]], frames: np.ndarray, pairs: int, seed: int) -> bytes:
    """The predictor's int8 output for every context, and the joiner's int8 logits for pairs of a projected frame of
    frames (width, n) and a context, drawn with seed; laid out as VECTORS_HEAD says."""
    rng = np.random.default_rng(seed)
    drawn = list(zip(rng.integers(frames.shape[1], size=pairs), rng.integers(len(contexts), size=pairs), strict=True))
    width, classes = frames.shape[0], encoder.n_classes()
    exponents = (
        sim.predictor_io.input_exponent,
        sim.predictor_io.output_exponent,
        sim.join_frame.exponent,
        sim.join_prefix.exponent,
        sim.join_out.exponent,
    )
    head = VECTORS_HEAD.pack(VECTORS_MAGIC, len(contexts), pairs, sim.size, sim.pad + 1, width, classes, *exponents)
    body = b"".join(padded(bytes(c) + sim.prefix(c).tobytes()) for c in contexts)
    for t, k in drawn:
        f = quant.requant(frames[:, t], sim.frames_out, sim.join_frame.exponent)
        p = quant.requant(sim.prefix(contexts[k]), sim.predictor_io.output_exponent, sim.join_prefix.exponent)
        body += padded(f.tobytes() + p.tobytes() + sim.logits(frames[:, t], [contexts[k]])[:, 0].tobytes())
    return head + body


def command_windows(cfg: dict, sim: quant.Int8Rnnt, norm: tuple, windows: list[np.ndarray]) -> bytes:
    """Raw feature windows (hops, features) as ai_engine_command_{begin,step,score} take them on board B, each with
    the decision Python takes on the int8 simulation of the window, laid out as the ctc probe's WINDOWS_HEAD says."""
    mean, std = norm
    reject, margin = cfg["quant"]["reject"], cfg["eval"]["margin"]
    lexicon = ctc_score.default_lexicon()
    tree = rnnt_search.command_tree(lexicon)
    (commands, most, longest), packed = ctc_score.packed_lexicon(lexicon)
    sizes = (commands, most, longest, cfg["chunk_hops"])
    body = ctc_probe.WINDOWS_HEAD.pack(WINDOWS_MAGIC, len(mean), len(windows), reject, margin, *sizes)
    body += packed + b"\0" * (-(ctc_probe.WINDOWS_HEAD.size + len(packed)) % 4)
    for x in windows:
        decision, _ = quant.int8_decided(sim, x, mean, std, tree, reject, margin)
        body += struct.pack("<I", len(x)) + np.ascontiguousarray(x, dtype="<f4").tobytes()
        body += ctc_score.DECISION_RECORD.pack(*decision.tolist())
    return body


def gate_windows(cfg: dict, sim: quant.Int8Rnnt, norm: tuple, windows: list[np.ndarray]) -> bytes:
    """Every Gate 3 window as its int8 input, laid out as the ctc probe's GATE_HEAD says with GATE_MAGIC, each with the
    decision Python takes on the int8 simulation: the unit app rebuilds raw features from the int8 with the mean and
    std the record carries, so the chip sees the same int8."""
    mean, std = norm
    reject, margin = cfg["quant"]["reject"], cfg["eval"]["margin"]
    lexicon = ctc_score.default_lexicon()
    tree = rnnt_search.command_tree(lexicon)
    (commands, most, longest), packed = ctc_score.packed_lexicon(lexicon)
    e = sim.frames.io.input_exponent
    sizes = (commands, most, longest, cfg["chunk_hops"])
    body = ctc_probe.GATE_HEAD.pack(GATE_MAGIC, len(mean), len(windows), reject, margin, *sizes, e)
    body += np.concatenate([mean, std]).astype("<f4").tobytes() + packed
    body += b"\0" * (-len(body) % 4)
    for x in windows:
        normalised = ((x - mean) / std).T[None].astype(np.float32)
        on_grid = ptq_espdl.to_int8(normalised, e)
        rebuilt = (on_grid[0].T.astype(np.float32) * np.float32(2.0**e)) * std + mean
        decision, _ = quant.int8_decided(sim, rebuilt.astype(np.float32), mean, std, tree, reject, margin)
        hops = np.ascontiguousarray(on_grid[0].T).tobytes()
        body += struct.pack("<I", len(x)) + hops + b"\0" * (-len(hops) % 4)
        body += ctc_score.DECISION_RECORD.pack(*decision.tolist())
    return body


def probe(cfg: dict, out: Path, work: Path, run: Path | None = None, row: str | None = None) -> tuple[Path, ...]:
    """Write out/rnnt_models.bin, the image of the three graphs and the norm; out/rnnt_streams.bin, the frames graph
    a chunk a step; out/rnnt_vectors.bin, the predictor and the joiner; out/rnnt_windows.bin, windows through the
    command calls. work keeps each ONNX and .espdl. With run, the graphs are those of row in its ladder, the frames
    graph streams a test sentence and the windows are the board's; out/rnnt_gate.bin then holds every Gate 3 window,
    out/rnnt_gate.json what each should get, and without run neither is left."""
    p, patches = cfg["probe"], cfg["esp_ppq_patches"]
    every, labels = out / GATE_FILE, out / GATE_LABELS
    if run is None:
        torch.manual_seed(p["seed"])
        net = ctc_probe.draw_norm_scales(encoder.build(cfg), *p["norm_scale"]).eval()
        dims = encoder.n_dims(cfg)
        *calib, probe_x = ctc_probe.drawn(cfg, dims, p["calib_sequences"] + 1)
        norm = (np.full(dims, p["input_mean"], np.float32), np.full(dims, p["input_std"], np.float32))
        size, pad = cfg["rnnt"]["context"], net.transducer.predictor.pad
        contexts = quant.tree_contexts(rnnt_search.command_tree(ctc_score.default_lexicon()), size, pad)
        calib_t = [torch.from_numpy(c) for c in calib]
        rungs = ptq_espdl.ladder(ctc_quant.LADDER)
        columns = cfg["rnnt"]["joiner_columns"]
        graphs = quant.quantized(net, calib_t, contexts, work, rungs, patches, JOINER_PAIRS, p["seed"], columns)
        names, trained_cfg = [f"c{k}" for k in range(len(ctc_score.default_lexicon()))], cfg
        windows, board, expected = ctc_probe.random_windows(cfg, *norm), [], []
    else:
        trained = gate.load_ctc(run)
        net, norm, names, trained_cfg = trained.model, (trained.mean, trained.std), trained.names, trained.cfg
        probe_x = ctc_quant.test_sentence(cfg, trained)
        size, pad = trained_cfg["rnnt"]["context"], net.transducer.predictor.pad
        contexts = quant.tree_contexts(rnnt_search.command_tree(ctc_score.default_lexicon()), size, pad)
        graphs = quant.loaded(run / "int8" / row)
        sessions = ctc_quant.board_windows(cfg, trained, data_paths())
        board = [x for scored in sessions for x in scored.decided]
        expected = [{"session": s.session, "expected": s.expected} for s in sessions for _ in s.decided]
        windows = board[:: max(1, len(board) // p["command"]["windows"])][: p["command"]["windows"]]
    frames_espdl, frames_record = ctc_probe.probe_net(
        graphs.frames, FRAMES_ENTRY, cfg["chunk_hops"], cfg, work, probe_x
    )
    sim = quant.Int8Rnnt(graphs, cfg["quant"]["hops"], gate.Ctc(net, *norm, names, [], trained_cfg), patches)
    hot_e = sim.predictor_io.input_exponent
    hot = ptq_espdl.to_int8(quant.one_hot(contexts[0], pad), hot_e).astype(np.float32) * np.float32(2.0**hot_e)
    frames = sim.projected(probe_x, probe_x.shape[2] // net.front.hop_stride)
    columns = cfg["rnnt"]["joiner_columns"]
    f = quant.requant(frames[:, 0], sim.frames_out, sim.join_frame.exponent).astype(np.float32)
    q = [
        quant.requant(
            sim.prefix(contexts[k % len(contexts)]), sim.predictor_io.output_exponent, sim.join_prefix.exponent
        )
        for k in range(columns)
    ]
    pair = (
        np.repeat(f[None, :, None], columns, axis=2) * np.float32(2.0**sim.join_frame.exponent),
        np.stack(q, axis=1).astype(np.float32)[None] * np.float32(2.0**sim.join_prefix.exponent),
    )
    with esp_ppq_patches.applied(patches):
        predictor = export_espdl.export(
            graphs.predictor, work / PREDICTOR_ENTRY / f"{PREDICTOR_ENTRY}.espdl", None, hot
        )
        joiner = export_espdl.export(graphs.joiner, work / JOINER_ENTRY / f"{JOINER_ENTRY}.espdl", None, pair)
    out.mkdir(parents=True, exist_ok=True)
    image = out / MODELS_FILE
    entries = [
        pack_models.Entry(FRAMES_ENTRY, "espdl", frames_espdl),
        pack_models.Entry(FRAMES_ENTRY, "norm", np.concatenate(norm).astype("<f4").tobytes()),
        pack_models.Entry(PREDICTOR_ENTRY, "espdl", predictor.read_bytes()),
        pack_models.Entry(JOINER_ENTRY, "espdl", joiner.read_bytes()),
    ]
    listen_hash = listen.HASH if run is None else trained_cfg.get("listen_hash", 0)
    image.write_bytes(pack_models.pack(entries, listen_hash=listen_hash))
    streams = out / STREAMS_FILE
    streams.write_bytes(ctc_probe.STREAMS_HEAD.pack(ctc_probe.STREAMS_MAGIC, 1) + frames_record)
    tested = out / VECTORS_FILE
    tested.write_bytes(vectors(sim, contexts, frames, JOINER_PAIRS, p["seed"]))
    command = out / WINDOWS_FILE
    command.write_bytes(command_windows(cfg, sim, norm, windows))
    for name, size_bytes in ((FRAMES_ENTRY, len(frames_espdl)), (PREDICTOR_ENTRY, predictor.stat().st_size)):
        print(f"{name}: {size_bytes} bytes of .espdl")
    print(f"{JOINER_ENTRY}: {joiner.stat().st_size} bytes of .espdl; {len(contexts)} contexts in the default tree")
    if run is None:
        every.unlink(missing_ok=True)
        labels.unlink(missing_ok=True)
        return image, streams, tested, command
    every.write_bytes(gate_windows(cfg, sim, norm, board))
    labels.write_text(json.dumps({"names": names, "windows": expected}, ensure_ascii=False), encoding="utf-8")
    return image, streams, tested, command, every, labels


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ctc_probe.PROBE_DIR)
    parser.add_argument("--work", type=Path, default=ML_ROOT / "artifacts" / "command_ctc" / "probe_rnnt")
    parser.add_argument("--run", type=Path, help="the graphs of a row of this trained run instead of random weights")
    parser.add_argument("--row", help="the rnnt_* row of <run>/int8/ladder.yaml whose graphs run")
    parser.add_argument("--gate-log", type=Path, help="count Gate 3 on the chip's decisions in this unit app log")
    args = parser.parse_args(argv)
    if args.gate_log:
        print(ctc_probe.gate_on_chip(args.gate_log, args.out / GATE_LABELS))
        return 0
    if (args.run is None) != (args.row is None):
        parser.error("--run and --row go together")
    for path in probe(load_yaml(ctc.CONFIG), args.out, args.work, args.run, args.row):
        print(f"{path}: {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
