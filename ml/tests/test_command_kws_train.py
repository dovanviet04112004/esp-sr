"""srpipe.tasks.command.kws.train: where a window ends, batches by share, SpecAugment, the operating point on val,
and a short run end to end on fake processed files."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from srpipe.core.config import load_yaml
from srpipe.dsp.spec import pitch
from srpipe.tasks import command
from srpipe.tasks.command import kws
from srpipe.tasks.command.kws import data
from srpipe.tasks.command.kws.postproc import decide

torch = pytest.importorskip("torch")

from srpipe.tasks.command.kws import train  # noqa: E402

CFG = load_yaml(kws.CONFIG)
NAMES = kws.classes(load_yaml(kws.CONFIG), load_yaml(command.CONFIG))
WINDOW = CFG["window_hops"]
N_BANDS = kws.n_dims(CFG) - pitch.N_FEATURES
ITEM_HOPS, SPEECH, VOICED, LATE = 160, [100, 120], (100, 136), 19


def test_vad_end_is_where_vad_turns_off_after_the_utterance() -> None:
    assert train.vad_end(np.r_[np.zeros(5), np.ones(10), np.zeros(5)], 6, 12) == 15
    assert train.vad_end(np.r_[np.ones(8), np.zeros(12)], 2, 15) == 8
    assert train.vad_end(np.zeros(20), 3, 9) == 9
    assert train.vad_end(np.r_[np.zeros(3), np.ones(17)], 5, 10) == 19


def test_a_window_ends_by_its_placement_and_starts_inside_its_item() -> None:
    assert train.placement(kws.SILENCE, "real") == train.ANYWHERE
    assert train.placement(kws.OTHER, "speech") == train.IN_SPEECH
    assert train.placement(kws.OTHER, "tts") == train.placement(NAMES[0], "real") == train.AT_END
    vad = np.zeros(ITEM_HOPS, dtype=np.int8)
    vad[slice(*VOICED)] = 1
    off = VOICED[1]
    assert train.end_span(vad, SPEECH, train.AT_END, WINDOW, LATE) == (off, off + LATE)
    assert train.end_span(vad, SPEECH, train.AT_END, WINDOW, 2 * LATE + 10) == (off, ITEM_HOPS - 1)
    assert train.end_span(vad, SPEECH, train.IN_SPEECH, WINDOW, LATE) == (SPEECH[0] + 1, off)
    assert train.end_span(vad, SPEECH, train.ANYWHERE, WINDOW, LATE) == (WINDOW - 1, ITEM_HOPS - 1)
    with pytest.raises(ValueError):
        train.end_span(vad[:120], [30, 60], train.AT_END, 200, LATE)


def fake_source(name: str, items: int, dims: int) -> train.Source:
    """Features that hold their own hop index, so a window shows where it was cut."""
    features = np.repeat(np.arange(items * ITEM_HOPS, dtype=np.float32)[:, None], dims, axis=1)
    first = np.arange(items) * ITEM_HOPS + WINDOW - 1
    cls = data.class_of(name, NAMES)
    return train.Source(name, cls, train.placement(cls, data.source_of(name)), features, first, first + 10)


def test_batches_hold_the_shares_and_cut_windows_inside_their_spans() -> None:
    sources = [fake_source(f"train_{c}_tts", 3, 4) for c in NAMES[:-1]]
    sources += [fake_source(f"train_{kws.OTHER}_speech", 5, 4), fake_source(f"train_{kws.SILENCE}_noise", 2, 4)]
    cfg = copy.deepcopy(CFG)
    cfg["train"]["batch"] = 4000
    batches = train.Batches(sources, NAMES, cfg, np.random.default_rng(0))
    assert batches.class_p[NAMES.index(kws.OTHER)] == pytest.approx(CFG["train"]["class_share"][kws.OTHER])
    assert batches.class_p.sum() == pytest.approx(1.0)
    shares = CFG["train"]["source_share"][kws.OTHER]
    total = shares["tts"] + shares["speech"]
    assert batches.file_p[NAMES.index(kws.OTHER)] == pytest.approx([shares["tts"] / total, shares["speech"] / total])
    x, labels = batches.draw()
    assert np.allclose(np.bincount(labels, minlength=len(NAMES)) / len(labels), batches.class_p, atol=0.02)
    ends = x[:, -1, 0].astype(int)
    assert np.all(ends - x[:, 0, 0].astype(int) == WINDOW - 1)
    assert np.all((ends - WINDOW + 1) % ITEM_HOPS <= 10)
    with pytest.raises(ValueError):
        train.class_shares(NAMES, {kws.OTHER: 0.7, kws.SILENCE: 0.4})
    with pytest.raises(ValueError):
        train.file_shares([fake_source(f"train_{kws.OTHER}_noise", 1, 4)], shares)


def test_masks_zero_whole_band_spans_and_whole_hop_spans() -> None:
    spec = {"bands": 2, "band_share": 0.15, "hops": 2, "hop_width": 10}
    x = np.ones((64, WINDOW, N_BANDS + pitch.N_FEATURES), dtype=np.float32)
    train.mask(x, spec, N_BANDS, np.random.default_rng(1))
    zero = x == 0.0
    masked_hops = zero.all(axis=2)
    assert masked_hops.any() and zero[~masked_hops].any()
    assert np.array_equal(zero[:, :, N_BANDS:].any(axis=2), masked_hops)
    for w, hops in zip(zero, masked_hops, strict=True):
        kept = w[~hops]
        assert np.array_equal(kept.all(axis=0), kept.any(axis=0))
        assert kept.all(axis=0).sum() <= spec["bands"] * round(spec["band_share"] * N_BANDS)
        assert hops.sum() <= spec["hops"] * spec["hop_width"]


def test_the_operating_point_meets_the_rejection_target_with_the_best_worst_command() -> None:
    names = ["a", "b", kws.OTHER, kws.SILENCE]
    rejected = decide.REJECTED
    decided = np.array(
        [
            [0, 900, 300],
            [0, 800, 300],
            [0, 700, 300],
            [1, 990, 300],
            [1, 950, 300],
            [1, 600, 300],
            [0, 650, 300],
            [1, 500, 300],
            [rejected, 400, 300],
            [rejected, 900, 300],
        ]
    )
    labels = np.array([0, 0, 0, 0, 1, 1, 2, 2, 3, 3])
    spec = {"reject_target": 0.95, "step_permille": 10}
    point = train.operating_point(decided, labels, names, spec)
    assert (point["reject_permille"], point["margin_permille"]) == (660, 0)
    assert point["rejection"] == 1.0 and point["recall"] == {"a": 0.75, "b": 0.5}
    assert point["wrong_command"] == pytest.approx(1 / 6)
    always = np.vstack([decided, [[0, 1000, 300]]])
    point = train.operating_point(always, np.r_[labels, 3], names, spec)
    assert point["rejection"] >= spec["reject_target"] and point["min_recall"] == 0.0


def fake_build(folder: Path, items: int, rng: np.random.Generator) -> None:
    """A finished simulation of one split file: items of ITEM_HOPS hops, speech at SPEECH, vad on over VOICED."""
    hops = items * ITEM_HOPS
    folder.mkdir(parents=True)
    stem = folder / "shard_00000"
    np.save(f"{stem}.features.npy", rng.standard_normal((hops, N_BANDS)).astype(np.float32))
    np.save(f"{stem}.pitch.npy", rng.standard_normal((hops, pitch.N_FEATURES)).astype(np.float32))
    figures = np.zeros((hops, 3), dtype=np.int8)
    for k in range(items):
        figures[k * ITEM_HOPS + VOICED[0] : k * ITEM_HOPS + VOICED[1], 0] = 1
    np.save(f"{stem}.figures.npy", figures)
    items_of = [{"frame_offset": k * ITEM_HOPS, "n_frames": ITEM_HOPS, "speech_frames": SPEECH} for k in range(items)]
    Path(f"{stem}.items.jsonl").write_text("".join(json.dumps(i) + "\n" for i in items_of), encoding="utf-8")
    built = {f"shard_00000.{part}": "" for part in ("features.npy", "pitch.npy", "figures.npy", "items.jsonl")}
    (folder / "manifest.yaml").write_text(yaml.safe_dump({"pitch": True, "sha256": built}), encoding="utf-8")


def test_a_short_run_trains_and_reports_val_figures(tmp_path: Path) -> None:
    rng = np.random.default_rng(3)
    files = [f"{role}_{c}_{'noise' if c == kws.SILENCE else 'tts'}" for role in data.ROLES for c in NAMES]
    files += [f"{role}_{kws.OTHER}_speech" for role in data.ROLES]
    for name in files:
        fake_build(tmp_path / name, 3, rng)

    def load(role: str, dtype: str) -> list[train.Source]:
        return [train.load_source(tmp_path / f, NAMES, WINDOW, LATE, dtype) for f in files if f.startswith(role)]

    sources, val = load("train_", "float16"), load("val_", "float32")
    said = next(s for s in sources if s.cls == NAMES[0])
    assert said.first.tolist() == [k * ITEM_HOPS + VOICED[1] for k in range(3)]
    assert (said.last - said.first).tolist() == [LATE] * 3
    held = train.held_windows(val, NAMES, WINDOW, 0)
    first = val[0].first[0]
    assert np.array_equal(held.windows[0], val[0].features[first - WINDOW + 1 : first + 1])
    cfg = copy.deepcopy(CFG)
    cfg["train"] |= {"batch": 8, "steps": 4, "eval_every": 2, "norm_batches": 2}
    cfg["eval"]["step_permille"] = 100
    model, stats, history = train.train(cfg, NAMES, sources, held, "cpu", tmp_path / "run")
    assert [row["step"] for row in history] == [2, 4]
    assert stats["mean"].shape == stats["std"].shape == (N_BANDS + pitch.N_FEATURES,)
    assert set(stats["val"]["by_file"]) == {s.name for s in val}
    assert (tmp_path / "run" / "checkpoints" / "step_000004.pt").exists()
    assert model(torch.zeros(1, 1, WINDOW, N_BANDS + pitch.N_FEATURES)).shape == (1, len(NAMES))
