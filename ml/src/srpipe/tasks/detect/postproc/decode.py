"""YOLO26's one-to-one head decoded into boxes, as ai_engine/src/detect/decode.c runs it after the network (KEHOACH
3.18): each cell's best class by its integer logit, kept when the logit reaches the level's floor, the k best cells by
the exact logit q 2^e with ties in scan order, boxes cut to integers in the S x S square, then mapped back to the
picture in float32 as esp-dl's detect postprocessors do; frame_of gives the letterbox as esp-dl's preprocessor does.
Run: python -m srpipe.tasks.detect.postproc.decode [--out contracts/golden] writes contracts/golden/detect_decode/."""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from srpipe.core.config import ML_ROOT
from srpipe.golden.gold import write_gold
from srpipe.tasks.detect import data

GOLDEN_ROOT = ML_ROOT.parent / "contracts" / "golden"
BLOCK = "detect_decode"
SEED = 20261030
PERMILLE = 1000
BOX_SIDES = 4
STRIDES = (8, 16, 32)
INT32 = (-(2**31), 2**31 - 1)


@dataclass(frozen=True)
class Frame:
    """Where the picture sits in the square: picture pixels a square pixel, float32, the borders, the picture's size."""

    inverse_scale_x: np.float32
    inverse_scale_y: np.float32
    border_left: int
    border_top: int
    width: int
    height: int


@dataclass(frozen=True)
class Level:
    """One level's raw outputs, channels last as esp-dl lays them: box (grid, grid, 4), cls (grid, grid, classes)."""

    box: np.ndarray
    cls: np.ndarray
    box_exponent: int
    cls_exponent: int
    stride: int


def frame_of(width: int, height: int, size: int) -> Frame:
    """The picture's place in the square as esp-dl's preprocessor computes it (data.geometry)."""
    box = data.geometry(width, height, size)
    return Frame(box.step_x, box.step_y, box.left, box.top, width, height)


def floor_of(score_permille: int, exponent: int) -> int:
    """The least class logit, in steps of 2^exponent, whose sigmoid reaches score_permille, from double."""
    if score_permille <= 0 or score_permille >= PERMILLE:
        return INT32[0] if score_permille <= 0 else INT32[1]
    p = score_permille / PERMILLE
    return min(max(math.ceil(math.log(p / (1.0 - p)) / 2.0**exponent), INT32[0]), INT32[1])


def on_picture(square: int, border: int, inverse_scale: np.float32, extent: int) -> int:
    v = int(np.float32(square - border) * inverse_scale)
    return min(max(v, 0), extent - 1)


def decode(levels: list[Level], classes: int, score_permille: int, frame: Frame, k: int, top_k_max: int) -> np.ndarray:
    """(n, 6) int boxes x1 y1 x2 y2 class score_permille, best first, n at most k and top_k_max."""
    k = min(k, top_k_max)
    found = []
    for lv in levels:
        floor_q = floor_of(score_permille, lv.cls_exponent)
        step = np.float32(lv.stride)
        box_step = np.float32(2.0**lv.box_exponent)
        grid = lv.cls.shape[0]
        logits = np.asarray(lv.cls, np.int64).reshape(grid * grid, classes)
        best = logits.argmax(axis=1)
        best_q = logits[np.arange(grid * grid), best]
        sides = np.asarray(lv.box, np.int64).reshape(grid * grid, BOX_SIDES).astype(np.float32) * box_step
        for cell in np.flatnonzero(best_q >= floor_q):
            h, w = divmod(int(cell), grid)
            cx, cy = np.float32(w) + np.float32(0.5), np.float32(h) + np.float32(0.5)
            s = sides[cell]
            square = (
                int((cx - s[0]) * step),
                int((cy - s[1]) * step),
                int((cx + s[2]) * step),
                int((cy + s[3]) * step),
            )
            found.append(
                (
                    float(best_q[cell]) * 2.0**lv.cls_exponent,
                    (
                        on_picture(square[0], frame.border_left, frame.inverse_scale_x, frame.width),
                        on_picture(square[1], frame.border_top, frame.inverse_scale_y, frame.height),
                        on_picture(square[2], frame.border_left, frame.inverse_scale_x, frame.width),
                        on_picture(square[3], frame.border_top, frame.inverse_scale_y, frame.height),
                        int(best[cell]),
                    ),
                )
            )
    kept = sorted(found, key=lambda f: -f[0])[:k]
    out = np.zeros((len(kept), 6), np.int64)
    for i, (logit, box) in enumerate(kept):
        score = np.float32(1.0 / (1.0 + math.exp(-logit)))
        out[i, :5] = box
        out[i, 5] = int(score * np.float32(PERMILLE) + np.float32(0.5))
    return out


