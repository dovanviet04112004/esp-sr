"""ReDimNet2 b0, the chip path of KEHOACH 3.17, along command_ctc's path: import writes the survey's pinned b0 as a run,
model.pt with its network's code; ptq rewrites it for esp-dl without changing what it computes, from its log-mel
features to the embedding, quantises it with each calibration of rung 2 (3.14) into <run>/int8/<calibration>/, and
rows each beside float in <run>/int8/ladder.yaml on the survey's material, keeping the board probe's window; int16 adds
rung 3's rows on rung 2's best. Run: python -m srpipe.tasks.speaker.quant import | ptq|int16|timing|same-int8 <run>"""

from __future__ import annotations

import argparse
import copy
import importlib
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import yaml
from torch import nn
from torch.nn import functional

from srpipe.compress.quant import esp_ppq_patches, export_espdl, mixed_espdl, ptq_espdl
from srpipe.core import run_dir
from srpipe.core.config import CONFIGS, ML_ROOT, data_paths, load_yaml
from srpipe.generated import grid
from srpipe.scenes import refs
from srpipe.scenes import speaker as survey

CONFIG = CONFIGS / "models" / "speaker.yaml"
BRANCH = LADDER = "speaker"
PACKAGE = "redimnet2"
MODEL_FILE = "model.pt"
INT8_DIR = "int8"
GRAPH_FILE = "graph.native"
LADDER_FILE = "ladder.yaml"
WINDOW_FILE = "board_window.npy"  # float log-mel (1, 1, mels, frames) the probe runs
ASTP_FLOOR = 1e-7  # poolings.py ASTP's variance floor, at the pin


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


class Copies(nn.Module):
    """A one-channel map repeated into count channels."""

    def __init__(self, count: int) -> None:
        super().__init__()
        self.count = count

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return torch.cat([x] * self.count, dim=1)


def per_channel(conv: nn.Conv2d) -> nn.Sequential:
    """conv, one input channel to many, as that channel copied to each output and a depthwise convolution of conv's
    kernels: the same sums, where esp-dl runs a one-channel input at a tenth of a depthwise one's rate."""
    depthwise = nn.Conv2d(
        conv.out_channels,
        conv.out_channels,
        conv.kernel_size,
        stride=conv.stride,
        padding=conv.padding,
        dilation=conv.dilation,
        groups=conv.out_channels,
        bias=conv.bias is not None,
    )
    with torch.no_grad():
        depthwise.weight.copy_(conv.weight)
        if conv.bias is not None:
            depthwise.bias.copy_(conv.bias)
    return nn.Sequential(Copies(conv.out_channels), depthwise)


def one_channel_inputs(model: nn.Module) -> nn.Module:
    """model with each Conv2d of one input channel and several outputs as per_channel."""
    for parent in list(model.modules()):
        for name, m in list(parent.named_children()):
            if isinstance(m, nn.Conv2d) and m.in_channels == 1 and m.out_channels > 1 and m.groups == 1:
                setattr(parent, name, per_channel(m))
    return model


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
        self.backbone = time_major(one_channel_inputs(unit_upsamples(keys_first_attention(rewritten))))
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
    args = parser.parse_args(argv)
    cfg = load_yaml(CONFIG)
    if args.command == "import":
        print(import_run(cfg, data_paths()))
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
