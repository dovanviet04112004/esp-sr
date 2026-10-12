"""ReDimNet2 b0, the chip path of KEHOACH 3.17, along command_ctc's path: import writes the survey's pinned b0 as a run,
model.pt with its network's code; ptq rewrites it for esp-dl without changing what it computes, from its log-mel
features to the embedding, quantises it with each calibration of rung 2 (3.14) into <run>/int8/<calibration>/, and
rows each beside float in <run>/int8/ladder.yaml on the survey's material, keeping the board probe's window; int16 adds
rung 3's rows on rung 2's best; deploy locks a row with its threshold into firmware/models/speaker/; music scores the
locked row with music in the room. Run: python -m srpipe.tasks.speaker.quant import | <command> <run>"""

from __future__ import annotations

import argparse
import copy
import importlib
import json
import math
import shutil
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import yaml
from torch import nn
from torch.nn import functional

from srpipe.compress.quant import esp_ppq_patches, export_espdl, mixed_espdl, ptq_espdl
from srpipe.core import run_dir
from srpipe.core.config import CONFIGS, ML_ROOT, data_paths, device_of, load_yaml
from srpipe.dsp.spec import spk_fbank
from srpipe.export import pack_models, update_lock
from srpipe.generated import grid, speaker
from srpipe.scenes import refs
from srpipe.scenes import speaker as survey
from srpipe.tasks.speaker.postproc import score

CONFIG = CONFIGS / "models" / "speaker.yaml"
BRANCH = LADDER = "speaker"
PACKAGE = "redimnet2"
MODEL_FILE = "model.pt"
INT8_DIR = "int8"
GRAPH_FILE = "graph.native"
LADDER_FILE = "ladder.yaml"
WINDOW_FILE = "board_window.npy"  # float log-mel (1, 1, mels, frames) the probe runs
DEPLOY_FILE = "deploy.yaml"  # in <run>/int8/<row>/: the threshold, its figures
MUSIC_FILE = "music.yaml"  # in <run>/int8/<row>/: its figures with music in the room
COHORT_FILE, COHORT_ENTRY = "cohort.bin", "speaker_cohort"  # float32 rows of dims
COHORT_STREAM = 3  # rng stream of the cohort, beside the survey's
ASTP_FLOOR = 1e-7  # poolings.py ASTP's variance floor, at the pin
CHIP_BITS = 8
PCM_FULL_SCALE = 32768.0


