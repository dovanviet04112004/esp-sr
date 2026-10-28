"""esp_ppq_patches.ops.band_tail: band_stream gives each band op its delay and SwiftF0's confidence tail as one
BandTail with esp-dl's integers, which fed STEP frames a call matches the window on every frame with one after it; a
graph with an op that cannot stream is refused."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from esp_ppq.executor.torch import TorchExecutor  # noqa: E402

from esp_ppq_cases import RUNGS, TAIL_FRAMES, Tail, espdl_graph, exponent, round_shift, tail_calib  # noqa: E402
from srpipe.compress.quant import esp_ppq_patches, export_espdl, ptq_espdl  # noqa: E402
from srpipe.compress.quant.esp_ppq_patches.ops.band_tail import HIDDEN_TAPS  # noqa: E402
from srpipe.compress.quant.espdl_shapes import attribute  # noqa: E402

FRAMES, STEP = TAIL_FRAMES, 8
PATCHES = ["aligned_channels", "band_stem", "band_head", "band_stream"]
NODE = re.compile(r"^  %(?P<outs>\S+(?:, %\S+)*) = (?P<op>\w+)\[(?P<attrs>.*)\]\((?P<ins>.*)\)$", re.M)


def tail_window(peaks: np.ndarray, level: np.ndarray, params: list[np.ndarray], shifts: list[int]) -> np.ndarray:
    """Confidence [T] from peaks [T, C] and level [T] as esp-dl's tail gives it, zeros past both ends."""
    hw, hb, cw, cb = (p.astype(np.int64) for p in params)
    join = np.concatenate([peaks, level[:, None]], axis=1).astype(np.int64)
    cols = np.pad(join, [(1, 1), (0, 0)])
    acc = hb + sum(cols[k : k + len(join)] @ hw[k] for k in range(HIDDEN_TAPS))
    h = np.maximum(round_shift(acc, shifts[0]), 0)
    return round_shift(h @ cw + cb[0], shifts[1])


def fed(peaks: np.ndarray, level: np.ndarray, lag: int, delay: int, calls: int) -> list[tuple]:
    """What the ops before BandTail give it each call of STEP frames: the peaks of frames n * STEP - (delay - 1) + j
    and the levels of frames lag later, zeros outside peaks and level."""
    behind = delay - 1
    out = []
    for n in range(calls):
        frames = n * STEP - behind + np.arange(STEP)
        p = np.array([peaks[f] if 0 <= f < len(peaks) else np.zeros(peaks.shape[1]) for f in frames], np.int64)
        fresh = np.array([level[f] if 0 <= f < len(level) else 0 for f in frames + lag], np.int64)
        out.append((p, fresh))
    return out


def tail_stream(calls: list[tuple], params, shifts, lag: int, delay: int) -> np.ndarray:
    """Confidence of BandTail over calls of fed, as espdl_band_tail.cpp runs it: output index i is frame i - delay,
    zero before the stream."""
    hw, hb, cw, cb = (p.astype(np.int64) for p in params)
    level_ring = np.zeros(lag, np.int64)
    join_ring = np.zeros((HIDDEN_TAPS - 1, calls[0][0].shape[1] + 1), np.int64)
    out = []
    for n, (p, fresh) in enumerate(calls):
        levels = np.concatenate([level_ring, fresh])[:STEP]
        join = np.concatenate([p, levels[:, None]], axis=1)
        join[n * STEP - (delay - 1) + np.arange(STEP) < 0] = 0
        cols = np.concatenate([join_ring, join])
        acc = hb + sum(cols[k : k + STEP] @ hw[k] for k in range(HIDDEN_TAPS))
        h = np.maximum(round_shift(acc, shifts[0]), 0)
        conf = round_shift(h @ cw + cb[0], shifts[1])
        conf[n * STEP - delay + np.arange(STEP) < 0] = 0
        out.append(conf)
        level_ring = fresh[STEP - lag :]
        join_ring = join[STEP - (HIDDEN_TAPS - 1) :]
    return np.concatenate(out)


def exported(tmp_path: Path, patches: list[str]):
    torch.manual_seed(17)
    calib = tail_calib(np.random.default_rng(43))
    graph = ptq_espdl.quantize(Tail().eval(), calib, tmp_path, RUNGS | {"calibration": "minmax"})
    with esp_ppq_patches.applied(patches):
        path = export_espdl.export(graph, tmp_path / "tail.espdl")
    return graph, calib[0], path


def nodes_of(info: str) -> dict[str, re.Match]:
    return {m["op"]: m for m in NODE.finditer(info)}


def names(listed: str) -> list[str]:
    return [n.strip().lstrip("%") for n in listed.split(",")]


def test_band_stream_gives_each_band_op_its_delay_and_the_tail_as_one_band_tail(tmp_path: Path) -> None:
    _, _, path = exported(tmp_path, PATCHES)
    nodes, tensors = espdl_graph(path)
    found = nodes_of(path.with_suffix(".info").read_text())
    assert not any(op in ("Concat", "Conv") for op, _ in nodes.values())
    delays = {op: attribute(found[op]["attrs"], "delay") for op in ("BandStem", "BandHead", "BandTail")}
    assert delays == {"BandStem": 1, "BandHead": 2, "BandTail": 3}
    tail = found["BandTail"]
    assert attribute(tail["attrs"], "lag") == 2 and attribute(tail["attrs"], "quant_type") == "S8"
    assert [tensors[n].shape for n in names(tail["ins"])[2:]] == [(3, 5, 16), (16,), (16,), (1,)]


def test_band_tail_streamed_matches_the_window_on_every_frame_with_one_after_it(tmp_path: Path) -> None:
    graph, x_in, path = exported(tmp_path, PATCHES)
    _, tensors = espdl_graph(path)
    info = path.with_suffix(".info").read_text()
    tail = nodes_of(info)["BandTail"]
    peaks_name, level_name, *param_names = names(tail["ins"])
    (out_name,) = names(tail["outs"])
    params = [tensors[n] for n in param_names]
    shifts, lag, delay = (attribute(tail["attrs"], k) for k in ("shifts", "lag", "delay"))
    wanted = [peaks_name, level_name, out_name]
    values = TorchExecutor(graph, device="cpu").forward(inputs=x_in, output_names=wanted)
    peaks, level, confidence = (
        torch.round(v / 2.0 ** exponent(info, n)).to(torch.int64).numpy()[0]
        for n, v in zip(wanted, values, strict=True)
    )
    peaks, level = peaks.T, level.reshape(-1)
    window = tail_window(peaks, level, params, shifts)
    assert np.array_equal(window, confidence)
    calls = fed(peaks, level, lag, delay, FRAMES // STEP + 1)
    streamed = tail_stream(calls, params, shifts, lag, delay)
    whole = np.arange(FRAMES - 1)
    assert np.array_equal(streamed[whole + delay], window[whole])
    off = tail_stream(calls, params, shifts, lag - 1, delay)
    assert not np.array_equal(off[whole + delay], window[whole])


def test_band_stream_refuses_a_graph_with_an_op_that_cannot_stream(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot stream"):
        exported(tmp_path, ["aligned_channels", "band_head", "band_stream"])
