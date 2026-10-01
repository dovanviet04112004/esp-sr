"""Gate 3 of the command tracks: which board sessions count, what each should get, where its windows sit, and how the
table scores them."""

from __future__ import annotations

import numpy as np

from srpipe.core import corpus
from srpipe.core.config import CONFIGS, load_yaml
from srpipe.dsp.spec.pitch import N_FEATURES, PitchConfig, PitchTracker
from srpipe.generated import grid
from srpipe.tasks.command import eval as gate
from srpipe.tasks.command.eval import REJECT, Scored

SPEC = {"pcm_shift": 13, "kinds": ["cmd", "neg"], "left_out": ["s3"]}


def test_only_sessions_of_the_products_shift_and_a_scored_kind_count() -> None:
    row = {"pcm_shift": "13", "kind": "cmd", "session": "s1"}
    assert gate.counted(row, SPEC)
    assert not gate.counted(row | {"pcm_shift": "16"}, SPEC)
    assert not gate.counted(row | {"kind": "wake"}, SPEC)
    assert not gate.counted(row | {"session": "s3"}, SPEC)


def test_a_command_session_expects_its_command_only_when_the_net_learned_it() -> None:
    command_of = {tuple(corpus.sounds("bật đèn")): "bat_den"}
    assert gate.expected_of("cmd", "bật đèn", command_of) == "bat_den"
    assert gate.expected_of("cmd", "mở cửa", command_of) == REJECT
    assert gate.expected_of("neg", "bật đèn", command_of) == REJECT


def test_a_window_ends_where_vad_turns_off_and_repeats_the_first_hop_before_the_session() -> None:
    hops, window, lead, bands = 200, 94, 100, 40
    features = np.arange(hops, dtype=np.float32)[:, None].repeat(bands, axis=1)
    clean = np.zeros(hops * grid.HOP_SAMPLES, dtype=np.int16)
    tracker = PitchTracker(PitchConfig(**load_yaml(CONFIGS / "scenes" / "device.yaml")["pitch"]))
    x = gate.windows(clean, features, [(120, 150), (10, 30)], window, lead, tracker)
    assert x.shape == (2, window, bands + N_FEATURES)
    assert x[0, -1, 0] == 151 and x[0, 0, 0] == 151 - window + 1
    assert x[1, -1, 0] == 31 and np.all(x[1, : window - 31, 0] == 0)


def test_the_table_scores_each_command_and_the_rejections_by_kind() -> None:
    results = [
        Scored("a", "cmd", "100", "bật đèn", "bat_den", [("bat_den", 900), ("bat_den", 800), (REJECT, 400)]),
        Scored("b", "cmd", "100", "mở cửa", REJECT, [(REJECT, 300), ("bat_den", 700)]),
        Scored("c", "neg", "100", "bật điện | tắt điện", REJECT, [(REJECT, 500)]),
    ]
    text = gate.table(results, ["bat_den", "other", "silence"], {"command_recall": 0.9, "rejection": 0.95})
    assert "- bat_den: 2/3 right (fail at 90%)" in text
    assert "- rejected: 2/3 (fail at 95%)" in text and "  - cmd: 1/2" in text and "  - neg: 1/1" in text
    assert "bật điện \\| tắt điện" in text
