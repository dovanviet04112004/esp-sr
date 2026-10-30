"""The detect chain of KEHOACH 3.18 from a server's package to an esp-dl graph. import locks the package's splits in a
run and exports model.pt to ONNX through ml/detect_ref/ultralytics, held to model.pt; ptq runs Espressif's YOLO26
recipe on the repo's esp-ppq, calibrated on a seeded train sample through the chip's letterbox, drops the one-to-many
branch, splits each one-to-one output into box and class tensors, and writes <run>/int8/{graph.native, model.espdl},
the .espdl without model->test()'s pair; deploy locks a run that passed the val gate for the vision_0 slot.
Run: python -m srpipe.tasks.detect.quant import | ptq <run> | deploy <run>"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
from pathlib import Path

import numpy as np

from srpipe.core.config import ML_ROOT, apply_overrides, data_paths, load_yaml
from srpipe.core.run_dir import create_run_dir
from srpipe.export import update_lock
from srpipe.scenes import refs
from srpipe.tasks.detect import CONFIG, data

BRANCH = "detect"
SLOT = "vision_0"
REFS = ML_ROOT / "detect_ref"
TARGET = "esp32s3"
ONE_TO_ONE = ("one2one_p3", "one2one_p4", "one2one_p5")
ONE_TO_MANY = ("one2many_p3", "one2many_p4", "one2many_p5")
BOX_CHANNELS = 4


def import_run(cfg: dict, paths: dict) -> Path:
    """A run of the package: its three splits' image lists locked, model.onnx exported and held to model.pt, the classes
    of model.pt the same as data.yaml's."""
    found = data.package(paths["interim"] / cfg["package"])
    with tempfile.TemporaryDirectory() as folder:
        lists = []
        for split in data.SPLITS:
            listing = Path(folder) / f"{split}.txt"
            listing.write_text("".join(f"{p.name}\n" for p in found.images[split]), encoding="utf-8")
            lists.append(listing)
        run = create_run_dir(paths["artifacts"], BRANCH, cfg, lists)
    spec = cfg["export"]
    refs.run(
        "ultralytics",
        str(found.model),
        str(run / "model.onnx"),
        "--size",
        str(cfg["size"]),
        "--check",
        str(found.root / "data" / "images" / "val"),
        "--count",
        str(spec["check_images"]),
        "--rtol",
        str(spec["rtol"]),
        projects=REFS,
    )
    meta = json.loads((run / "model.onnx.json").read_text(encoding="utf-8"))
    if meta["names"] != found.names:
        raise ValueError(f"{found.model}: classes {meta['names'][:3]}… differ from data.yaml's {found.names[:3]}…")
    return run


def ancestors(graph, names) -> set:
    found, stack = set(), [graph.outputs[n].source_op for n in names if graph.outputs[n].source_op is not None]
    while stack:
        op = stack.pop()
        if op not in found:
            found.add(op)
            stack.extend(v.source_op for v in op.inputs if v.source_op is not None)
    return found


def calibration_sample(cfg: dict, found: data.Package) -> list[Path]:
    """quant.calib_images of train drawn by the seed, in name order; TQT trains on the first quant.tqt_images."""
    spec = cfg["quant"]
    pool = found.images["train"]
    picks = np.random.default_rng(spec["seed"]).choice(
        len(pool), size=min(spec["calib_images"], len(pool)), replace=False
    )
    return [pool[k] for k in sorted(picks)]


