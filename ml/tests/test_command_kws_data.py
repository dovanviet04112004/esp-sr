"""srpipe.tasks.command.kws.data: file names and classes, the voice and class of a TTS clip, real clips by class, and
silence stretches that keep each noise file in one role."""

from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
import yaml

from srpipe.core import splits
from srpipe.core.audio_io import write_wav
from srpipe.core.config import CONFIGS, load_yaml
from srpipe.generated import grid
from srpipe.tasks import command
from srpipe.tasks.command import kws
from srpipe.tasks.command.kws import data

FS = grid.SAMPLE_RATE_HZ
NAMES = kws.classes(load_yaml(command.CONFIG))
COMMAND_OF = {c["text"]: c["id"] for c in command.learned(load_yaml(command.CONFIG))}


def test_a_file_name_carries_role_class_and_source() -> None:
    for role in data.ROLES:
        for cls in NAMES:
            for source in data.SOURCES:
                name = data.file_name(role, cls, source)
                assert splits.role_of(name) == role and data.class_of(name, NAMES) == cls
                assert data.source_of(name) == source
    with pytest.raises(ValueError):
        data.class_of("train_chup_anh_tts.txt", NAMES)
    with pytest.raises(ValueError):
        data.class_of("train_bat_den_board.txt", NAMES)


def test_a_net_of_some_commands_keeps_their_order_and_refuses_an_unlearned_one() -> None:
    command_cfg = load_yaml(command.CONFIG)
    assert kws.classes(command_cfg, ["tat_den", "bat_den"]) == ["bat_den", "tat_den", kws.OTHER, kws.SILENCE]
    with pytest.raises(ValueError, match="chup_anh"):
        kws.classes(command_cfg, ["bat_den", "chup_anh"])


def test_a_tts_clip_goes_to_its_voices_role_and_its_commands_class(tmp_path: Path) -> None:
    def clip(cid: str, speaker: str, kept: bool, say: str | None) -> dict:
        wav = tmp_path / "command" / "synth_pos" / "f5" / f"{cid}.wav"
        return {"id": cid, "speaker": speaker, "kept": kept, "wav": str(wav)} | ({"say": say} if say else {})

    clips = [
        clip("preset_03_t0_s0", "⭐ Mai Anh", True, "bật đèn"),
        clip("clone_VIVOSSPK07_t1_s0", "VIVOSSPK07", True, "dừng lại"),
        clip("clone_bud500_00050_t2_s0", "bud500_00050_000", True, None),
        clip("clone_VIVOSSPK09_t0_s0", "VIVOSSPK09", False, "bật đèn"),
    ]
    assert [data.voice_of(c) for c in clips[:3]] == ["vieneu_preset_03", "VIVOSSPK07", splits.ABSENT]
    roles = {"vieneu_preset_03": "val", "VIVOSSPK07": "train"}
    rows = data.tts_rows(clips, tmp_path, roles, COMMAND_OF)
    assert set(rows) == {("val", "bat_den"), ("train", "dung_lai"), ("train", kws.OTHER)}
    assert rows[("val", "bat_den")][0].item == "command/synth_pos/f5/preset_03_t0_s0.wav"
    assert all(r.origin == data.SYNTH for group in rows.values() for r in group)


