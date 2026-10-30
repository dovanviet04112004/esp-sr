"""A server's YOLO26 model.pt exported to the ONNX the detect chain imports (KEHOACH 3.18), then held to model.pt.

Head and Attention follow scripts/export.py of Espressif's YOLO26 tutorial (esp-dl, MIT): both branches' six raw maps,
no box decoding, static shapes. <out>.json: names, strides, size, versions, the check, the mAP model.pt trained to.
Run: uv run --project ml/detect_ref/ultralytics python ml/detect_ref/ultralytics/run.py <model.pt> <out.onnx>
     --size <S> --check <image dir> [--count 8] [--rtol 1e-3]"""

from __future__ import annotations

import argparse
import hashlib
import json
import types
from pathlib import Path

import numpy as np
import onnx
import onnxruntime
import onnxsim
import torch
import ultralytics
from PIL import Image
from ultralytics import YOLO
from ultralytics.nn.modules import Attention, Detect

OPSET = 13
OUTPUTS = ["one2many_p3", "one2many_p4", "one2many_p5", "one2one_p3", "one2one_p4", "one2one_p5"]
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}


def raw_maps(self: Detect, x: list[torch.Tensor]) -> list[torch.Tensor]:
    many = [torch.cat((self.cv2[i](x[i]), self.cv3[i](x[i])), 1) for i in range(self.nl)]
    one = [torch.cat((self.one2one_cv2[i](x[i]), self.one2one_cv3[i](x[i])), 1) for i in range(self.nl)]
    return many + one


def static_attention(self: Attention, x: torch.Tensor) -> torch.Tensor:
    _, c, h, w = x.shape
    q, k, v = (
        self.qkv(x)
        .view(-1, self.num_heads, self.key_dim * 2 + self.head_dim, h * w)
        .split([self.key_dim, self.key_dim, self.head_dim], dim=2)
    )
    attn = ((q.transpose(-2, -1) @ k) * self.scale).softmax(dim=-1)
    return self.proj((v @ attn.transpose(-2, -1)).view(-1, c, h, w) + self.pe(v.reshape(-1, c, h, w)))


def trained(ckpt: dict) -> dict | None:
    """The val mAP Ultralytics measured while the server trained, and the image size it trained at, when model.pt
    keeps them."""
    scores = ckpt.get("train_metrics") or {}
    if "metrics/mAP50-95(B)" not in scores:
        return None
    return {
        "map50_95": float(scores["metrics/mAP50-95(B)"]),
        "map50": float(scores["metrics/mAP50(B)"]),
        "imgsz": (ckpt.get("train_args") or {}).get("imgsz"),
    }


def patched(model_pt: Path) -> tuple[torch.nn.Module, dict]:
    """The checkpoint's network in eval mode, batch norms fused, both branches kept, with its names and strides."""
    yolo = YOLO(str(model_pt))
    ckpt = yolo.ckpt if isinstance(yolo.ckpt, dict) else {}
    net = yolo.model.float().eval()
    head = net.model[-1]
    # Detect.fuse drops the one-to-many branch; the chain prunes it after quantisation (KEHOACH 3.18).
    head.fuse = lambda *args, **kwargs: None
    net = net.fuse(verbose=False)
    head.forward = types.MethodType(raw_maps, head)
    for m in net.modules():
        if isinstance(m, Attention):
            m.forward = types.MethodType(static_attention, m)
    meta = {
        "names": [yolo.names[i] for i in sorted(yolo.names)],
        "nc": int(head.nc),
        "stride": [int(s) for s in head.stride.tolist()],
        "reg_max": int(head.reg_max),
        "trained_with": ckpt.get("version"),
        "trained": trained(ckpt),
    }
    return net, meta


def export(net: torch.nn.Module, size: int, out: Path) -> None:
    image = torch.zeros(1, 3, size, size)
    torch.onnx.export(
        net,
        image,
        str(out),
        opset_version=OPSET,
        input_names=["images"],
        output_names=OUTPUTS,
        do_constant_folding=False,
        dynamo=False,
    )
    simple, checked = onnxsim.simplify(onnx.load(str(out)))
    if not checked:
        raise RuntimeError(f"{out}: onnxsim could not check the simplified graph against the exported one")
    onnx.save(simple, str(out))


def images_of(folder: Path, count: int, size: int) -> list[np.ndarray]:
    """The first count images of folder, sorted by name, RGB scaled to size x size, as 1x3xSxS float in 0..1."""
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)[:count]
    if not files:
        raise FileNotFoundError(f"{folder}: no image to check the export on")
    return [
        np.ascontiguousarray(
            np.asarray(Image.open(f).convert("RGB").resize((size, size)), dtype=np.float32).transpose(2, 0, 1)[None]
            / np.float32(255.0)
        )
        for f in files
    ]


def worst_gap(net: torch.nn.Module, out: Path, images: list[np.ndarray]) -> float:
    """The largest gap of any ONNX output to the network's on images, as a share of that output's largest magnitude."""
    session = onnxruntime.InferenceSession(str(out), providers=["CPUExecutionProvider"])
    worst = 0.0
    for x in images:
        with torch.no_grad():
            want = [t.numpy() for t in net(torch.from_numpy(x))]
        got = session.run(OUTPUTS, {"images": x})
        for w, g in zip(want, got, strict=True):
            worst = max(worst, float(np.abs(w - g).max() / max(float(np.abs(w).max()), 1e-12)))
    return worst


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("model", type=Path, help="the server's model.pt")
    parser.add_argument("out", type=Path, help="the ONNX to write; <out>.json beside it")
    parser.add_argument("--size", type=int, required=True, help="S of the square input, a multiple of 32")
    parser.add_argument("--check", type=Path, required=True, help="a folder of images, the package's val")
    parser.add_argument("--count", type=int, default=8, help="images the export is checked on")
    parser.add_argument("--rtol", type=float, default=1e-3, help="largest gap allowed, as a share of an output's peak")
    args = parser.parse_args(argv)
    if args.size % 32:
        parser.error(f"--size {args.size}: not a multiple of 32")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    net, meta = patched(args.model)
    export(net, args.size, args.out)
    images = images_of(args.check, args.count, args.size)
    gap = worst_gap(net, args.out, images)
    meta |= {
        "size": args.size,
        "opset": OPSET,
        "outputs": OUTPUTS,
        "model_sha256": hashlib.sha256(args.model.read_bytes()).hexdigest(),
        "ultralytics": ultralytics.__version__,
        "torch": torch.__version__,
        "check": {"images": len(images), "worst_gap": gap, "rtol": args.rtol},
    }
    Path(f"{args.out}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{args.out}: {meta['nc']} classes, strides {meta['stride']}, worst gap to model.pt {gap:.2e}")
    if gap > args.rtol:
        print(f"{args.out}: the export is {gap:.2e} off model.pt, more than {args.rtol:.0e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