class Batches:
    """Images letterboxed as the chip does, in uint8 batches of quant.batch, decoded afresh on each pass."""

    def __init__(self, images: list[Path], cfg: dict) -> None:
        self.images, self.cfg = images, cfg

    def __len__(self) -> int:
        return -(-len(self.images) // self.cfg["quant"]["batch"])

    def __iter__(self):
        import torch

        size, pad, batch = self.cfg["size"], self.cfg["pad"], self.cfg["quant"]["batch"]
        for i in range(0, len(self.images), batch):
            squares = [data.letterbox(data.rgb_of(p), size, pad)[0] for p in self.images[i : i + batch]]
            yield torch.from_numpy(np.stack(squares).transpose(0, 3, 1, 2).copy())


def one_block_tqt(tqt: dict):
    """ESP-PPQ's TQT holding one block's cached tensors at a time: its own loop keeps a block's until the next block's
    are collected, twice the RAM at the 256 x 256 maps."""
    from esp_ppq.quantization.optim import TrainedQuantizationThresholdPass

    class OneBlockTQT(TrainedQuantizationThresholdPass):
        def optimize(self, graph, dataloader, executor, collate_fn, **kwargs) -> None:
            blocks = self.split_graph_into_blocks(
                graph=graph,
                executing_order=executor._executing_order,
                blocksize=self.block_size,
                interested_layers=self.interested_layers,
            )
            for k, block in enumerate(blocks):
                qt_inputs, fp_outputs = self.collect(
                    graph=graph,
                    block=block,
                    executor=executor,
                    dataloader=dataloader,
                    collate_fn=collate_fn,
                    collecting_device=self.collecting_device,
                )
                before, after = self.finetune(
                    steps=self.steps,
                    learning_rate=self.lr,
                    block=block,
                    qt_inputs=qt_inputs,
                    fp_outputs=fp_outputs,
                    executor=executor,
                )
                del qt_inputs, fp_outputs
                print(f"tqt block {k + 1}/{len(blocks)} {block.sp.name}: loss {before:.4f} -> {min(before, after):.4f}")

    return OneBlockTQT(
        steps=tqt["steps"],
        lr=tqt["lr"],
        int_lambda=tqt["int_lambda"],
        block_size=tqt["block_size"],
        collecting_device="cpu",
    )


def dispatch(graph, quantizer, cfg: dict) -> None:
    """Every op to a platform: int8 by default, the patterns of quant.int16 and the one-to-one output Concat at 16 bits
    and float, the one-to-many branch in float as it is dropped after."""
    import esp_ppq.lib as ppq_lib
    from esp_ppq.api import get_target_platform
    from esp_ppq.core import TargetPlatform

    table = ppq_lib.Dispatcher(graph=graph, method="conservative").dispatch(quantizer.quant_operation_types)
    for name, platform in table.items():
        if platform == TargetPlatform.UNSPECIFIED:
            table[name] = TargetPlatform(quantizer.target_platform)
    wide = get_target_platform(TARGET, 16)
    for name in table:
        if any(re.search(p, name) for p in cfg["quant"]["int16"]):
            table[name] = wide
    for op in ancestors(graph, ONE_TO_MANY) - ancestors(graph, ONE_TO_ONE):
        table[op.name] = TargetPlatform.FP32
    for name in ONE_TO_ONE:
        table[graph.outputs[name].source_op.name] = TargetPlatform.FP32
    for op in graph.operations.values():
        quantizer.quantize_operation(op_name=op.name, platform=table[op.name])


def pruned(graph) -> None:
    """Drop every op no output reaches and every variable nothing reads, until none is left."""
    while True:
        dead = [
            op for op in graph.operations.values() if not any(v.name in graph.outputs or v.dest_ops for v in op.outputs)
        ]
        for op in dead:
            for var in list(op.inputs):
                op.inputs.remove(var)
                if op in var.dest_ops:
                    var.dest_ops.remove(op)
            graph.remove_operation(op, keep_coherence=False)
        unread = [
            v
            for v in graph.variables.values()
            if v.name not in graph.inputs and v.name not in graph.outputs and not v.dest_ops
        ]
        for var in unread:
            graph.variables.pop(var.name)
        if not dead and not unread:
            return


def split_heads(graph, n_classes: int) -> None:
    """The one-to-many outputs dropped; each one-to-one Concat removed, its box and class inputs the outputs
    <name>_box and <name>_cls, in the order espressif/yolo26 reads them."""
    for name in ONE_TO_MANY:
        graph.outputs.pop(name)
    pruned(graph)
    heads = {}
    for name in ONE_TO_ONE:
        concat = graph.variables[name].source_op
        widths = [v.shape[1] for v in concat.inputs]
        if concat.type != "Concat" or widths != [BOX_CHANNELS, n_classes]:
            raise ValueError(
                f"{name}: want Concat of [{BOX_CHANNELS}, {n_classes}] channels, got {concat.type} of {widths}"
            )
        for var, tail in zip(concat.inputs, ("box", "cls"), strict=True):
            graph.variables.pop(var.name)
            var._name = f"{name}_{tail}"
            graph.variables[var.name] = var
            var.dest_ops.remove(concat)
            heads[var.name] = var
        graph.outputs.pop(name)
        graph.remove_operation(concat, keep_coherence=False)
    graph.outputs.clear()
    for name in ONE_TO_ONE:
        for tail in ("box", "cls"):
            graph.outputs[f"{name}_{tail}"] = heads[f"{name}_{tail}"]
    pruned(graph)


def ptq(cfg: dict, run: Path, paths: dict) -> Path:
    """<run>/int8: the quantised graph as ESP-PPQ's native file and as .espdl, a val image kept for model->test()."""
    import esp_ppq.lib as ppq_lib
    import torch
    from esp_ppq.api import get_target_platform
    from esp_ppq.api.interface import load_onnx_graph
    from esp_ppq.core import QuantizationVisibility
    from esp_ppq.executor import TorchExecutor
    from esp_ppq.quantization.optim import (
        EspdlLUTFusionPass,
        ParameterQuantizePass,
        PassiveParameterQuantizePass,
        QuantAlignmentPass,
        QuantizeFusionPass,
        QuantizeSimplifyPass,
        RuntimeCalibrationPass,
    )

    from srpipe.compress.quant import export_espdl

    spec = cfg["quant"]
    found = data.package(paths["interim"] / cfg["package"])
    meta = json.loads((run / "model.onnx.json").read_text(encoding="utf-8"))
    device = "cuda" if torch.cuda.is_available() else "cpu"
    torch.manual_seed(spec["seed"])
    graph = load_onnx_graph(onnx_import_file=str(run / "model.onnx"))
    quantizer = ppq_lib.Quantizer(platform=get_target_platform(TARGET, 8), graph=graph)
    dispatch(graph, quantizer, cfg)
    executor = TorchExecutor(graph=graph, device=device)
    executor.tracing_operation_meta(inputs=torch.zeros(1, 3, cfg["size"], cfg["size"], device=device))
    sample = calibration_sample(cfg, found)

    def collate(x):
        return x.to(device).float() / 255.0

    ppq_lib.Pipeline(
        [
            QuantizeSimplifyPass(),
            QuantizeFusionPass(activation_type=quantizer.activation_fusion_types),
            ParameterQuantizePass(),
            RuntimeCalibrationPass(method=spec["calibration"]),
        ]
    ).optimize(
        calib_steps=spec["calib_steps"],
        collate_fn=collate,
        graph=graph,
        dataloader=Batches(sample, cfg),
        executor=executor,
    )
    ppq_lib.Pipeline(
        [
            one_block_tqt(spec["tqt"]),
            PassiveParameterQuantizePass(clip_visiblity=QuantizationVisibility.EXPORT_WHEN_ACTIVE),
            QuantAlignmentPass(elementwise_alignment=spec["alignment"]),
            EspdlLUTFusionPass(target_ops=["Swish"], lut_step=spec["lut_step"]),
        ]
    ).optimize(
        collate_fn=collate,
        graph=graph,
        dataloader=list(Batches(sample[: spec["tqt_images"]], cfg)),
        executor=executor,
    )
    split_heads(graph, meta["nc"])
    out = run / "int8"
    export_espdl.save_native(graph, out / "graph.native")
    return export_espdl.export(graph, out / "model.espdl", int16_lut_step=spec["lut_step"])


def deploy(cfg: dict, run: Path) -> tuple[Path, Path]:
    """<run>/int8/model.espdl into firmware/models/detect with meta.json and the lock's detect row, slot vision_0 and
    detect.yaml's chip fields, refused unless the run's val score holds int8 and passed KEHOACH 3.18 step 4's gate."""
    scored = run / "int8" / "map_val.yaml"
    report = load_yaml(scored) if scored.exists() else {}
    if "int8" not in report or not report["passes"]:
        raise ValueError(f"{run}: no int8 score on val that passed the gate, {report or 'none'}")
    meta = json.loads((run / "model.onnx.json").read_text(encoding="utf-8"))
    target = update_lock.MODELS / BRANCH
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(run / "int8" / "model.espdl", target / f"{BRANCH}.espdl")
    fields = {
        "slot": SLOT,
        "size": cfg["size"],
        "pad": cfg["pad"],
        "stride": meta["stride"],
        "names": meta["names"],
        "val": {k: report[k] for k in ("images", "float", "int8", "drop") if k in report},
        "trained": meta.get("trained"),
        "chip": load_yaml(CONFIG)["chip"],
    }
    files = [update_lock.Deployed(target / f"{BRANCH}.espdl", BRANCH, "espdl")]
    return update_lock.record(BRANCH, run, files, fields, grid_bound=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("import", help="the package as a run: splits locked, model.pt exported to ONNX and checked")
    start.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    rung = sub.add_parser("ptq", help="Espressif's YOLO26 recipe into <run>/int8/")
    rung.add_argument("run", type=Path)
    lock = sub.add_parser("deploy", help="a run that passed the val gate into firmware/models/detect and the lock")
    lock.add_argument("run", type=Path)
    args = parser.parse_args(argv)
    paths = data_paths()
    if args.command == "import":
        print(import_run(apply_overrides(load_yaml(CONFIG), args.overrides), paths))
        return 0
    cfg = load_yaml(args.run / "config.resolved.yaml")
    if args.command == "deploy":
        for path in deploy(cfg, args.run):
            print(path)
        return 0
    print(ptq(cfg, args.run, paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