def test_real_clips_land_in_their_commands_class(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    extract = raw / "speech" / "hf"
    extract.mkdir(parents=True)
    fields = "file\tphrase\tseconds\torigin"
    lines = [
        "bat_den/a.wav\tbật đèn\t0.8\tpublic",
        "tro_ly/b.wav\ttrợ lý\t0.6\tpublic",
        "bat_den/c.wav\tbật đèn\t0.7\tsynth",
        "bat_den/d.wav\tbật đèn\t0.5\tpublic",
    ]
    (extract / "clips.tsv").write_text("\n".join([fields, *lines]) + "\n", encoding="utf-8")
    lengths = {"bat_den/a.wav": 0.8, "tro_ly/b.wav": 0.6, "bat_den/c.wav": 0.7, "bat_den/d.wav": 0.0}
    for name, seconds in lengths.items():
        write_wav(extract / name, np.zeros(round(seconds * FS)))
    rows, seconds = data.hf_rows(raw, "hf", COMMAND_OF)
    assert list(rows) == ["bat_den"] and [r.item for r in rows["bat_den"]] == ["speech/hf/bat_den/a.wav"]
    assert seconds == {"speech/hf/bat_den/a.wav": 0.8}
    theirs = raw / "speech" / "theirs"
    for folder, sub in (("train", "bat_den"), ("train", "bat_het"), ("train", "noise"), ("test", "tat_den")):
        write_wav(theirs / folder / sub / "x.wav", np.zeros(FS))
    spec = {
        "folders": ["speech/theirs/train", "speech/theirs/test"],
        "other": ["bat_het"],
        "silence": "speech/theirs/train/noise",
    }
    found, lengths = data.kws_vi_rows(raw, spec, set(COMMAND_OF.values()))
    assert {cls: len(r) for cls, r in found.items()} == {"bat_den": 1, kws.OTHER: 1, "tat_den": 1, kws.SILENCE: 1}
    assert all(s == pytest.approx(1.0) for s in lengths.values())


def test_silence_stretches_keep_each_noise_file_in_one_role(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    for k in range(20):
        write_wav(raw / "noise" / "a" / f"{k:02d}.wav", 0.01 * np.ones(FS * (1 + k % 3)))
    for k in range(10):
        write_wav(raw / "noise" / "b" / f"{k:02d}.wav", 0.01 * np.ones(4 * FS))
    spec = {
        "pools": [{"dir": "noise/a", "glob": "*.wav", "weight": 1}, {"dir": "noise/b", "glob": "*.wav", "weight": 1}],
        "count": {"train": 40, "val": 8},
        "seconds": [1.0, 3.0],
        "val_files": 0.2,
    }
    rows, seconds = data.silence_rows(spec, raw, set(), 7)
    assert {role: len(r) for role, r in rows.items()} == {"train": 40, "val": 8}
    files = {role: {r.item.split("@")[0] for r in group} for role, group in rows.items()}
    assert not files["train"] & files["val"]
    for group in rows.values():
        for r in group:
            name, span = r.item.split("@")
            start, end = (float(v) for v in span.split("-"))
            info = sf.info(str(raw / name))
            assert 0.0 <= start < end <= info.frames / info.samplerate + 1e-3
            assert seconds[r.item] == pytest.approx(end - start, abs=1e-3)
    again, _ = data.silence_rows(spec, raw, set(), 7)
    assert again == rows


def test_simulate_links_a_file_another_version_built_the_same_way(tmp_path: Path, monkeypatch) -> None:
    cfg = copy.deepcopy(load_yaml(kws.CONFIG))
    paths = {k: tmp_path / k for k in ("splits", "processed", "raw", "interim")}
    base = load_yaml(CONFIGS / cfg["features"])
    device_cfg = base | {"session": base["session"] | {"items": cfg["simulate"]["session_items"]}}
    for version in ("v1", "v2"):
        folder = paths["splits"] / "command_kws" / version
        folder.mkdir(parents=True)
        (folder / "train_silence_noise.txt").write_text("noise/a.wav@0.000-1.000\t-\t-\tpublic\n", encoding="utf-8")
    split_file = paths["splits"] / "command_kws" / "v1" / "train_silence_noise.txt"
    built = paths["processed"] / "command_kws" / "v1" / "train_silence_noise"
    built.mkdir(parents=True)
    (built / "shard_00000.features.npy").write_bytes(b"x")
    body = {"config": device_cfg, "split": {"file": split_file.name, "sha256": splits.sha256_of(split_file)}}
    body |= {"repeats": 1, "pads_s": list(cfg["simulate"]["pads_s"]), "pitch": True}
    (built / "manifest.yaml").write_text(yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(data.device, "build", lambda *a, **k: pytest.fail("built again"))
    cfg["split"]["version"] = "v2"
    data.simulate(cfg, paths)
    out = paths["processed"] / "command_kws" / "v2" / "train_silence_noise"
    assert (out / "manifest.yaml").samefile(built / "manifest.yaml")
    assert (out / "shard_00000.features.npy").samefile(built / "shard_00000.features.npy")
