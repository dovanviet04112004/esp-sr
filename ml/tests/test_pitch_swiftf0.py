"""SwiftF0 rewritten for the chip: decode puts a peak on its pitch step, moves between steps by the neighbours and gives
no confidence to a peak outside the range; swift_front gives a frame a hop, the same hop by hop as over the whole
stream, again from the start after a reset; the rewrite matches swift-f0's detect."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from srpipe.dsp.spec import swift_front
from srpipe.dsp.spec.mel import ln_f32
from srpipe.generated import grid
from srpipe.tasks.pitch.postproc import decode

FRAMES = 4


def peaked(step: int, high: float = 8.0) -> np.ndarray:
    logits = np.full((decode.PITCH_BINS, FRAMES), -4.0, dtype=np.float32)
    logits[step] = high
    return logits


def test_decode_puts_a_lone_peak_on_its_step_and_gives_no_confidence_outside_the_range() -> None:
    sure = np.full(FRAMES, 3.0, dtype=np.float32)
    pitch_hz, confidence = decode.decode(peaked(30), sure, 50.0, 400.0)
    assert np.allclose(pitch_hz, decode.STEPS_HZ[30], rtol=1e-3)
    assert np.allclose(confidence, 1.0 / (1.0 + np.exp(-3.0)))
    above = int(np.searchsorted(decode.STEPS_HZ, 400.0)) + 2
    _, outside = decode.decode(peaked(above), sure, 50.0, 400.0)
    assert not outside.any()


def test_decode_moves_between_steps_by_the_neighbours() -> None:
    logits = peaked(30)
    logits[31] = 8.0
    pitch_hz, _ = decode.decode(logits, np.zeros(FRAMES, dtype=np.float32), 50.0, 400.0)
    assert np.allclose(pitch_hz, np.sqrt(decode.STEPS_HZ[30].astype(np.float64) * decode.STEPS_HZ[31]), rtol=1e-6)


def test_spectra_give_a_frame_a_whole_hop_and_the_log_floor_on_silence() -> None:
    windows = {n: np.hanning(n).astype(np.float32) for n in swift_front.SIZES}
    bands = {n: np.ones((n // 2 + 1, 128), dtype=np.float32) for n in swift_front.SIZES}
    silence = np.zeros(5 * grid.HOP_SAMPLES + 17, dtype=np.float32)
    out = swift_front.spectra(silence, windows, bands)
    assert out.shape == (len(swift_front.SIZES), 128, 5)
    assert np.all(out == ln_f32(np.array([swift_front.LOG_FLOOR]))[0])


def test_the_front_hop_by_hop_gives_the_whole_stream_s_frames_and_restarts_after_a_reset() -> None:
    rng = np.random.default_rng(9)
    windows = {n: np.hanning(n).astype(np.float32) for n in swift_front.SIZES}
    dense = {n: rng.uniform(0.0, 1.0, (n // 2 + 1, swift_front.BANDS)).astype(np.float32) for n in swift_front.SIZES}
    pcm = np.round(rng.normal(0.0, 3000.0, 12 * grid.HOP_SAMPLES)).astype(np.int16)
    whole = swift_front.spectra(pcm / np.float32(32768.0), windows, dense)
    front = swift_front.SwiftFront(windows, {n: swift_front.sparse(m) for n, m in dense.items()})
    tail = np.zeros(swift_front.DELAY_HOPS * grid.HOP_SAMPLES, np.int16)

    def frames() -> np.ndarray:
        hops = np.concatenate([pcm, tail]).reshape(-1, grid.HOP_SAMPLES)
        return np.stack([f for f in map(front.push, hops) if f is not None], axis=-1)

    assert np.array_equal(frames(), whole)
    front.reset()
    assert np.array_equal(frames(), whole)


def test_the_rewrite_matches_swift_f0_detect(tmp_path: Path) -> None:
    swift_f0 = pytest.importorskip("swift_f0")
    pytest.importorskip("onnx")
    pytest.importorskip("torch")
    from srpipe.tasks.pitch import quant

    shutil.copyfile(Path(swift_f0.__file__).parent / quant.MODEL_FILE, tmp_path / quant.MODEL_FILE)
    assert quant.rewrite_gap(tmp_path, 50.0, 400.0) < 1.0e-4
