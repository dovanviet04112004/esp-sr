"""mAP of the detect chain (KEHOACH 3.18): the float ONNX by onnxruntime and the int8 graph as ESP-PPQ simulates the
chip, each image through the chip's letterbox, the one-to-one head decoded as espressif/yolo26 does, boxes kept as
Ultralytics' end-to-end head keeps them, scored by pycocotools against a split's YOLO labels.
Run: python -m srpipe.tasks.detect.eval <run> [--split val|test] [--limit N]"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import yaml

from srpipe.core.config import data_paths, load_yaml
from srpipe.tasks.detect import data

BOX_CHANNELS = 4
ONE_TO_ONE = ("one2one_p3", "one2one_p4", "one2one_p5")


def decode(boxes: list[np.ndarray], logits: list[np.ndarray], strides: list[int]) -> tuple[np.ndarray, np.ndarray]:
    """Per level (4, H, W) distances and (C, H, W) class logits to (N, 4) x1 y1 x2 y2 in the square and (N, C) scores:
    each cell's centre minus the left and top distances and plus the right and bottom ones, times the stride."""
    out_boxes, out_scores = [], []
    for box, logit, stride in zip(boxes, logits, strides, strict=True):
        h, w = box.shape[1:]
        gy, gx = np.meshgrid(np.arange(h, dtype=np.float32) + 0.5, np.arange(w, dtype=np.float32) + 0.5, indexing="ij")
        corners = np.stack([gx - box[0], gy - box[1], gx + box[2], gy + box[3]]) * np.float32(stride)
        out_boxes.append(corners.reshape(4, -1).T)
        out_scores.append((1.0 / (1.0 + np.exp(-logit.astype(np.float32)))).reshape(logit.shape[0], -1).T)
    return np.concatenate(out_boxes), np.concatenate(out_scores)


def kept(boxes: np.ndarray, scores: np.ndarray, max_det: int, conf: float) -> np.ndarray:
    """(k, 6) x1 y1 x2 y2 score class: the max_det cells of highest best score, then the max_det highest of their class
    scores, those above conf, highest first."""
    cells = np.argsort(-scores.max(axis=1), kind="stable")[:max_det]
    flat = scores[cells].reshape(-1)
    top = np.argsort(-flat, kind="stable")[:max_det]
    top = top[flat[top] > conf]
    cell, cls = np.divmod(top, scores.shape[1])
    return np.column_stack([boxes[cells[cell]], flat[top], cls]).astype(np.float64)


def detections_of(maps: list[np.ndarray], strides: list[int], cfg: dict) -> np.ndarray:
    """One image's kept boxes in the square from three raw one-to-one maps, (1, 4 + C, H, W) each."""
    boxes = [m[0, :BOX_CHANNELS] for m in maps]
    logits = [m[0, BOX_CHANNELS:] for m in maps]
    return kept(*decode(boxes, logits, strides), cfg["eval"]["max_det"], cfg["eval"]["conf"])


def mean_ap(images: list[Path], found: data.Package, dets: list[np.ndarray]) -> tuple[float, float]:
    """COCO mAP50-95 and mAP50 of dets, boxes in source pixels, against the images' YOLO labels."""
    from pycocotools.coco import COCO
    from pycocotools.cocoeval import COCOeval

    truth = {"images": [], "annotations": [], "categories": [{"id": k} for k in range(len(found.names))]}
    results = []
    for k, (path, det) in enumerate(zip(images, dets, strict=True)):
        h, w = data.rgb_of(path).shape[:2]
        truth["images"].append({"id": k, "width": w, "height": h})
        for c, cx, cy, bw, bh in data.labels(found.labels_of(path)):
            box = [(cx - bw / 2) * w, (cy - bh / 2) * h, bw * w, bh * h]
            truth["annotations"].append(
                {
                    "id": len(truth["annotations"]) + 1,
                    "image_id": k,
                    "category_id": int(c),
                    "bbox": box,
                    "area": box[2] * box[3],
                    "iscrowd": 0,
                }
            )
        for x1, y1, x2, y2, score, c in det:
            results.append({"image_id": k, "category_id": int(c), "bbox": [x1, y1, x2 - x1, y2 - y1], "score": score})
    if not results:
        return 0.0, 0.0
    with contextlib.redirect_stdout(io.StringIO()):
        gt = COCO()
        gt.dataset = truth
        gt.createIndex()
        scored = COCOeval(gt, gt.loadRes(results), "bbox")
        scored.evaluate()
        scored.accumulate()
        scored.summarize()
    return float(scored.stats[0]), float(scored.stats[1])


