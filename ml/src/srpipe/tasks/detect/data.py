"""The detect chain's package (KEHOACH 3.18): model.pt and data/ in YOLO's layout as a server hands them over, the
chip's letterbox, and the COCO 2017 test package: yolo26n.pt, a seeded train2017 sample as train, val2017 halved by
the seed into val and test, read from raw/vision/coco2017 and written to interim/<package>.
Run: python -m srpipe.tasks.detect.data coco-urls (the train sample to download) | coco"""

from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from srpipe.core.config import apply_overrides, data_paths, load_yaml
from srpipe.tasks.detect import CONFIG

SPLITS = ("train", "val", "test")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp")
COCO_RAW = Path("vision") / "coco2017"


@dataclass(frozen=True)
class Package:
    """A server's package: model.pt, the class names of data/data.yaml in output order, and each split's images."""

    root: Path
    names: list[str]
    images: dict[str, list[Path]]

    @property
    def model(self) -> Path:
        return self.root / "model.pt"

    def labels_of(self, image: Path) -> Path:
        split = image.parent.name
        return self.root / "data" / "labels" / split / f"{image.stem}.txt"


def package(root: Path) -> Package:
    """root's package, checked: model.pt, data/data.yaml naming the classes, each split's images with label files."""
    if not (root / "model.pt").is_file():
        raise FileNotFoundError(f"{root}: no model.pt")
    spec = yaml.safe_load((root / "data" / "data.yaml").read_text(encoding="utf-8"))
    names = spec["names"]
    names = [names[k] for k in sorted(names)] if isinstance(names, dict) else list(names)
    images = {}
    for split in SPLITS:
        folder = root / "data" / "images" / split
        images[split] = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
        if not images[split]:
            raise FileNotFoundError(f"{folder}: no image")
    found = Package(root, names, images)
    unlabelled = [p for split in SPLITS for p in images[split] if not found.labels_of(p).is_file()]
    if unlabelled:
        raise FileNotFoundError(f"{root}: {len(unlabelled)} images without a label file, e.g. {unlabelled[0]}")
    return found


@dataclass(frozen=True)
class Letterbox:
    """Where an image went in the letterboxed square: source pixels per square pixel, and the top-left border."""

    step_x: float
    step_y: float
    left: int
    top: int

    def to_source(self, boxes: np.ndarray) -> np.ndarray:
        """Boxes (n, 4) x1 y1 x2 y2 in the square back to source pixels."""
        out = boxes.astype(np.float64).copy()
        out[:, 0::2] = (out[:, 0::2] - self.left) * self.step_x
        out[:, 1::2] = (out[:, 1::2] - self.top) * self.step_y
        return out


def letterbox(rgb: np.ndarray, size: int, pad: int) -> tuple[np.ndarray, Letterbox]:
    """rgb (h, w, 3) uint8 by nearest neighbour into size x size, pad around, as espressif/yolo26's preprocess does."""
    src_h, src_w = rgb.shape[:2]
    scale_x, scale_y = size / float(src_w), size / float(src_h)
    scale = min(scale_x, scale_y)
    left = top = 0
    span_w = span_h = size
    if scale_x < scale_y:
        gap = size - int(scale * src_h)
        top, span_h = gap // 2, size - gap
    else:
        gap = size - int(scale * src_w)
        left, span_w = gap // 2, size - gap
    step_x, step_y = float(src_w) / span_w, float(src_h) / span_h
    rows = np.minimum((np.arange(span_h) * step_y).astype(np.int64), src_h - 1)
    cols = np.minimum((np.arange(span_w) * step_x).astype(np.int64), src_w - 1)
    out = np.full((size, size, 3), pad, dtype=np.uint8)
    out[top : top + span_h, left : left + span_w] = rgb[rows[:, None], cols[None, :]]
    return out, Letterbox(step_x, step_y, left, top)


def rgb_of(path: Path) -> np.ndarray:
    from PIL import Image

    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.uint8)


def input_of(square: np.ndarray) -> np.ndarray:
    """A letterboxed square as the network's input: 1 x 3 x S x S float32 in 0..1."""
    return np.ascontiguousarray(square.transpose(2, 0, 1)[None], dtype=np.float32) / np.float32(255.0)