def raw_level(
    rng: np.random.Generator, grid: int, classes: int, bytes_: int, hot: int
) -> tuple[np.ndarray, np.ndarray]:
    """Box sides within a few cells and class logits under the floor but for hot cells, a few of them tied."""
    dtype = np.int8 if bytes_ == 1 else np.int16
    top = 127 if bytes_ == 1 else 4000
    box = rng.integers(0, top // 2, (grid, grid, BOX_SIDES)).astype(dtype)
    cls = rng.integers(-top, -top // 2, (grid, grid, classes)).astype(dtype)
    for _ in range(hot):
        h, w, c = rng.integers(0, grid), rng.integers(0, grid), rng.integers(0, classes)
        cls[h, w, c] = rng.integers(-top // 8, top)
    cls[0, 0, :2] = top // 2
    return box, cls


def case(rng, size: int, picture: tuple[int, int], classes: int, bytes_: int, k: int, off: int = 0) -> dict:
    """Three levels of S = size, the picture's frame, and decode's boxes; off shifts the left border, a negative
    control."""
    exponents = [(-3, -4), (-4, -5), (-2, -3)] if bytes_ == 1 else [(-10, -11), (-9, -10), (-11, -12)]
    levels = []
    for (box_e, cls_e), stride in zip(exponents, STRIDES, strict=True):
        box, cls = raw_level(rng, size // stride, classes, bytes_, hot=size // stride)
        levels.append(Level(box, cls, box_e, cls_e, stride))
    frame = frame_of(picture[0], picture[1], size)
    shifted = Frame(frame.inverse_scale_x, frame.inverse_scale_y, frame.border_left + off, frame.border_top, *picture)
    boxes = decode(levels, classes, 250, shifted, k, k)
    tensors = {}
    for i, lv in enumerate(levels):
        tensors[f"box{i}"] = lv.box
        tensors[f"cls{i}"] = lv.cls
    tensors["exponents"] = np.array([[lv.box_exponent, lv.cls_exponent, lv.stride] for lv in levels], np.int32)
    tensors["frame"] = np.array([frame.inverse_scale_x, frame.inverse_scale_y], np.float32)
    tensors["picture"] = np.array([frame.border_left, frame.border_top, *picture, 250, k], np.int32)
    tensors["boxes"] = boxes.astype(np.float32)
    return tensors


def emit(root: Path) -> list[Path]:
    """int8 and int16 heads on a wide, a tall and a square picture, k under and over the cells that pass, ties among
    them; then a negative control with the left border one pixel off."""
    rng = np.random.default_rng(SEED)
    cases = {
        "case_000": case(rng, 64, (200, 100), 3, 1, 8),
        "case_001": case(rng, 64, (90, 160), 5, 2, 32),
        "case_002": case(rng, 96, (96, 96), 4, 2, 4),
        "case_003": case(rng, 64, (640, 427), 80, 2, 32),
        "case_neg_000": case(rng, 64, (200, 100), 3, 1, 8, off=1),
    }
    written = []
    for name, tensors in cases.items():
        path = root / BLOCK / f"{name}.gold"
        write_gold(path, tensors)
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=GOLDEN_ROOT)
    for path in emit(parser.parse_args().out):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
