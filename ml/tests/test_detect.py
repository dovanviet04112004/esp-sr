"""The detect chain's pure parts (KEHOACH 3.18): the letterbox matches Espressif's per-pixel reference, a box maps back
to the source, COCO boxes become YOLO lines without crowds, a package missing its model or a label is refused, the
one-to-one head decodes and keeps boxes as espressif/yolo26 and Ultralytics do, mAP is 1 for exact boxes and lower
for shifted ones, the gate refuses labels that miss the model and an int8 that fell too far, deploy takes only a run
that passed it, and calibration batches hold the chip's letterbox in uint8."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from srpipe.tasks.detect import data
from srpipe.tasks.detect import eval as detect_eval


def reference_letterbox(img: np.ndarray, size: int, pad: int) -> np.ndarray:
    """espdl_preprocess of Espressif's YOLO26 tutorial, pixel by pixel, as the oracle."""
    src_h, src_w = img.shape[:2]
    scale_x, scale_y = size / float(src_w), size / float(src_h)
    top = bottom = left = right = 0
    if scale_x < scale_y:
        pad_h = size - int(min(scale_x, scale_y) * src_h)
        top, bottom = pad_h // 2, pad_h - pad_h // 2
    else:
        pad_w = size - int(min(scale_x, scale_y) * src_w)
        left, right = pad_w // 2, pad_w - pad_w // 2
    act_w, act_h = size - left - right, size - top - bottom
    out = np.full((size, size, 3), pad, dtype=np.uint8)
    for y in range(act_h):
        ys = min(int(y * (float(src_h) / act_h)), src_h - 1)
        for x in range(act_w):
            out[y + top, x + left] = img[ys, min(int(x * (float(src_w) / act_w)), src_w - 1)]
    return out


@pytest.mark.parametrize("shape", [(37, 53), (53, 37), (40, 40), (480, 640)])
def test_the_letterbox_matches_espressifs_reference_pixel_for_pixel(shape: tuple[int, int]) -> None:
    img = np.random.default_rng(1).integers(0, 256, (*shape, 3), dtype=np.uint8)
    size = 64 if shape[0] < 100 else 160
    square, _ = data.letterbox(img, size, 114)
    assert np.array_equal(square, reference_letterbox(img, size, 114))


def test_a_box_in_the_square_maps_back_to_the_source() -> None:
    img = np.zeros((300, 600, 3), dtype=np.uint8)
    _, box = data.letterbox(img, 512, 114)
    corners = np.array([[box.left, box.top, 512 - box.left, 512 - box.top]], dtype=np.float64)
    assert np.allclose(box.to_source(corners), [[0, 0, 600, 300]], atol=1.5)


def test_coco_boxes_become_yolo_lines_and_crowds_are_left_out() -> None:
    notes = {
        "images": [{"id": 7, "width": 200, "height": 100}],
        "annotations": [
            {"image_id": 7, "category_id": 3, "bbox": [10, 20, 40, 30], "iscrowd": 0},
            {"image_id": 7, "category_id": 1, "bbox": [0, 0, 50, 50], "iscrowd": 1},
        ],
    }
    rows = data.coco_rows(notes, [1, 3])
    assert rows == {7: ["1 0.150000 0.350000 0.200000 0.300000"]}


def make_package(root: Path, label_last: bool = True) -> Path:
    root.mkdir(parents=True)
    (root / "model.pt").write_bytes(b"pt")
    (root / "data").mkdir()
    (root / "data" / "data.yaml").write_text(yaml.safe_dump({"names": {0: "a", 1: "b"}}), encoding="utf-8")
    for split in data.SPLITS:
        (root / "data" / "images" / split).mkdir(parents=True)
        (root / "data" / "labels" / split).mkdir(parents=True)
        (root / "data" / "images" / split / "x.jpg").write_bytes(b"jpg")
        if label_last or split != "test":
            (root / "data" / "labels" / split / "x.txt").write_text("0 0.5 0.5 0.1 0.1\n", encoding="utf-8")
    return root


def test_a_package_reads_its_classes_in_order_and_refuses_an_unlabelled_image(tmp_path: Path) -> None:
    found = data.package(make_package(tmp_path / "ok"))
    assert found.names == ["a", "b"] and all(len(found.images[s]) == 1 for s in data.SPLITS)
    with pytest.raises(FileNotFoundError, match="without a label"):
        data.package(make_package(tmp_path / "bad", label_last=False))
    (tmp_path / "ok" / "model.pt").unlink()
    with pytest.raises(FileNotFoundError, match=r"model\.pt"):
        data.package(tmp_path / "ok")


