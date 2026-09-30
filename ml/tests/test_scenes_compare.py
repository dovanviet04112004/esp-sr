"""The comparison set of KEHOACH 3.16: prepare copies recordings exactly, finds planted speech, mixes at the asked SNR,
writes the same bytes twice, and refuses what builders could not read one way."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
import yaml

from srpipe.core.config import load_yaml
from srpipe.generated import grid
from srpipe.scenes import compare

HOP = grid.HOP_SAMPLES
FS = grid.SAMPLE_RATE_HZ
HOPS = 400
BURSTS = [(60, 90), (170, 220)]
RULE = {
    "floor_percentile": 10.0,
    "speech_above_floor_db": 10.0,
    "merge_gap_s": 0.3,
    "min_speech_s": 0.2,
    "noise_margin_s": 0.3,
    "min_noise_s": 0.5,
}
FIXED_VARIANTS = {
    "raw_ch0",
    "pc_mean",
    "pc_gsc",
    "pc_mean_omlsa",
    "pc_gsc_omlsa",
    "pc_gsc_omlsa20",
    "pc_gsc_nsnet2",
    "pc_gsc_rnnoise",
    "tinyai",
    "board_mean",
    "board_gsc",
    "board_mean_omlsa",
    "board_gsc_omlsa",
    "board_gsc_omlsa20",
    "espsr_bss",
    "espsr_bss_ch0",
    "espsr_bss_webrtc_mild",
    "espsr_bss_webrtc_medium",
    "espsr_bss_webrtc_aggressive",
    "espsr_bss_nsnet1",
    "espsr_bss_nsnet2",
    "espsr_bss_nsnet3",
    "board_gsc_espsr_webrtc_mild",
    "board_gsc_espsr_webrtc_medium",
    "board_gsc_espsr_webrtc_aggressive",
    "board_gsc_espsr_nsnet1",
    "board_gsc_espsr_nsnet2",
    "board_gsc_espsr_nsnet3",
}
FIXED_ITEMS = [
    "board_read_1m",
    "board_read_3m",
    "board_read_fan",
    "board_read_music",
    "board_cmd_bat_den",
    "board_cmd_tang_am_luong",
    "mix_fan_snr5",
    "mix_fan_snr0",
    "mix_music_snr5",
    "mix_music_snr0",
    "tinyai_alternating",
    "tinyai_overlap",
    "tinyai_talker_a",
    "tinyai_talker_b",
    "tinyai_music_voice",
]


def floor_noise(rng: np.random.Generator, n: int, dbfs: float) -> np.ndarray:
    return rng.standard_normal(n) * 32768.0 * 10.0 ** (dbfs / 20.0)


def talker(rng: np.random.Generator, ch: int) -> np.ndarray:
    x = floor_noise(rng, HOPS * HOP, -64.0)
    t = np.arange(HOPS * HOP) / FS
    for a, b in BURSTS:
        x[a * HOP : b * HOP] += 0.03 * 32768.0 * np.sin(2 * np.pi * (440.0 + 50.0 * ch) * t[a * HOP : b * HOP])
    return np.round(x).astype(np.int16)


def session(raw: Path, name: str, prompt: str, channels: list[np.ndarray]) -> None:
    folder = raw / compare.BOARD / name
    folder.mkdir(parents=True)
    for k, pcm in enumerate(channels):
        sf.write(str(folder / f"ch{k}.wav"), pcm, FS, subtype="PCM_16")
    (folder / "session.json").write_text(json.dumps({"prompt": prompt}, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def raw(tmp_path: Path) -> Path:
    root = tmp_path / "raw"
    rng = np.random.default_rng(7)
    session(root, "S_read", "giọng thường 1 m: xin chào các bạn", [talker(rng, 0), talker(rng, 1)])
    session(root, "S_cmd", "bật đèn", [talker(rng, 0), talker(rng, 1)])
    noise = [np.round(floor_noise(rng, 2 * HOPS * HOP, -50.0)).astype(np.int16) for _ in range(2)]
    session(root, "S_noise", "quạt chạy, không người nói", noise)
    tiny = root / "device" / "tinyai"
    tiny.mkdir(parents=True)
    sf.write(str(tiny / "two.wav"), np.stack([talker(rng, 0), talker(rng, 1)], axis=1), FS, subtype="PCM_16")
    sf.write(str(tiny / "out.wav"), talker(rng, 0)[: -2 * HOP], FS, subtype="PCM_16")
    sf.write(str(tiny / "fast.wav"), np.zeros((480, 2), dtype=np.int16), 3 * FS, subtype="PCM_16")
    return root


def config(seed: int = 11) -> dict:
    cfg = load_yaml(compare.CONFIG)
    mix = {"speech": "S_read", "speech_span_s": [0.0, 6.0], "noise": "S_noise", "noise_span_s": [0.0, 12.8]}
    cfg |= {"seed": seed, "segments": RULE}
    cfg["items"] = [
        {"name": "read", "session": "S_read", "text": "prompt", "segments": "auto"},
        {"name": "cmd", "session": "S_cmd", "text": "prompt", "text_scope": "segment", "segments": "auto"},
        {"name": "music", "session": "S_read", "text": "prompt", "segments": {"noise_s": [[0.0, 0.5]]}},
        {"name": "mix5", "mix": mix | {"snr_db": 5.0}, "text": "prompt"},
        {"name": "tiny", "raw": "device/tinyai/two.wav", "outside": {"tinyai": "device/tinyai/out.wav"}},
    ]
    return cfg


def read(path: Path) -> np.ndarray:
    return sf.read(str(path), dtype="int16", always_2d=True)[0]


def item_json(root: Path, name: str) -> dict:
    return json.loads((root / name / "item.json").read_text(encoding="utf-8"))


def test_the_committed_config_names_every_fixed_variant_and_item() -> None:
    cfg = load_yaml(compare.CONFIG)
    compare.validate(cfg)
    assert set(cfg["variants"]) == FIXED_VARIANTS
    assert [item["name"] for item in cfg["items"]] == FIXED_ITEMS
    assert set(cfg["segments"]) == set(RULE)


def test_a_session_is_copied_exactly_with_the_text_after_the_colon(raw: Path, tmp_path: Path) -> None:
    root = tmp_path / "out"
    compare.prepare(config(), raw, root)
    ch = [read(raw / compare.BOARD / "S_read" / f"ch{k}.wav")[:, 0] for k in range(2)]
    np.testing.assert_array_equal(read(root / "read" / "input.wav"), np.stack(ch, axis=1))
    np.testing.assert_array_equal(read(root / "read" / "raw_ch0.wav")[:, 0], ch[0])
    body = item_json(root, "read")
    assert (body["text"], body["text_scope"], body["channels"], body["rate_hz"]) == ("xin chào các bạn", "item", 2, FS)
    cmd = item_json(root, "cmd")
    assert (cmd["text"], cmd["text_scope"]) == ("bật đèn", "segment")
    assert body["sha256"]["input.wav"] == compare.sha256_of(root / "read" / "input.wav")


def test_auto_segments_are_the_planted_bursts_and_noise_keeps_its_margin(raw: Path, tmp_path: Path) -> None:
    root = tmp_path / "out"
    compare.prepare(config(), raw, root)
    hop_s = HOP / FS
    segments = item_json(root, "read")["segments"]
    assert segments["speech_s"] == [[round(a * hop_s, 3), round(b * hop_s, 3)] for a, b in BURSTS]
    margin = compare.to_hops(RULE["noise_margin_s"])
    expected_noise = [[0, 60 - margin], [90 + margin, 170 - margin], [220 + margin, HOPS]]
    assert segments["noise_s"] == [[round(a * hop_s, 3), round(b * hop_s, 3)] for a, b in expected_noise]
    hand = item_json(root, "music")["segments"]
    assert hand == {"speech_s": None, "noise_s": [[0.0, 0.5]]}


def test_a_floor_alone_holds_no_speech(raw: Path) -> None:
    noise = read(raw / compare.BOARD / "S_noise" / "ch0.wav")[:, 0]
    assert compare.detected_segments(noise, RULE)["speech_s"] == []


def test_a_mixture_adds_up_and_meets_its_snr_on_the_active_hops(raw: Path, tmp_path: Path) -> None:
    root = tmp_path / "out"
    compare.prepare(config(), raw, root)
    clean, noise, mixed = (read(root / "mix5" / f"{s}.wav").astype(np.int32) for s in ("clean", "noise", "input"))
    assert np.max(np.abs(mixed - clean - noise)) <= 1
    measured = compare.snr_db(clean, noise.astype(np.float64), RULE)
    assert measured == pytest.approx(5.0, abs=0.05)
    assert compare.snr_db(clean, 2.0 * noise.astype(np.float64), RULE) == pytest.approx(measured - 6.02, abs=0.01)
    source = item_json(root, "mix5")["source"]["mix"]
    assert 0.0 <= source["noise_start_s"] <= 12.8 - 6.0
    assert item_json(root, "mix5")["segments"]["speech_s"] == item_json(root, "read")["segments"]["speech_s"]


def test_an_outside_variant_is_copied_and_an_unlabelled_file_carries_no_text(raw: Path, tmp_path: Path) -> None:
    root = tmp_path / "out"
    compare.prepare(config(), raw, root)
    np.testing.assert_array_equal(read(root / "tiny" / "tinyai.wav"), read(raw / "device" / "tinyai" / "out.wav"))
    body = item_json(root, "tiny")
    assert (body["text"], body["segments"]) == (None, None)
    assert body["variants"]["tinyai"]["samples"] == HOPS * HOP - 2 * HOP


def test_two_runs_write_the_same_bytes_and_only_the_seed_moves_the_noise(raw: Path, tmp_path: Path) -> None:
    listings = []
    for n, seed in enumerate((11, 11, 12)):
        manifest = compare.prepare(config(seed), raw, tmp_path / f"out{n}")
        listings.append(yaml.safe_load(manifest.read_text(encoding="utf-8"))["items"])
    assert listings[0] == listings[1]
    assert listings[2]["read"] == listings[0]["read"]
    assert listings[2]["mix5"]["input.wav"] != listings[0]["mix5"]["input.wav"]


def test_prepare_refuses_what_could_be_read_two_ways(raw: Path, tmp_path: Path) -> None:
    cfg = config()
    two_sources = copy.deepcopy(cfg)
    two_sources["items"][0]["raw"] = "device/tinyai/two.wav"
    undeclared = copy.deepcopy(cfg)
    undeclared["items"][4]["outside"] = {"pc_gsc": "device/tinyai/out.wav"}
    repeated = copy.deepcopy(cfg)
    repeated["items"].append(dict(cfg["items"][0]))
    for bad, match in ((two_sources, "exactly one"), (undeclared, "not a variant"), (repeated, "repeat")):
        with pytest.raises(ValueError, match=match):
            compare.prepare(bad, raw, tmp_path / "out")


def test_prepare_refuses_a_rate_a_short_noise_span_and_a_clipping_mix(raw: Path, tmp_path: Path) -> None:
    fast = config()
    fast["items"] = [{"name": "fast", "raw": "device/tinyai/fast.wav"}]
    short = config()
    short["items"][3]["mix"]["noise_span_s"] = [0.0, 3.0]
    loud = config()
    loud["items"][3]["mix"]["snr_db"] = -60.0
    for bad, match in ((fast, "Hz"), (short, "shorter than the speech"), (loud, "clips")):
        with pytest.raises(ValueError, match=match):
            compare.prepare(bad, raw, tmp_path / "out")


def test_render_writes_each_pc_variant_through_the_python_chain(raw: Path, tmp_path: Path) -> None:
    cfg = config()
    cfg["items"] = cfg["items"][:1]
    cfg["variants"] = {k: v for k, v in cfg["variants"].items() if k in ("raw_ch0", "pc_mean", "pc_gsc_omlsa")}
    cfg["workers"] = 1
    compare.prepare(cfg, raw, tmp_path / "set")
    assert compare.render(cfg, tmp_path / "set") == ["read: pc_mean, pc_gsc_omlsa"]
    x = read(tmp_path / "set/read/input.wav")
    mean, cleaned = read(tmp_path / "set/read/pc_mean.wav"), read(tmp_path / "set/read/pc_gsc_omlsa.wav")
    assert len(mean) == len(cleaned) == len(x) // HOP * HOP
    assert not np.array_equal(mean, cleaned)


def test_score_finds_delay_and_gain_and_credits_only_a_noise_cut(raw: Path, tmp_path: Path, monkeypatch) -> None:
    cfg = config()
    cfg["items"] = cfg["items"][:1]
    root = tmp_path / "set"
    compare.prepare(cfg, raw, root)
    folder = root / "read"
    x = read(folder / "raw_ch0.wav")[:, 0].astype(np.float64)
    late = np.concatenate([np.zeros(100), 0.5 * x])[: len(x)]
    sf.write(str(folder / "late_half.wav"), np.round(late).astype(np.int16), FS, subtype="PCM_16")
    quiet_noise = x.copy()
    for a, b in item_json(root, "read")["segments"]["noise_s"]:
        quiet_noise[round(a * FS) : round(b * FS)] *= 0.1
    sf.write(str(folder / "noise_cut.wav"), np.round(quiet_noise).astype(np.int16), FS, subtype="PCM_16")
    scores = {"sig": 3.0, "bak": 4.0, "ovrl": 2.5}
    monkeypatch.setattr(compare.refs, "dnsmos", lambda clips, *a: {cid: scores for cid in clips})
    rows = compare.score(cfg, root, tmp_path / "listen", tmp_path / "cache")["read"]
    assert rows["late_half"]["ovrl"] == 2.5
    assert rows["raw_ch0"]["lag_ms"] == 0 and rows["raw_ch0"]["noise_db"] == 0 and rows["raw_ch0"]["snr_gain_db"] == 0
    assert rows["late_half"]["lag_ms"] == round(1000 * 100 / FS, 1)
    assert abs(rows["late_half"]["speech_db"] + 6.02) < 0.1 and abs(rows["late_half"]["snr_gain_db"]) < 0.1
    assert abs(rows["noise_cut"]["noise_db"] + 20) < 0.5 and rows["noise_cut"]["snr_gain_db"] > 19
    assert (tmp_path / "listen/read/late_half.wav").exists()
    compare.write_rows({"read": rows}, tmp_path / "rows.csv")
    assert (tmp_path / "rows.csv").read_text().splitlines()[0].startswith("item,variant,lag_ms")


def test_refs_runs_each_engine_once_over_every_item_from_the_variant_it_follows(tmp_path: Path, monkeypatch) -> None:
    cfg = config()
    calls = []
    monkeypatch.setattr(compare.refs, "enhance", lambda name, pairs, spec, *a: calls.append((name, pairs, spec)))
    done = compare.run_refs(cfg, tmp_path, tmp_path / "cache")
    names = [item["name"] for item in cfg["items"]]
    assert [c[0] for c in calls] == ["nsnet2", "rnnoise"]
    assert done == [f"nsnet2: {len(names)} clips", f"rnnoise: {len(names)} clips"]
    assert calls[0][1] == [(tmp_path / n / "pc_gsc.wav", tmp_path / n / "pc_gsc_nsnet2.wav") for n in names]
    assert calls[0][2] == cfg["refs"]["nsnet2"] and calls[1][2] is None