def score(images: list[Path], found: data.Package, cfg: dict, strides: list[int], net: Callable) -> tuple[float, float]:
    """mAP of net, which maps a 1 x 3 x S x S input to the three raw one-to-one maps, over images."""
    dets = []
    for path in images:
        square, box = data.letterbox(data.rgb_of(path), cfg["size"], cfg["pad"])
        det = detections_of(net(data.input_of(square)), strides, cfg)
        det[:, :4] = box.to_source(det[:, :4])
        dets.append(det)
    return mean_ap(images, found, dets)


def float_net(run: Path) -> Callable:
    import onnxruntime

    session = onnxruntime.InferenceSession(str(run / "model.onnx"), providers=["CPUExecutionProvider"])
    return lambda x: session.run(list(ONE_TO_ONE), {"images": x})


def int8_net(run: Path) -> Callable:
    """The quantised graph as ESP-PPQ simulates the chip, its six box and class outputs joined back per level."""
    import torch
    from esp_ppq.executor import TorchExecutor

    from srpipe.compress.quant import export_espdl

    graph = export_espdl.load_native(run / "int8" / "graph.native")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    executor = TorchExecutor(graph=graph, device=device)
    names = list(graph.outputs)

    def net(x: np.ndarray) -> list[np.ndarray]:
        out = dict(
            zip(
                names,
                (t.detach().float().cpu().numpy() for t in executor.forward(torch.from_numpy(x).to(device))),
                strict=True,
            )
        )
        return [np.concatenate([out[f"{n}_box"], out[f"{n}_cls"]], axis=1) for n in ONE_TO_ONE]

    return net


def evaluate(run: Path, split: str, limit: int | None) -> dict:
    cfg = load_yaml(run / "config.resolved.yaml")
    found = data.package(data_paths()["interim"] / cfg["package"])
    images = found.images[split][:limit] if limit else found.images[split]
    strides = json.loads((run / "model.onnx.json").read_text(encoding="utf-8"))["stride"]
    report = {"split": split, "images": len(images)}
    for name, make in (("float", float_net), ("int8", int8_net)):
        if name == "int8" and not (run / "int8" / "graph.native").exists():
            continue
        map50_95, map50 = score(images, found, cfg, strides, make(run))
        report[name] = {"map50_95": round(map50_95, 4), "map50": round(map50, 4)}
    if "int8" in report:
        report["drop"] = round(report["float"]["map50_95"] - report["int8"]["map50_95"], 4)
        report["passes"] = report["drop"] <= cfg["eval"]["gate"]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path)
    parser.add_argument("--split", choices=["val", "test"], default="val")
    parser.add_argument("--limit", type=int, default=None, help="the first N images of the split only")
    args = parser.parse_args(argv)
    report = evaluate(args.run, args.split, args.limit)
    out = (
        args.run / "int8" / f"map_{args.split}.yaml"
        if (args.run / "int8").exists()
        else args.run / f"map_{args.split}.yaml"
    )
    out.write_text(yaml.safe_dump(report, sort_keys=False), encoding="utf-8")
    print(yaml.safe_dump(report, sort_keys=False))
    return 0 if report.get("passes", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