def labels(path: Path) -> np.ndarray:
    """A YOLO label file as (n, 5) float: class, centre x, centre y, width, height, each a share of the image."""
    rows = [line.split() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return np.array(rows, dtype=np.float64).reshape(-1, 5)


def coco_rows(annotations: dict, classes: list[int]) -> dict[int, list[str]]:
    """Per image id, its non-crowd boxes as YOLO label lines, classes numbered by sorted category id."""
    index = {c: k for k, c in enumerate(classes)}
    sizes = {im["id"]: (im["width"], im["height"]) for im in annotations["images"]}
    rows: dict[int, list[str]] = {im["id"]: [] for im in annotations["images"]}
    for a in annotations["annotations"]:
        x, y, w, h = a["bbox"]
        if a.get("iscrowd") or w <= 0 or h <= 0:
            continue
        iw, ih = sizes[a["image_id"]]
        rows[a["image_id"]].append(
            f"{index[a['category_id']]} {(x + w / 2) / iw:.6f} {(y + h / 2) / ih:.6f} {w / iw:.6f} {h / ih:.6f}"
        )
    return rows


def write_split(out: Path, split: str, files: dict[str, bytes], rows: dict[str, list[str]]) -> None:
    images, label_dir = out / "data" / "images" / split, out / "data" / "labels" / split
    images.mkdir(parents=True, exist_ok=True)
    label_dir.mkdir(parents=True, exist_ok=True)
    for name, blob in files.items():
        (images / name).write_bytes(blob)
        (label_dir / f"{Path(name).stem}.txt").write_text("".join(f"{r}\n" for r in rows[name]), encoding="utf-8")


def coco_notes(raw: Path) -> tuple[dict, dict]:
    with zipfile.ZipFile(raw / "annotations_trainval2017.zip") as notes:
        return (
            json.loads(notes.read("annotations/instances_train2017.json")),
            json.loads(notes.read("annotations/instances_val2017.json")),
        )


def train_sample(train: dict, count: int, seed: int) -> list[str]:
    """count train2017 file names drawn by the seed among images holding at least one box, sorted."""
    boxed = sorted({a["image_id"] for a in train["annotations"] if not a.get("iscrowd")})
    files = {im["id"]: im["file_name"] for im in train["images"]}
    drawn = np.random.default_rng(seed).choice(len(boxed), size=count, replace=False)
    return sorted(files[boxed[k]] for k in drawn)


def build_coco(cfg: dict, paths: dict) -> Path:
    """The COCO 2017 test package under interim/<package>: model.pt, train from the downloaded train2017 sample, val2017
    halved by the seed into val and test; a package already there is left alone."""
    spec, raw = cfg["coco"], paths["raw"] / COCO_RAW
    out = paths["interim"] / cfg["package"]
    if (out / "data" / "data.yaml").exists():
        return out
    train, val = coco_notes(raw)
    classes = sorted(c["id"] for c in val["categories"])
    names = [c["name"] for c in sorted(val["categories"], key=lambda c: c["id"])]
    by_file = {im["file_name"]: im["id"] for im in val["images"] + train["images"]}
    val_rows, train_rows = coco_rows(val, classes), coco_rows(train, classes)
    val_files = sorted(im["file_name"] for im in val["images"])
    order = np.random.default_rng(spec["seed"]).permutation(len(val_files))
    half = len(val_files) // 2
    with zipfile.ZipFile(raw / "val2017.zip") as images:
        for split, picks in (("val", order[:half]), ("test", order[half:])):
            chosen = [val_files[k] for k in picks]
            files = {f: images.read(f"val2017/{f}") for f in chosen}
            write_split(out, split, files, {f: val_rows[by_file[f]] for f in chosen})
    wanted = train_sample(train, spec["train_count"], spec["seed"])
    folder = raw / spec["train_sample"]
    missing = [f for f in wanted if not (folder / f).is_file()]
    if missing:
        raise FileNotFoundError(f"{folder}: {len(missing)} of the seeded sample missing, e.g. {missing[0]}; coco-urls")
    write_split(
        out, "train", {f: (folder / f).read_bytes() for f in wanted}, {f: train_rows[by_file[f]] for f in wanted}
    )
    shutil.copyfile(raw / spec["model"], out / "model.pt")
    data_yaml = {"path": ".", "train": "images/train", "val": "images/val", "test": "images/test", "names": names}
    (out / "data" / "data.yaml").write_text(yaml.safe_dump(data_yaml, allow_unicode=True, sort_keys=False), "utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=["coco-urls", "coco"])
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    args = parser.parse_args(argv)
    cfg, paths = apply_overrides(load_yaml(CONFIG), args.overrides), data_paths()
    if args.step == "coco-urls":
        train, _ = coco_notes(paths["raw"] / COCO_RAW)
        for name in train_sample(train, cfg["coco"]["train_count"], cfg["coco"]["seed"]):
            print(f"{cfg['coco']['train_url']}/{name}")
        return 0
    out = build_coco(cfg, paths)
    found = package(out)
    print(f"{out}: {len(found.names)} classes, " + ", ".join(f"{s} {len(found.images[s])}" for s in SPLITS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