def explicit_padding(model: nn.Module) -> nn.Module:
    """model with each padding='same' convolution given its symmetric pads, since esp-dl refuses auto_pad on 1-d."""
    for m in model.modules():
        if isinstance(m, nn.Conv1d | nn.Conv2d) and m.padding == "same":
            spans = [(k - 1) * d for k, d in zip(m.kernel_size, m.dilation, strict=True)]
            if m.padding_mode != "zeros" or any(s % 2 for s in spans):
                raise ValueError(f"{m}: 'same' needs uneven pads or another padding mode")
            m.padding = tuple(s // 2 for s in spans)
    return model


def block_diagonal(conv: nn.Conv1d | nn.Conv2d) -> nn.Conv1d | nn.Conv2d:
    """conv as one ungrouped convolution whose weights are zero across groups: the same outputs."""
    full = type(conv)(
        conv.in_channels,
        conv.out_channels,
        conv.kernel_size,
        stride=conv.stride,
        padding=conv.padding,
        dilation=conv.dilation,
        bias=conv.bias is not None,
        padding_mode=conv.padding_mode,
    )
    ins, outs = conv.in_channels // conv.groups, conv.out_channels // conv.groups
    with torch.no_grad():
        full.weight.zero_()
        for g in range(conv.groups):
            full.weight[g * outs : (g + 1) * outs, g * ins : (g + 1) * ins] = conv.weight[g * outs : (g + 1) * outs]
        if conv.bias is not None:
            full.bias.copy_(conv.bias)
    return full


def ungrouped(model: nn.Module) -> nn.Module:
    """model with each grouped convolution but depthwise ones made block_diagonal: esp-dl runs a grouped convolution
    only as depthwise, its output channels taken as its input channels."""
    for parent in list(model.modules()):
        for name, m in list(parent.named_children()):
            grouped = isinstance(m, nn.Conv1d | nn.Conv2d) and m.groups > 1
            if grouped and not m.groups == m.in_channels == m.out_channels:
                setattr(parent, name, block_diagonal(m))
    return model


class ChannelNorm(nn.Module):
    """redimnet2's channels_first LayerNorm as layer_norm over the channels moved last: the same norm, its weights one
    vector over the last axis, where the original multiplies by weights shaped (C, 1, ...) that ESP-PPQ leaves in that
    shape over channels-last data, broadcast along the wrong axis on the chip."""

    def __init__(self, norm: nn.Module) -> None:
        super().__init__()
        self.weight, self.bias, self.eps = norm.weight, norm.bias, norm.eps

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        last = x.ndim - 1
        moved = x.movedim(1, last)
        return functional.layer_norm(moved, (moved.shape[-1],), self.weight, self.bias, self.eps).movedim(last, 1)


def channel_norms(model: nn.Module) -> nn.Module:
    """model with each channels_first LayerNorm of redimnet2 as a ChannelNorm."""
    for parent in list(model.modules()):
        for name, m in list(parent.named_children()):
            if getattr(m, "data_format", None) == "channels_first":
                setattr(parent, name, ChannelNorm(m))
    return model


class StageSum(nn.Module):
    """redimnet2's weigth1d as one depthwise 1x1 convolution an input, kernels its softmaxed weights, the outputs added:
    the same sum, where weigth1d stacks its inputs and sums over the stack, an axis esp-dl sums slowly
    (measurements/latency.md 25)."""

    def __init__(self, stage: nn.Module) -> None:
        super().__init__()
        if stage.sequential:
            raise ValueError("StageSum reads weigth1d's stacked form")
        w = torch.softmax(stage.w.detach(), dim=1)[0, :, :, 0]
        self.scales = nn.ModuleList()
        for row in w:
            conv = nn.Conv1d(len(row), len(row), 1, groups=len(row), bias=False)
            with torch.no_grad():
                conv.weight.copy_(row[:, None, None])
            self.scales.append(conv)

    def forward(self, xs: list[torch.Tensor]) -> torch.Tensor:
        out = self.scales[0](xs[0])
        for scale, x in zip(self.scales[1:], xs[1:], strict=True):
            out = out + scale(x)
        return out


def stage_sums(model: nn.Module) -> nn.Module:
    """model with each weigth1d of redimnet2 as a StageSum."""
    for parent in list(model.modules()):
        for name, m in list(parent.named_children()):
            if type(m).__name__ == "weigth1d":
                setattr(parent, name, StageSum(m))
    return model


def folded_head(bn: nn.BatchNorm1d, linear: nn.Linear) -> nn.Linear:
    """linear after bn in eval as one Linear; ESP-PPQ runs a BatchNorm only over 3-d or 4-d tensors."""
    scale = bn.weight / torch.sqrt(bn.running_var + bn.eps)
    shift = bn.bias - bn.running_mean * scale
    head = nn.Linear(linear.in_features, linear.out_features)
    with torch.no_grad():
        head.weight.copy_(linear.weight * scale[None, :])
        head.bias.copy_(linear.bias + linear.weight @ shift)
    return head


class ContextPool(nn.Module):
    """ASTP with global context as esp-dl can run it: the context's part of the attention's first projection is added
    per channel, where ASTP concatenates the context expanded over time, an Expand esp-dl lacks; squares are products,
    since esp-dl runs Pow in float. Its j-th channel is ASTP's channel order[j], in and out."""

    def __init__(self, astp: nn.Module, order: torch.Tensor) -> None:
        super().__init__()
        n, w = astp.in_dim, astp.linear1.weight
        self.frames = nn.Conv1d(n, w.shape[0], 1, bias=False)
        self.context = nn.Conv1d(2 * n, w.shape[0], 1)
        self.linear2 = nn.Conv1d(astp.linear2.in_channels, n, 1)
        with torch.no_grad():
            self.frames.weight.copy_(w[:, :n][:, order])
            self.context.weight.copy_(torch.cat([w[:, n : 2 * n][:, order], w[:, 2 * n :][:, order]], dim=1))
            self.context.bias.copy_(astp.linear1.bias)
            self.linear2.weight.copy_(astp.linear2.weight[order])
            self.linear2.bias.copy_(astp.linear2.bias[order])

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        mean = torch.mean(x, dim=-1, keepdim=True)
        std = torch.sqrt(torch.var(x, dim=-1, keepdim=True) + ASTP_FLOOR)
        alpha = torch.tanh(self.frames(x) + self.context(torch.cat((mean, std), dim=1)))
        alpha = torch.softmax(self.linear2(alpha), dim=2)
        mean = torch.sum(alpha * x, dim=2)
        var = torch.sum(alpha * (x * x), dim=2) - mean * mean
        return torch.cat([mean, torch.sqrt(var.clamp(min=ASTP_FLOOR))], dim=1)


def swapped(conv: nn.Conv2d) -> nn.Conv2d:
    """conv over (..., frames, bands) where it ran over (..., bands, frames): its kernel, stride, pads and dilation
    transposed."""
    out = nn.Conv2d(
        conv.in_channels,
        conv.out_channels,
        conv.kernel_size[::-1],
        stride=conv.stride[::-1],
        padding=conv.padding[::-1],
        dilation=conv.dilation[::-1],
        groups=conv.groups,
        bias=conv.bias is not None,
    )
    with torch.no_grad():
        out.weight.copy_(conv.weight.transpose(2, 3))
        if conv.bias is not None:
            out.bias.copy_(conv.bias)
    return out


def unit_upsamples(model: nn.Module) -> nn.Module:
    """model without its nearest Upsamples by 1, which give their input back and export as a Resize copying it."""
    for parent in list(model.modules()):
        for name, m in list(parent.named_children()):
            factors = m.scale_factor if isinstance(m, nn.Upsample) else None
            if factors is not None and m.mode == "nearest" and set(np.atleast_1d(factors).tolist()) == {1.0}:
                setattr(parent, name, nn.Identity())
    return model


class To1d(nn.Module):
    """redimnet2's to1d from time-major maps: (b, c, frames, bands) to (b, bands x c, frames), the same order."""

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        b, c, t, f = x.shape
        return x.permute(0, 3, 1, 2).reshape(b, f * c, t)


class To2d(nn.Module):
    """redimnet2's to2d into time-major maps: (b, bands x c, frames) to (b, c, frames, bands)."""

    def __init__(self, bands: int, channels: int) -> None:
        super().__init__()
        self.bands, self.channels = bands, channels

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x.reshape(x.shape[0], self.bands, self.channels, x.shape[2]).permute(0, 2, 3, 1)


class KeysFirstAttention(nn.Module):
    """redimnet2's MultiHeadAttention with each head's weights laid (keys, queries) and its output (features, queries):
    the same sums, where esp-dl's int8 MatMul takes its output columns 16 at a time and the weights times the values
    had only a head's few features as columns."""

    def __init__(self, attention: nn.Module) -> None:
        super().__init__()
        if attention.qk_norm or attention.qk_rope:
            raise ValueError("KeysFirstAttention runs plain scaled dot-product heads")
        self.attention = attention

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        a, (n, t, _) = self.attention, x.shape
        heads, width = a.num_heads, a.head_dim

        def features_first(proj: nn.Linear) -> torch.Tensor:
            return proj(x).view(n, t, heads, width).permute(0, 2, 3, 1).reshape(n * heads, width, t)

        keys = a.k_proj(x).view(n, t, heads, width).transpose(1, 2).reshape(n * heads, t, width)
        weights = torch.softmax(torch.bmm(keys, features_first(a.q_proj)) * a.scaling, dim=1)
        out = torch.bmm(features_first(a.v_proj), weights).view(n, heads, width, t)
        return a.out_proj(out.permute(0, 3, 1, 2).reshape(n, t, heads * width))


def keys_first_attention(model: nn.Module) -> nn.Module:
    """model with each of redimnet2's MultiHeadAttention as KeysFirstAttention."""
    for parent in list(model.modules()):
        for name, m in list(parent.named_children()):
            if type(m).__name__ == "MultiHeadAttention":
                setattr(parent, name, KeysFirstAttention(m))
    return model


def time_major(model: nn.Module) -> nn.Module:
    """model's 2-d maps laid (b, c, frames, bands): each Conv2d swapped, to1d and to2d as To1d and To2d. On the chip a
    map is then (frames, bands, c), so going to and from 1-d (frames, bands x c) moves no data once lean_transposes
    folds the export's Transposes around it (measurements/latency.md 25)."""
    for parent in list(model.modules()):
        for name, m in list(parent.named_children()):
            if isinstance(m, nn.Conv2d):
                setattr(parent, name, swapped(m))
            elif type(m).__name__ == "to1d":
                setattr(parent, name, To1d())
            elif type(m).__name__ == "to2d":
                setattr(parent, name, To2d(m.f, m.c))
    return model


class Embed(nn.Module):
    """b0 from log-mel features (1, 1, frames, mels) to the embedding the extractor gives (KEHOACH 3.17)."""

    def __init__(self, wrap: nn.Module) -> None:
        super().__init__()
        if wrap.pad_right_samples is not None or wrap.before_pool_offset is not None or wrap.bn2 is not None:
            raise ValueError("the extractor pads, offsets or norms where Embed does not")
        if not wrap.pool.global_context_att or wrap.return_all_outputs:
            raise ValueError("Embed reads a pool with global context and one backbone output")
        b = wrap.backbone
        if b.is_subnet or b.agg_gnorm or any(b._stage_has_dual):
            raise ValueError("Embed runs one plain stage after another")
        rewritten = stage_sums(channel_norms(ungrouped(explicit_padding(copy.deepcopy(b)))))
        self.backbone = time_major(ptq_espdl.one_channel_inputs(unit_upsamples(keys_first_attention(rewritten))))
        to2d, head = self.backbone.fin_to2d, self.backbone.head
        channels, bands = head.out_channels if isinstance(head, nn.Conv2d) else to2d.channels, to2d.bands
        order = torch.arange(channels * bands).reshape(channels, bands).t().reshape(-1)
        self.pool = ContextPool(wrap.pool, order)
        self.head = folded_head(wrap.bn, wrap.linear)
        with torch.no_grad():
            self.head.weight.copy_(self.head.weight[:, torch.cat([order, order + channels * bands])])

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        b = self.backbone
        x = x[:, :, : x.shape[2] // b.time_stride * b.time_stride, :]
        outs = [b.stem(x)]
        for stage in range(b.num_stages):
            outs.extend(b.run_stage(outs, stage))
        return self.head(self.pool(To1d()(b.head(b.fin_to2d(b.fin_wght1d(outs))))))


def import_run(cfg: dict, paths: dict) -> Path:
    """artifacts/speaker/runs/<run>/ with the survey's extractor as model.pt and its network's code at their pinned
    paths, the config resolved with the extractor's pins; split.lock is empty, nothing is learnt here (KEHOACH 4.4)."""
    src = cfg["source"]
    spec = load_yaml(CONFIGS / src["survey"])["extractors"][src["extractor"]]
    checkpoint, package = spec["args"]
    if package != PACKAGE:
        raise ValueError(f"{src['extractor']}: a {package} extractor; the chip path takes {PACKAGE}")
    folder = refs.speaker_files(src["extractor"], spec, paths["cache"])
    run = run_dir.create_run_dir(ML_ROOT / "artifacts", BRANCH, cfg | {"source": src | {"pins": spec}})
    for path in spec["files"]:
        target = run / (MODEL_FILE if path == checkpoint else path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(folder / path, target)
    load_wrap(run)
    return run


def load_wrap(run: Path) -> nn.Module:
    """The run's ReDimNet2Wrap in eval, built from model.pt's model_config by the code beside it as run.py builds it."""
    sys.path.insert(0, str(run))
    saved = torch.load(run / MODEL_FILE, map_location="cpu")
    wrap = importlib.import_module(f"{PACKAGE}.redimnet2").ReDimNet2Wrap(**saved["model_config"])
    loaded = wrap.load_state_dict(saved["state_dict"])
    if loaded.missing_keys or loaded.unexpected_keys:
        raise ValueError(f"{run / MODEL_FILE}: {loaded}")
    return wrap.eval()


def tail_of(samples: np.ndarray, n_samples: int) -> torch.Tensor:
    """The last n_samples as a (1, n_samples) batch, zeros ahead of a shorter window."""
    tail = samples[-n_samples:]
    return torch.from_numpy(np.pad(tail, (n_samples - len(tail), 0)).astype(np.float32))[None]


def features(wrap: nn.Module, samples: np.ndarray, n_samples: int) -> torch.Tensor:
    """The extractor's own log-mel features of the window's tail, frame after frame: (1, 1, frames, mels)."""
    with torch.no_grad():
        return wrap.spec(tail_of(samples, n_samples)).unsqueeze(1).transpose(2, 3).contiguous()


def check_rewrite(net: Embed, wrap: nn.Module, windows: list[np.ndarray], n_samples: int, rtol: float) -> float:
    """The largest gap of net to the extractor over windows, as a share of the extractor's largest value; refused
    above rtol."""
    worst = 0.0
    with torch.no_grad():
        for samples in windows:
            want = wrap(tail_of(samples, n_samples))
            got = net(features(wrap, samples, n_samples))
            worst = max(worst, float((got - want).abs().max() / want.abs().max()))
    if worst > rtol:
        raise ValueError(f"the rewritten graph is {worst:.2e} off the extractor, above {rtol:.0e}")
    return worst


def figures(embeddings: np.ndarray, windows: list[survey.Window], survey_cfg: dict, quant: dict) -> dict:
    """The survey's owner figures at quant.count enrolment windows, once at each impostor share of quant.shares."""
    owner = survey_cfg["owner"] | {"counts": [quant["count"]]}
    out = {}
    for share in quant["shares"]:
        at = survey_cfg | {"owner": owner, "rule": survey_cfg["rule"] | {"impostors_passing": share}}
        out[share] = survey.owner_figures(embeddings, windows, at, survey_cfg["window_s"])[quant["count"]]
    return out


def table(ladder: dict) -> str:
    """Float and each row: EER and the accepted windows kept, in all and per group, at each impostor share."""
    graphs = {"float": ladder["float"]} | {name: row["figures"] for name, row in ladder["rows"].items()}
    first = next(iter(ladder["float"].values()))
    groups = [k.removeprefix("kept accepted ") for k in first if k.startswith("kept accepted ") and " at " not in k]
    lines = ["| graph | impostors passing | EER | kept accepted | " + " | ".join(groups) + " |"]
    lines.append("|---" * (4 + len(groups)) + "|")
    for name, by_share in graphs.items():
        for share, f in by_share.items():
            cells = [f"{100 * f['eer']:.2f}%", f"{100 * f['kept accepted']:.1f}%"]
            cells += [f"{100 * f[f'kept accepted {g}']:.1f}%" if f"kept accepted {g}" in f else "—" for g in groups]
            lines.append(f"| {name} | {100 * share:g}% | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def recorded(run: Path, head: dict, rows: dict, fresh: bool = False) -> dict:
    """head and rows merged into <run>/int8/ladder.yaml, the rows already there kept unless fresh."""
    out = run / INT8_DIR / LADDER_FILE
    kept = yaml.safe_load(out.read_text(encoding="utf-8")) if out.is_file() and not fresh else {}
    merged = kept | head | {"rows": kept.get("rows", {}) | rows}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(merged, sort_keys=False), encoding="utf-8")
    return merged


def drawn(q: dict, impostors: list) -> tuple[list[int], int]:
    """The impostor windows the ladder calibrates on and the one the board probe runs, drawn with quant.seed."""
    picks = np.random.default_rng(q["seed"]).choice(len(impostors), q["calib_windows"] + 1, replace=False)
    return [int(i) for i in picks[:-1]], int(picks[-1])


def timing(cfg: dict, run: Path, paths: dict, workers: int) -> Path:
    """<run>/int8/timing/graph.native: the run's model quantised under quant.yaml's timing rungs on the ladder's
    calibration windows, quick to make, its time on the chip the ladder's own (KEHOACH 3.14 step 2.1)."""
    survey_cfg, n_samples = load_yaml(CONFIGS / cfg["source"]["survey"]), round(cfg["window_s"] * grid.SAMPLE_RATE_HZ)
    wrap, impostors = load_wrap(run), survey.impostor_windows(survey_cfg, paths, workers)
    picks, board = drawn(cfg["quant"], impostors)
    calib = [features(wrap, impostors[i].samples, n_samples) for i in picks]
    folder = run / INT8_DIR / ptq_espdl.TIMING_ROW
    with esp_ppq_patches.applied(cfg["esp_ppq_patches"]):
        graph = ptq_espdl.quantize(Embed(wrap).eval(), calib, folder, ptq_espdl.timing_rungs(LADDER))
    np.save(run / INT8_DIR / WINDOW_FILE, features(wrap, impostors[board].samples, n_samples).numpy())
    return export_espdl.save_native(graph, folder / GRAPH_FILE)


def same_int8(cfg: dict, run: Path, paths: dict, workers: int, against: str, calibration: str | None) -> tuple:
    """(off, worst) of ptq_espdl.same_integers for Embed as built here and as built at commit against, quantised under
    the ladder's rungs on quant.check_windows of its calibration windows and compared on as many owner windows;
    None when the network code is the commit's (KEHOACH 3.14 step 2.4)."""
    old = ptq_espdl.at_commit(Path(__file__), __name__, against)
    if old is None:
        return None
    q, survey_cfg = cfg["quant"], load_yaml(CONFIGS / cfg["source"]["survey"])
    n_samples, wrap = round(cfg["window_s"] * grid.SAMPLE_RATE_HZ), load_wrap(run)
    owner, impostors = survey.owner_windows(survey_cfg, paths), survey.impostor_windows(survey_cfg, paths, workers)
    picks, _ = drawn(q, impostors)
    calib = [features(wrap, impostors[i].samples, n_samples) for i in picks[: q["check_windows"]]]
    inputs = [features(wrap, w.samples, n_samples).numpy() for w in owner[: q["check_windows"]]]
    rungs = ptq_espdl.ladder(LADDER) | ({"calibration": calibration} if calibration else {})
    nets = [old.Embed(wrap).eval(), Embed(wrap).eval()]
    return ptq_espdl.same_integers(nets, calib, inputs, rungs, cfg["esp_ppq_patches"])


@dataclass
class Bench:
    """What every row of the ladder is measured on: the rewritten net and its gap to the extractor, the calibration
    windows' features, the scored windows (the owner's, the impostors' less the calibration ones), their features
    and float embeddings."""

    net: Embed
    rewrite_gap: float
    survey_cfg: dict
    calib: list[torch.Tensor]
    scored: list
    feats: list[torch.Tensor]
    fl: np.ndarray


def bench(cfg: dict, run: Path, paths: dict, workers: int) -> Bench:
    """The run's Bench under cfg, the branch's config, with the board probe's window saved beside the ladder."""
    q, survey_cfg = cfg["quant"], load_yaml(CONFIGS / cfg["source"]["survey"])
    n_samples = round(cfg["window_s"] * grid.SAMPLE_RATE_HZ)
    wrap = load_wrap(run)
    net = Embed(wrap).eval()
    owner, impostors = survey.owner_windows(survey_cfg, paths), survey.impostor_windows(survey_cfg, paths, workers)
    picks, board = drawn(q, impostors)
    calib = [impostors[i].samples for i in picks]
    scored = owner + [w for i, w in enumerate(impostors) if i not in set(picks)]
    rewrite_gap = check_rewrite(net, wrap, calib, n_samples, q["rewrite_rtol"])
    feats = [features(wrap, w.samples, n_samples) for w in scored]
    with torch.no_grad():
        fl = np.stack([net(f).numpy().ravel() for f in feats])
    (run / INT8_DIR).mkdir(parents=True, exist_ok=True)
    np.save(run / INT8_DIR / WINDOW_FILE, features(wrap, impostors[board].samples, n_samples).numpy())
    calib_feats = [features(wrap, samples, n_samples) for samples in calib]
    return Bench(net, rewrite_gap, survey_cfg, calib_feats, scored, feats, fl)


def quantized(cfg: dict, b: Bench, folder: Path, rungs: dict):
    """b's net quantised under rungs with the branch's patches, kept at folder/graph.native for the probe."""
    with esp_ppq_patches.applied(cfg["esp_ppq_patches"]):
        graph = ptq_espdl.quantize(b.net, b.calib, folder, rungs)
    export_espdl.save_native(graph, folder / GRAPH_FILE)
    return graph


def int8_row(cfg: dict, b: Bench, graph, rungs: dict) -> dict:
    """graph's row: the rungs it was built on, the cosine of its simulated embeddings to float, and its figures."""
    sim = ptq_espdl.Simulator(graph, cfg["esp_ppq_patches"])
    q8 = np.stack([sim(f.numpy()).ravel() for f in b.feats])
    cosine = (survey.unit(b.fl) * survey.unit(q8)).sum(axis=1)
    return {
        "calibration": rungs["calibration"],
        "int16_ops": rungs["int16_ops"],
        "cosine_median": float(np.median(cosine)),
        "cosine_p10": float(np.percentile(cosine, 10)),
        "figures": figures(q8, b.scored, b.survey_cfg, cfg["quant"]),
    }


def worst_kept(at_share: dict) -> float:
    """The accepted windows kept in the worst group of one share's figures."""
    return min(v for k, v in at_share.items() if k.startswith("kept accepted ") and " at " not in k)


def best_calibration(ladder: dict, calibrations: list[str], share: float) -> str:
    """The calibration of rung 2 that keeps the most accepted windows in its worst group at the impostor share of
    KEHOACH 3.17's gate, then the lowest EER there."""
    rows = ladder.get("rows", {})
    missing = [c for c in calibrations if c not in rows]
    if missing:
        raise ValueError(f"the ladder has no row of {missing}: run make speaker-ptq first")
    return min(calibrations, key=lambda c: (-worst_kept(rows[c]["figures"][share]), rows[c]["figures"][share]["eer"]))


def ptq(cfg: dict, run: Path, paths: dict, workers: int, calibrations: list[str] | None = None) -> dict:
    """Rungs 1 and 2 of the run's model under cfg, the branch's config as ctc's ladder reads its own: each of
    calibrations, by default all of quant.calibrations, kept at <run>/int8/<calibration>/graph.native and rowed in the
    ladder beside float, on the owner's windows and the impostors' less the calibration ones; the whole ladder starts
    afresh, named calibrations keep the other rows."""
    q = cfg["quant"]
    unknown = sorted(set(calibrations or []) - set(q["calibrations"]))
    if unknown:
        raise ValueError(f"{unknown} are not among quant.calibrations {q['calibrations']}")
    b = bench(cfg, run, paths, workers)
    head = {"window_s": cfg["window_s"], "windows": len(b.scored), "rewrite_gap": b.rewrite_gap}
    ladder = recorded(run, head | {"float": figures(b.fl, b.scored, b.survey_cfg, q)}, {}, fresh=calibrations is None)
    for calibration in calibrations or q["calibrations"]:
        rungs = ptq_espdl.ladder(LADDER) | {"calibration": calibration}
        graph = quantized(cfg, b, run / INT8_DIR / calibration, rungs)
        ladder = recorded(run, {}, {calibration: int8_row(cfg, b, graph, rungs)})
        print(table(ladder), flush=True)
    return ladder


def int16(cfg: dict, run: Path, paths: dict, workers: int) -> dict:
    """Rung 3 on rung 2's best calibration: ESP-PPQ's per-layer error over quant.layerwise_windows calibration windows
    ranks the graph's operations, and a row for each count of quant.int16_tops puts the worst of them at 16 bits."""
    q = cfg["quant"]
    path = run / INT8_DIR / LADDER_FILE
    best = best_calibration(
        yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}, q["calibrations"], q["gate_share"]
    )
    b = bench(cfg, run, paths, workers)
    rungs = ptq_espdl.ladder(LADDER) | {"calibration": best}
    base = quantized(cfg, b, run / INT8_DIR / "int16_base", rungs)
    head = {"float": figures(b.fl, b.scored, b.survey_cfg, q), "int16_base": best}
    ladder = recorded(run, head, {"int16_top0": int8_row(cfg, b, base, rungs)})
    print(table(ladder), flush=True)
    with esp_ppq_patches.applied(cfg["esp_ppq_patches"]):
        ranked = mixed_espdl.ranked_layers(base, b.calib[: q["layerwise_windows"]])
    layers = [{"op": name, "noise_to_signal": round(error, 6)} for name, error in ranked]
    ladder = recorded(run, {"layerwise": layers}, {})
    for name, ops in mixed_espdl.int16_rows(ranked, q["int16_tops"]).items():
        wide = rungs | {"int16_ops": ops}
        graph = quantized(cfg, b, run / INT8_DIR / name, wide)
        ladder = recorded(run, {}, {name: int8_row(cfg, b, graph, wide)})
        print(table(ladder), flush=True)
    return ladder


def front_end_gaps(wrap: nn.Module) -> list[str]:
    """Where the run's TFMelBanks differs from contracts/speaker.yaml's front end: its numbers, then its tables."""
    normalize, emphasis, spectral = wrap.spec.torchfbank
    cfg = spk_fbank.contract_config()
    found = {
        "normalised": type(normalize).__name__ == "NormalizeAudio" and float(normalize.eps) == cfg.eps,
        "frame_samples": spectral.length,
        "hop_samples": spectral.shift,
        "fft_size": spectral.nfft,
        "n_bands": spectral.num_bins,
        "f_min_hz": float(spectral.low_freq),
        "f_max_hz": float(spectral.high_freq),
        "preemphasis": float(emphasis.coef),
        "eps": float(spectral.eps),
        "log eps": float(wrap.spec.eps),
    }
    want = {"normalised": True, "log eps": cfg.eps} | {k: getattr(cfg, k) for k in found if hasattr(cfg, k)}
    gaps = [f"{k} {found[k]} against {want[k]}" for k in found if found[k] != want[k]]
    front = spk_fbank.SpkFbank(cfg)
    if not np.array_equal(spectral.window, front.window):
        gaps.append("the window")
    if not np.array_equal(spectral.melbanks_pt.numpy()[:, :, 0].T, front.weights):
        gaps.append("the mel weights")
    return gaps


def chip_units(
    graph, patches: list[str], windows: list, front: spk_fbank.SpkFbank, wrap: nn.Module
) -> tuple[list[np.ndarray], dict]:
    """Each window's unit embedding as the chip computes it: the tail's features by the mirror of spk_fbank, put on the
    graph's int8 input grid, the ESP-PPQ simulation, the mirror of the scoring's unit length. Beside them, how far the
    mirror's features sit from the extractor's own and how many int8 inputs that moves (KEHOACH 3.17)."""
    io, sim = ptq_espdl.io_of(graph), ptq_espdl.Simulator(graph, patches)
    n = front.cfg.window_samples
    out, worst, gaps, moved, inputs = [], 0.0, [], 0, 0
    for w in windows:
        pcm = spk_fbank.tail(np.rint(w.samples * PCM_FULL_SCALE).astype(np.int16), n)
        mirror = front.run(pcm)[None, None]
        extractor = features(wrap, pcm.astype(np.float32) / np.float32(PCM_FULL_SCALE), n).numpy()
        gap = np.abs(mirror - extractor)
        worst, gaps = max(worst, float(gap.max())), [*gaps, float(gap.mean())]
        x = ptq_espdl.to_int8(mirror, io.input_exponent)
        moved += int(np.count_nonzero(x != ptq_espdl.to_int8(extractor, io.input_exponent)))
        inputs += x.size
        y = sim(x.astype(np.float32) * np.float32(2.0**io.input_exponent))
        out.append(score.unit(ptq_espdl.to_int8(y, io.output_exponent).ravel(), io.output_exponent))
    gap = {"max_abs": worst, "mean_abs": float(np.mean(gaps)), "int8_inputs_moved": moved, "int8_inputs": inputs}
    return out, gap


def rule_rows(
    targets: list[tuple[int, str]], impostors: list[int], dq: dict, members: list[int] | None = None
) -> tuple[dict, float]:
    """At each share of deploy.shares, the lowest score in thousandths of the AS-norm score that lets at most that share
    of the impostor scores through, with the share of target scores, each at a distance, it keeps per distance, and of
    the members' scores when there are members; and the first share whose every distance keeps deploy.keep_min, else
    the last (KEHOACH 3.17)."""
    ranked = sorted(impostors, reverse=True)
    rows = {}
    for share in dq["shares"]:
        allowed = math.floor(share * len(ranked))
        milli_z = min(ranked[allowed] + 1, score.Z_LIMIT) if allowed < len(ranked) else -score.Z_LIMIT
        kept = {
            f"{d} cm": float(np.mean([s >= milli_z for s, at in targets if at == d]))
            for d in sorted({at for _, at in targets})
        }
        passing = float(np.mean([s >= milli_z for s in impostors]))
        rows[share] = {"milli_z": int(milli_z), "impostors passing": passing, "kept accepted": kept}
        if members:
            rows[share]["members kept"] = float(np.mean([s >= milli_z for s in members]))
    fits = [s for s in dq["shares"] if min(rows[s]["kept accepted"].values()) >= dq["keep_min"]]
    return rows, fits[0] if fits else dq["shares"][-1]


def cohort_speakers(windows: list, survey_cfg: dict) -> set[str]:
    """Half the survey's strangers, drawn by its seed on a stream of their own: every window of theirs is the cohort,
    and none of them takes part in the threshold's draws (ADR-0022)."""
    strangers = sorted({w.spk for w in windows if w.spk != survey_cfg["owner"]["spk"]})
    rng = np.random.default_rng([survey_cfg["seed"], COHORT_STREAM])
    return set(rng.choice(strangers, len(strangers) // 2, replace=False).tolist())


def cohort_rows(units: list[np.ndarray], windows: list, cohort: set[str]) -> np.ndarray:
    """The unit embeddings of the cohort's windows, in window order, float32 rows."""
    rows = [units[i] for i, w in enumerate(windows) if w.spk in cohort]
    if not rows:
        raise ValueError("the cohort holds no window")
    return np.stack(rows).astype(np.float32)


def deployed_cohort(lock_row: dict, dims: int) -> np.ndarray:
    """The cohort the lock's speaker row deployed, float32 rows of dims, as the image's speaker_cohort carries it."""
    found = [f for f in lock_row["files"] if f["kind"] == "cohort"]
    if len(found) != 1:
        raise ValueError(f"the speaker row of the lock deploys {len(found)} cohorts: make speaker-deploy again")
    data = (update_lock.MODELS / BRANCH / found[0]["file"]).read_bytes()
    return np.frombuffer(data, dtype="<f4").reshape(-1, dims).astype(np.float32)


def deployed_top(lock_row: dict) -> int:
    """The cohort rows AS-norm averages over at each side, as the lock's speaker row deployed them (ADR-0023)."""
    top = lock_row.get("cohort", {}).get("top")
    if not isinstance(top, int) or top < 1:
        raise ValueError("the speaker row of the lock holds no cohort top: make speaker-deploy again")
    return top


def window_energies(w) -> tuple[int, int, int, int]:
    """A window's sums of squares over its lead and its utterance, as svc_listen takes them (ADR-0024)."""
    return score.noise_energies(np.rint(w.samples * PCM_FULL_SCALE).astype(np.int16), w.lead_hops)


def noise_edges(windows: list, bins: int) -> list[np.float32]:
    """Edges that split the windows' utterance-over-lead power ratios the board can judge into bins of equal counts,
    float32 as the image holds them; 0 for each when it can judge none (ADR-0024)."""
    ratios = []
    for w in windows:
        lead_energy, lead_samples, speech_energy, speech_samples = window_energies(w)
        if lead_samples and lead_energy and speech_samples:
            ratios.append((float(speech_energy) * float(lead_samples)) / (float(lead_energy) * float(speech_samples)))
    if not ratios:
        return [np.float32(0.0)] * (bins - 1)
    return [np.float32(q) for q in np.quantile(np.array(ratios), np.arange(1, bins) / bins)]


def threshold_table(
    units: list[np.ndarray],
    windows: list,
    survey_cfg: dict,
    dq: dict,
    most: int,
    cohort: set[str],
    noisy: dict[str, tuple[list[np.ndarray], list]] | None = None,
) -> dict:
    """The chip's threshold for 1 to most people holding a voiceprint, scored by AS-norm over the deploy.cohort_top rows
    of the cohort's speakers, who take no other part (ADR-0023). Each enrolment draw serves every count; with n people,
    n - 1 strangers with enough windows enrol from their own. The owner's accepted windows, the members' other windows
    and every other stranger's are scored with the best over the n voiceprints, all draws pooled, under rule_rows.
    Given noisy conditions, (units, windows) by name, noise_bins sets each count's thresholds per bin (ADR-0024)."""
    owner = survey_cfg["owner"]
    enrol_group = f"{owner['enrol']['date']} {owner['enrol']['distance_cm']}"
    mine = [i for i, w in enumerate(windows) if w.spk == owner["spk"]]
    pool = [i for i in mine if windows[i].group == enrol_group]
    rows_of_cohort = cohort_rows(units, windows, cohort)
    top = dq["cohort_top"]
    unit_norms: dict[tuple[str, int], tuple[np.float32, np.float32]] = {}
    conditions = {"quiet": (units, windows)} | (noisy or {})

    def scored(i: int, prints: list[np.ndarray], norms: list, name: str = "quiet") -> tuple[int, int, int]:
        u = conditions[name][0][i]
        if (name, i) not in unit_norms:
            unit_norms[(name, i)] = score.norm(u, rows_of_cohort, top)
        return score.best(u, prints, norms, unit_norms[(name, i)])

    by_spk: dict[str, list[int]] = {}
    for i, w in enumerate(windows):
        if w.spk != owner["spk"] and w.spk not in cohort:
            by_spk.setdefault(w.spk, []).append(i)
    enrollable = sorted(s for s, rows in by_spk.items() if len(rows) >= speaker.ENROL_COMMANDS)
    rng = np.random.default_rng([survey_cfg["seed"], survey.ENROL_STREAM, speaker.ENROL_COMMANDS])
    draws = [sorted(rng.choice(pool, speaker.ENROL_COMMANDS, replace=False).tolist()) for _ in range(owner["draws"])]
    strangers = {
        name: [i for i, w in enumerate(ws) if w.spk != owner["spk"] and w.spk not in cohort]
        for name, (_, ws) in conditions.items()
    }
    edges = noise_edges(
        [ws[i] for name, (_, ws) in conditions.items() for i in strangers[name]], pack_models.NOISE_EDGES + 1
    )
    bin_of = {name: [score.noise_bin(*window_energies(w), edges) for w in ws] for name, (_, ws) in conditions.items()}
    counts = []
    for n in range(1, most + 1):
        members_rng = np.random.default_rng([survey_cfg["seed"], survey.ENROL_STREAM, speaker.ENROL_COMMANDS, n])
        targets: list[tuple[int, str]] = []
        impostors: list[int] = []
        theirs_scores: list[int] = []
        named: dict[str, list[bool]] = {"owner": [], "members": []}
        heard: dict[str, list[tuple[int, str, int]]] = {name: [] for name in conditions}
        passing: dict[str, list[tuple[int, int]]] = {name: [] for name in conditions}
        for enrolled in draws:
            members = members_rng.choice(enrollable, n - 1, replace=False).tolist() if n > 1 else []
            prints = [score.voiceprint([units[i] for i in enrolled])]
            member_tests: list[tuple[int, int]] = []
            for k, m in enumerate(members, start=1):
                theirs = sorted(members_rng.choice(by_spk[m], speaker.ENROL_COMMANDS, replace=False).tolist())
                prints.append(score.voiceprint([units[i] for i in theirs]))
                member_tests += [(i, k) for i in by_spk[m] if i not in set(theirs)]
            norms = [score.norm(p, rows_of_cohort, top) for p in prints]
            for i in (i for i in mine if i not in set(enrolled) and windows[i].accepted):
                s, index, _ = scored(i, prints, norms)
                targets.append((s, windows[i].group.split()[-1]))
                named["owner"].append(index == 0)
            for i, k in member_tests:
                s, index, _ = scored(i, prints, norms)
                theirs_scores.append(s)
                named["members"].append(index == k)
            impostors += [scored(i, prints, norms)[0] for s, rows in by_spk.items() if s not in members for i in rows]
            if noisy is None:
                continue
            said = {k for i in enrolled for k in windows[i].said}
            for name, (_, ws) in conditions.items():
                for i, w in enumerate(ws):
                    if w.spk == owner["spk"] and w.accepted and not said & set(w.said):
                        heard[name].append((scored(i, prints, norms, name)[0], w.group.split()[-1], bin_of[name][i]))
                passing[name] += [
                    (scored(i, prints, norms, name)[0], bin_of[name][i])
                    for i in strangers[name]
                    if ws[i].spk not in members
                ]
        rows, chosen = rule_rows(targets, impostors, dq, theirs_scores)
        right = {who: float(np.mean(found)) for who, found in named.items() if found}
        counts.append({"people": n, "shares": rows, "chosen": chosen, "named right": right})
        if noisy is not None:
            counts[-1]["noise"] = noise_bins(heard, passing, rows[chosen]["milli_z"], dq["shares"][0], len(edges))
    table = {
        "enrol_commands": speaker.ENROL_COMMANDS,
        "draws": owner["draws"],
        "owner windows": len(mine),
        "impostor windows": sum(len(rows) for rows in by_spk.values()),
        "impostors enrollable": len(enrollable),
        "cohort": {"speakers": len(cohort), "rows": len(rows_of_cohort), "top": top},
        "counts": counts,
    }
    if noisy is not None:
        n_bins = len(edges) + 1
        windows_of = {name: len(ws) for name, (_, ws) in conditions.items()}
        heard_of = {
            name: [i for i, w in enumerate(ws) if w.spk == owner["spk"] and w.accepted]
            for name, (_, ws) in conditions.items()
        }
        owner_bins = {name: bin_shares([bin_of[name][i] for i in heard_of[name]], n_bins) for name in conditions}
        stranger_bins = {name: bin_shares([bin_of[name][i] for i in strangers[name]], n_bins) for name in conditions}
        table["noise"] = {
            "edges": [float(e) for e in edges],
            "windows": windows_of,
            "owner by bin": owner_bins,
            "strangers by bin": stranger_bins,
        }
    return table


def bin_shares(bins: list[int], n_bins: int) -> list[float]:
    """The share of bins falling in each of n_bins noise bins, the noisiest first."""
    return (np.bincount(np.asarray(bins, dtype=np.int64), minlength=n_bins) / max(1, len(bins))).tolist()


def under_thresholds(
    heard: dict[str, list[tuple[int, str, int]]], passing: dict[str, list[tuple[int, int]]], thresholds: list[int]
) -> dict:
    """The owner's accepted windows kept per distance and the strangers passing, per condition, each window under its
    noise bin's threshold."""
    kept = {
        name: {
            f"{d} cm": float(np.mean([s >= thresholds[b] for s, at, b in found if at == d]))
            for d in sorted({at for _, at, _ in found})
        }
        for name, found in heard.items()
    }
    passed = {name: float(np.mean([s >= thresholds[b] for s, b in found])) for name, found in passing.items() if found}
    return {"kept accepted": kept, "strangers passing": passed}


def noise_bins(
    heard: dict[str, list[tuple[int, str, int]]],
    passing: dict[str, list[tuple[int, int]]],
    quiet_milli_z: int,
    share: float,
    n_edges: int,
) -> dict:
    """One count's thresholds per noise bin (ADR-0024) from the strangers' (score, bin) of every condition: in each
    noisier bin the one letting share of that bin's strangers through, the quiet table's where a bin has none; the
    quietest keeps quiet_milli_z unless that lets more than share of its strangers through. With them, and with
    quiet_milli_z alone in every bin as ADR-0023 has it, the figures of under_thresholds."""
    pooled: dict[int, list[int]] = defaultdict(list)
    for found in passing.values():
        for s, b in found:
            pooled[b].append(s)
    quiet = max(quiet_milli_z, at_share(pooled[n_edges], share)) if pooled[n_edges] else quiet_milli_z
    noisier = [at_share(pooled[b], share) if pooled[b] else quiet for b in range(n_edges)]
    by_bin = under_thresholds(heard, passing, [*noisier, quiet])
    one = under_thresholds(heard, passing, [quiet_milli_z] * (n_edges + 1))
    return {"milli_z": noisier, "quiet": quiet} | by_bin | {"one threshold": one}


def deploy(cfg: dict, run: Path, row: str, paths: dict, workers: int) -> tuple[Path, Path]:
    """The graph of row as firmware/models/speaker/ holds it: the .espdl with the board probe's window for
    model->test(), the cohort of strangers as cohort.bin, the threshold set on the survey's material through the chip's
    own path, and the lock (KEHOACH 3.17, ADR-0022).
    <run>/int8/<row>/deploy.yaml keeps the threshold's figures, the front end's gap to the extractor and the survey's
    figures on that path, beside the ladder's. Refused for a run whose window or front end is not
    contracts/speaker.yaml's, and for a row not int8 at both ends."""
    if cfg["window_s"] != speaker.WINDOW_S:
        raise ValueError(f"window_s {cfg['window_s']} s; contracts/speaker.yaml cuts {speaker.WINDOW_S} s")
    wrap = load_wrap(run)
    if gaps := front_end_gaps(wrap):
        raise ValueError(f"{run.name}: its front end is not contracts/speaker.yaml's: {'; '.join(gaps)}")
    folder = run / INT8_DIR / row
    graph = export_espdl.load_native(folder / GRAPH_FILE)
    if (bits := ptq_espdl.io_bits(graph)) != (CHIP_BITS, CHIP_BITS):
        raise ValueError(f"row {row} reads {bits[0]} and gives {bits[1]} bits; the chip runs int8 at both ends")
    survey_cfg, dq = load_yaml(CONFIGS / cfg["source"]["survey"]), cfg["deploy"]
    vads: dict = {}
    owner = survey.owner_windows(survey_cfg, paths, vads=vads)
    impostors = survey.impostor_windows(survey_cfg, paths, workers)
    picks, board = drawn(cfg["quant"], impostors)
    scored = owner + [w for i, w in enumerate(impostors) if i not in set(picks)]
    front = spk_fbank.SpkFbank(spk_fbank.contract_config())
    units, gap = chip_units(graph, cfg["esp_ppq_patches"], scored, front, wrap)
    cohort = cohort_speakers(scored, survey_cfg)
    pools = device_of(survey.board_cfg(survey_cfg))["noise"]["pools"]
    noisy = {}
    for snr in dq["noise"]["snr_db"]:
        pcm_of = survey.NoisyPcm(survey_cfg, vads, snr, pools, paths, survey_cfg["seed"])
        ws = survey.owner_windows(survey_cfg, paths, pcm_of=pcm_of)
        ws += survey.impostor_windows(survey_cfg, paths, workers, (pools, snr))
        noisy[f"{snr:g} dB"] = (chip_units(graph, cfg["esp_ppq_patches"], ws, front, wrap)[0], ws)
    table = threshold_table(units, scored, survey_cfg, dq, pack_models.SPEAKERS, cohort, noisy)
    survey_figures = figures(np.stack(units), scored, survey_cfg, cfg["quant"])
    kept = table | {"front_end": gap, "figures": survey_figures}
    (folder / DEPLOY_FILE).write_text(yaml.safe_dump(kept, sort_keys=False), encoding="utf-8")
    io = ptq_espdl.io_of(graph)
    pcm = spk_fbank.tail(np.rint(impostors[board].samples * PCM_FULL_SCALE).astype(np.int16), front.cfg.window_samples)
    x = ptq_espdl.to_int8(front.run(pcm)[None, None], io.input_exponent)
    with esp_ppq_patches.applied(cfg["esp_ppq_patches"]):
        built = export_espdl.export(
            graph,
            folder / "deploy" / f"{BRANCH}.espdl",
            test_input=x.astype(np.float32) * np.float32(2.0**io.input_exponent),
        )
    target = update_lock.MODELS / BRANCH
    target.mkdir(parents=True, exist_ok=True)
    espdl = target / f"{BRANCH}.espdl"
    espdl.write_bytes(built.read_bytes())
    cohort_file = target / COHORT_FILE
    cohort_file.write_bytes(cohort_rows(units, scored, cohort).astype("<f4").tobytes())
    rungs = yaml.safe_load((run / INT8_DIR / LADDER_FILE).read_text(encoding="utf-8"))["rows"][row]
    fields = {
        "speaker_hash": f"0x{speaker.HASH:08x}",
        "features": {"name": f"tf_mel{speaker.N_BANDS}", "frames": speaker.N_FRAMES, "bands": speaker.N_BANDS},
        "row": row,
        "rungs": {"calibration": rungs["calibration"], "int16_ops": rungs.get("int16_ops", [])},
        "threshold": {
            "milli_z": [c["noise"]["quiet"] for c in table["counts"]],
            "impostors_passing": [c["chosen"] for c in table["counts"]],
            "noise": {"edges": table["noise"]["edges"], "milli_z": [c["noise"]["milli_z"] for c in table["counts"]]},
        },
        "cohort": {"seed": survey_cfg["seed"], "stream": COHORT_STREAM} | table["cohort"],
        "weights_in_flash": dq["weights_in_flash"],
    }
    files = [update_lock.Deployed(espdl, BRANCH, "espdl"), update_lock.Deployed(cohort_file, COHORT_ENTRY, "cohort")]
    return update_lock.record(BRANCH, run, files, fields)


def music_prints(
    units: dict[str, list[np.ndarray]],
    windows: dict[str, list],
    survey_cfg: dict,
    cohort_rows_: np.ndarray,
    top: int,
    swap: tuple[str, int],
) -> dict[str, list[tuple]]:
    """For each of threshold_table's enrolment draws of the owner's quiet windows, the voiceprint, its AS-norm mean and
    deviation and the utterances it heard: as drawn ("quiet"), and with swap[1] of its windows swapped for windows of
    condition swap[0] from the same enrolment group, none hearing an utterance the kept ones heard ("mixed")."""
    owner = survey_cfg["owner"]
    enrol_group = f"{owner['enrol']['date']} {owner['enrol']['distance_cm']}"
    quiet = windows["quiet"]
    pool = [i for i, w in enumerate(quiet) if w.spk == owner["spk"] and w.group == enrol_group]
    rng = np.random.default_rng([survey_cfg["seed"], survey.ENROL_STREAM, speaker.ENROL_COMMANDS])
    draws = [sorted(rng.choice(pool, speaker.ENROL_COMMANDS, replace=False).tolist()) for _ in range(owner["draws"])]
    name, count = swap
    other = windows[name]
    swap_pool = [i for i, w in enumerate(other) if w.spk == owner["spk"] and w.group == enrol_group]
    swap_rng = np.random.default_rng([survey_cfg["seed"], survey.NOISE_STREAM])
    prints: dict[str, list[tuple]] = {"quiet": [], "mixed": []}
    for enrolled in draws:
        heard = {k for i in enrolled for k in quiet[i].said}
        made = score.voiceprint([units["quiet"][i] for i in enrolled])
        prints["quiet"].append((made, score.norm(made, cohort_rows_, top), heard))
        kept = enrolled[: speaker.ENROL_COMMANDS - count]
        heard = {k for i in kept for k in quiet[i].said}
        free = [j for j in swap_pool if not heard & set(other[j].said)]
        swapped = sorted(swap_rng.choice(free, count, replace=False).tolist())
        made = score.voiceprint([units["quiet"][i] for i in kept] + [units[name][j] for j in swapped])
        heard |= {k for j in swapped for k in other[j].said}
        prints["mixed"].append((made, score.norm(made, cohort_rows_, top), heard))
    return prints


def music_scores(
    units: dict[str, list[np.ndarray]],
    windows: dict[str, list],
    survey_cfg: dict,
    cohort: set[str],
    prints: dict[str, list[tuple]],
    cohort_rows_: np.ndarray,
    top: int,
) -> dict:
    """Per condition and kind of prints, every AS-norm score in thousandths beside survey.snr_db of its window: the
    owner's accepted windows with their distance, each print against those sharing no utterance with it ("targets"),
    and every print against the windows of the strangers outside the cohort ("strangers") (ADR-0023)."""
    spk = survey_cfg["owner"]["spk"]
    out = {}
    for name, ws in windows.items():
        u = units[name]
        mine = [i for i, w in enumerate(ws) if w.spk == spk and w.accepted]
        strangers = [i for i, w in enumerate(ws) if w.spk != spk and w.spk not in cohort]
        unit_norms = {i: score.norm(u[i], cohort_rows_, top) for i in mine + strangers}
        snr = {i: survey.snr_db(ws[i]) for i in mine + strangers}
        row: dict = {"owner windows": sum(w.spk == spk for w in ws), "accepted": len(mine)}
        row["stranger windows"] = len(strangers)
        for kind, made in prints.items():
            targets: list[tuple[int, str, float]] = []
            passing: list[tuple[int, float]] = []
            for p, norm, heard in made:
                for i in mine:
                    if not heard & set(ws[i].said):
                        s = score.best(u[i], [p], [norm], unit_norms[i])[0]
                        targets.append((s, ws[i].group.split()[-1], snr[i]))
                passing += [(score.best(u[i], [p], [norm], unit_norms[i])[0], snr[i]) for i in strangers]
            row[kind] = {"targets": targets, "strangers": passing}
        out[name] = row
    return out


def at_share(scores: list[int], share: float) -> int:
    """The lowest score in thousandths that lets at most share of scores through, as rule_rows sets it."""
    ranked = sorted(scores, reverse=True)
    allowed = math.floor(share * len(ranked))
    return min(ranked[allowed] + 1, score.Z_LIMIT) if allowed < len(ranked) else -score.Z_LIMIT


def snr_edges(snrs: list[float], bins: int) -> list[float]:
    """Edges in dB that split the SNRs a board can tell into bins of equal counts; none when it can tell none."""
    told = np.array([s for s in snrs if not math.isnan(s)])
    return [round(float(q), 2) for q in np.quantile(told, np.arange(1, bins) / bins)] if len(told) else []


def snr_bin(snr: float, edges_db: list[float]) -> int:
    """The bin of edges_db an SNR falls in; one the board cannot tell counts as the quietest."""
    return len(edges_db) if math.isnan(snr) else int(np.searchsorted(edges_db, snr, side="right"))


def judged(targets: list[tuple], strangers: list[tuple], threshold_of: Callable[[float], int]) -> dict:
    """The targets kept per distance and the strangers passing when each window's threshold is threshold_of its SNR."""
    kept = {
        f"{d} cm": float(np.mean([s >= threshold_of(snr) for s, at, snr in targets if at == d]))
        for d in sorted({at for _, at, _ in targets})
    }
    return {
        "kept accepted": kept,
        "strangers passing": float(np.mean([s >= threshold_of(snr) for s, snr in strangers])),
    }


def constant(milli_z: int) -> Callable[[float], int]:
    return lambda _snr: milli_z


def by_bin(thresholds: dict[int, int], edges_db: list[float], otherwise: int) -> Callable[[float], int]:
    return lambda snr: thresholds.get(snr_bin(snr, edges_db), otherwise)


def music_figures(scores: dict, fixed: int, share: float, bins: int) -> dict:
    """Per kind of prints, then per condition: the owner's accepted windows kept per distance and the strangers passing
    under three thresholds. "fixed" is fixed. "condition" lets share of that condition's strangers through, which only a
    board told the music's level could set. "by snr" splits the strangers' SNRs of every condition into bins of equal
    counts and lets share of each bin through, which a board can set from the window alone."""
    out: dict = {}
    for kind in ("quiet", "mixed"):
        pooled = [st for row in scores.values() for st in row[kind]["strangers"]]
        edges = snr_edges([snr for _, snr in pooled], bins)
        binned: dict[int, list[int]] = defaultdict(list)
        for s, snr in pooled:
            binned[snr_bin(snr, edges)].append(s)
        thresholds = {b: at_share(v, share) for b, v in binned.items()}
        out[kind] = {"by snr": {"edges_db": edges, "milli_z": [thresholds.get(b) for b in range(bins)]}}
        for name, row in scores.items():
            targets, strangers = row[kind]["targets"], row[kind]["strangers"]
            own = at_share([s for s, _ in strangers], share)
            out[kind][name] = {k: row[k] for k in ("owner windows", "accepted", "stranger windows")} | {
                "fixed": judged(targets, strangers, constant(fixed)),
                "condition": judged(targets, strangers, constant(own)) | {"milli_z": own},
                "by snr": judged(targets, strangers, by_bin(thresholds, edges, fixed)),
                "owner median milli_z": int(np.median([s for s, _, _ in targets])),
                "owner median snr db": median_told([snr for _, _, snr in targets]),
            }
    return out


def median_told(snrs: list[float]) -> float | None:
    told = [s for s in snrs if not math.isnan(s)]
    return round(float(np.median(told)), 2) if told else None


def music_table(body: dict) -> str:
    """music.yaml's figures as markdown tables, percentages: per cut, the voiceprint quiet / mixed in each cell."""
    lines = []
    for cut, figures in body["cuts"].items():
        lines += [
            "",
            f"{cut}: fixed threshold {figures['milli_z']}; cells voiceprint quiet / mixed",
            "",
            "| condition | threshold | accepted | kept 1 m | kept 3 m | strangers passing | median | snr |",
            "|---|---|---|---|---|---|---|---|",
        ]
        quiet, mixed = figures["quiet"], figures["mixed"]
        for name in (n for n in quiet if n != "by snr"):
            for policy in ("fixed", "condition", "by snr"):
                q, m = quiet[name][policy], mixed[name][policy]
                kept = [
                    " / ".join(f"{100 * x['kept accepted'].get(d, math.nan):.1f}%" for x in (q, m))
                    for d in ("100 cm", "300 cm")
                ]
                lines.append(
                    f"| {name} | {policy} | {quiet[name]['accepted']}/{quiet[name]['owner windows']} | "
                    f"{' | '.join(kept)} | "
                    f"{100 * q['strangers passing']:.2f}% / {100 * m['strangers passing']:.2f}% | "
                    f"{quiet[name]['owner median milli_z']} | {quiet[name]['owner median snr db']} |"
                )
        lines.append(f"\nby snr, quiet prints: {quiet['by snr']}")
    return "\n".join(lines)


def music(cfg: dict, run: Path, paths: dict, workers: int) -> dict:
    """The locked row with music in the room (E11-T33): the survey's material quiet, then at each SNR of
    music.snr_db the owner's sessions with music from a room of the bank and the strangers' with a file of the same
    pool. Two cuts go through the chip's path: the window b0 hears now ("full") and the same from its utterance's
    first vad hop ("speech"), on a cohort and a threshold of its own set alike. Figures of music_figures go to
    <run>/int8/<row>/music.yaml. Refused for a run the lock does not deploy."""
    lock = json.loads(pack_models.LOCK.read_text(encoding="utf-8"))["models"][BRANCH]
    if lock["run"] != run.name:
        raise ValueError(f"the lock deploys {lock['run']}, not {run.name}")
    folder = run / INT8_DIR / lock["row"]
    graph = export_espdl.load_native(folder / GRAPH_FILE)
    wrap, front = load_wrap(run), spk_fbank.SpkFbank(spk_fbank.contract_config())
    survey_cfg, m = load_yaml(CONFIGS / cfg["source"]["survey"]), cfg["music"]
    vads: dict = {}
    owner = survey.owner_windows(survey_cfg, paths, vads=vads)
    full = {"quiet": owner + survey.impostor_windows(survey_cfg, paths, workers)}
    for snr in m["snr_db"]:
        pcm_of = survey.NoisyPcm(survey_cfg, vads, snr, [m["pool"]], paths, cfg["quant"]["seed"])
        heard = survey.owner_windows(survey_cfg, paths, pcm_of=pcm_of)
        full[f"{snr:g} dB"] = heard + survey.impostor_windows(survey_cfg, paths, workers, ([m["pool"]], snr))
    cuts = {"full": full, "speech": {name: [survey.speech_of(w) for w in ws] for name, ws in full.items()}}
    cohort = cohort_speakers(full["quiet"], survey_cfg)
    top, share = deployed_top(lock), cfg["deploy"]["shares"][0]
    swap = (f"{m['mixed_snr_db']:g} dB", m["mixed_windows"])
    body: dict = {"run": run.name, "row": lock["row"], "music": m, "cuts": {}}
    for cut, windows in cuts.items():
        units = {}
        for name, ws in windows.items():
            units[name] = chip_units(graph, cfg["esp_ppq_patches"], ws, front, wrap)[0]
            print(f"{cut}, {name}: {len(ws)} windows through the chip's path", flush=True)
        if cut == "full":
            rows_ = deployed_cohort(lock, len(units["quiet"][0]))
        else:
            rows_ = cohort_rows(units["quiet"], windows["quiet"], cohort)
        prints = music_prints(units, windows, survey_cfg, rows_, top, swap)
        scores = music_scores(units, windows, survey_cfg, cohort, prints, rows_, top)
        own = at_share([s for s, _ in scores["quiet"]["quiet"]["strangers"]], share)
        fixed = int(lock["threshold"]["milli_z"][0]) if cut == "full" else own
        body["cuts"][cut] = {"milli_z": fixed, "quiet milli_z": own} | music_figures(
            scores, fixed, share, m["snr_bins"]
        )
    (folder / MUSIC_FILE).write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("import", help="the survey's pretrained b0 as a run under artifacts/speaker/runs")
    ladder = sub.add_parser("ptq", help="rungs 1 and 2: each calibration beside float into <run>/int8/")
    ladder.add_argument("run", type=Path, help="a run directory of python -m srpipe.tasks.speaker.quant import")
    ladder.add_argument("--workers", type=int, default=8, help="processes simulating the impostors' sessions")
    ladder.add_argument("--calibrations", nargs="+", help="only these of quant.calibrations: a stopped ladder goes on")
    wide = sub.add_parser("int16", help="rung 3: rows with the worst operations at 16 bits, on rung 2's best")
    wide.add_argument("run", type=Path)
    wide.add_argument("--workers", type=int, default=8)
    quick = sub.add_parser("timing", help="the timing row: a quick graph that times the chip as the ladder's rows")
    quick.add_argument("run", type=Path)
    quick.add_argument("--workers", type=int, default=8)
    check = sub.add_parser("same-int8", help="whether a rewrite of the network keeps every integer under the rungs")
    check.add_argument("run", type=Path)
    check.add_argument("--against", default="HEAD", help="the commit whose network code the edited one is held to")
    check.add_argument("--calibration", help="one of quant.calibrations; the ladder's default otherwise")
    check.add_argument("--workers", type=int, default=8)
    lock = sub.add_parser("deploy", help="a row with its threshold into firmware/models/speaker/ and the lock")
    lock.add_argument("run", type=Path)
    lock.add_argument("--row", required=True, help="a row of <run>/int8/ladder.yaml")
    lock.add_argument("--workers", type=int, default=8)
    noisy = sub.add_parser("music", help="the locked row with music in the room at each SNR of music.snr_db")
    noisy.add_argument("run", type=Path)
    noisy.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    cfg = load_yaml(CONFIG)
    if args.command == "import":
        print(import_run(cfg, data_paths()))
    elif args.command == "music":
        body = music(cfg, args.run, data_paths(), args.workers)
        print(music_table(body))
        print(args.run / INT8_DIR / body["row"] / MUSIC_FILE)
    elif args.command == "deploy":
        for path in deploy(cfg, args.run, args.row, data_paths(), args.workers):
            print(path)
    elif args.command == "int16":
        int16(cfg, args.run, data_paths(), args.workers)
        print(args.run / INT8_DIR / LADDER_FILE)
    elif args.command == "timing":
        print(timing(cfg, args.run, data_paths(), args.workers))
    elif args.command == "same-int8":
        found = same_int8(cfg, args.run, data_paths(), args.workers, args.against, args.calibration)
        return ptq_espdl.same_int8_verdict(found, args.against)
    else:
        ptq(cfg, args.run, data_paths(), args.workers, args.calibrations)
        print(args.run / INT8_DIR / LADDER_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