def test_the_head_decodes_distances_around_each_cell_centre() -> None:
    box = np.zeros((4, 2, 2), dtype=np.float32)
    box[:, 1, 0] = [0.5, 0.5, 1.5, 1.5]
    logits = np.full((3, 2, 2), -20.0, dtype=np.float32)
    logits[2, 1, 0] = 20.0
    boxes, scores = detect_eval.decode([box], [logits], [8])
    assert np.allclose(boxes[2], [0.0, 8.0, 16.0, 24.0])
    assert scores[2].argmax() == 2 and scores[2, 2] > 0.99 and scores[0].max() < 1e-6


def test_kept_boxes_are_the_highest_scores_above_conf() -> None:
    boxes = np.arange(16, dtype=np.float64).reshape(4, 4)
    scores = np.array([[0.1, 0.9], [0.8, 0.2], [0.05, 0.01], [0.3, 0.7]])
    out = detect_eval.kept(boxes, scores, max_det=3, conf=0.25)
    assert out[:, 4].tolist() == [0.9, 0.8, 0.7] and out[:, 5].tolist() == [1, 0, 1]
    assert np.array_equal(out[0, :4], boxes[0])


def test_map_is_one_for_exact_boxes_and_lower_for_shifted_ones(tmp_path: Path) -> None:
    pytest.importorskip("pycocotools")
    pil_image = pytest.importorskip("PIL.Image")

    root = make_package(tmp_path / "p")
    image = root / "data" / "images" / "val" / "x.jpg"
    pil_image.fromarray(np.zeros((100, 200, 3), dtype=np.uint8)).save(image)
    found = data.package(root)
    exact = np.array([[90.0, 45.0, 110.0, 55.0, 0.9, 0.0]])
    assert detect_eval.mean_ap([image], found, [exact])[0] == pytest.approx(1.0)
    shifted = exact + np.array([[8.0, 4.0, 8.0, 4.0, 0.0, 0.0]])
    assert detect_eval.mean_ap([image], found, [shifted])[0] < 0.5


GATE = {"gate": 0.02, "min_float_share": 0.5}
TRAINED = {"map50_95": 0.40, "map50": 0.56, "imgsz": 640}


def scored(float_map: float, int8_map: float | None = None) -> dict:
    report = {"float": {"map50_95": float_map}}
    return report | ({"int8": {"map50_95": int8_map}} if int8_map is not None else {})


def test_the_gate_passes_a_close_int8_of_a_float_near_what_the_server_measured() -> None:
    report = detect_eval.verdict(scored(0.3635, 0.3496), TRAINED, GATE)
    assert report["passes"] and report["drop"] == pytest.approx(0.0139) and report["float_share"] > 0.9


def test_the_gate_refuses_a_package_whose_labels_miss_its_model_though_int8_keeps_up() -> None:
    assert not detect_eval.verdict(scored(0.004, 0.003), TRAINED, GATE)["passes"]
    assert detect_eval.verdict(scored(0.004, 0.003), None, GATE)["passes"]


def test_the_gate_refuses_an_int8_that_fell_too_far() -> None:
    assert not detect_eval.verdict(scored(0.3635, 0.30), TRAINED, GATE)["passes"]


def test_the_broken_package_moves_each_label_to_the_next_class() -> None:
    assert data.shifted("79 0.5 0.5 0.1 0.2", 80) == "0 0.5 0.5 0.1 0.2"
    assert data.shifted("3 0.1 0.2 0.3 0.4", 80) == "4 0.1 0.2 0.3 0.4"


def test_deploy_refuses_a_run_without_a_passing_int8_score_on_val(tmp_path: Path) -> None:
    from srpipe.tasks.detect import quant

    run = tmp_path / "run"
    (run / "int8").mkdir(parents=True)
    with pytest.raises(ValueError, match="passed the gate"):
        quant.deploy({}, run)
    (run / "int8" / "map_val.yaml").write_text(yaml.safe_dump(scored(0.004, 0.003) | {"passes": False}), "utf-8")
    with pytest.raises(ValueError, match="passed the gate"):
        quant.deploy({}, run)


def test_calibration_batches_are_the_chips_letterbox_in_uint8(tmp_path: Path) -> None:
    torch = pytest.importorskip("torch")
    pil_image = pytest.importorskip("PIL.Image")
    from srpipe.tasks.detect import quant

    rng = np.random.default_rng(2)
    paths = []
    for k, shape in enumerate([(30, 50), (50, 30), (40, 40)]):
        paths.append(tmp_path / f"{k}.png")
        pil_image.fromarray(rng.integers(0, 256, (*shape, 3), dtype=np.uint8)).save(paths[-1])
    cfg = {"size": 32, "pad": 114, "quant": {"batch": 2}}
    batches = list(quant.Batches(paths, cfg))
    assert len(quant.Batches(paths, cfg)) == 2 and [b.shape for b in batches] == [(2, 3, 32, 32), (1, 3, 32, 32)]
    want = data.letterbox(data.rgb_of(paths[2]), 32, 114)[0].transpose(2, 0, 1)
    assert batches[1].dtype == torch.uint8 and np.array_equal(batches[1][0].numpy(), want)
