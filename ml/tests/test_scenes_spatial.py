"""bss's scoring of srpipe.scenes.spatial: variants with bss_live's delayed output and the gates it gives each hop."""

from __future__ import annotations

import numpy as np
import pytest

from srpipe.dsp.afe import bss
from srpipe.generated import afe, grid
from srpipe.scenes import spatial

TURN_S = 1.5
TAU_MAX = 2.099  # samples of delay at endfire for board B's 4.5 cm


def talker(rng: np.random.Generator, seconds: float, delay_samples: float) -> np.ndarray:
    """A voiced, syllable-paced source at both microphones, the second delay_samples late."""
    n = int(seconds * grid.SAMPLE_RATE_HZ)
    t = np.arange(n) / grid.SAMPLE_RATE_HZ
    phase = 2 * np.pi * np.cumsum(140.0 + 20.0 * np.sin(2 * np.pi * 0.7 * t)) / grid.SAMPLE_RATE_HZ
    voiced = sum(np.sin(k * phase) / k for k in range(1, 49)) + 0.3 * rng.standard_normal(n)
    x = 0.1 * voiced * (0.55 + 0.45 * np.sin(2 * np.pi * 4.0 * t)) / np.max(np.abs(voiced))
    freq = np.fft.rfftfreq(n, 1 / grid.SAMPLE_RATE_HZ)
    late = np.fft.irfft(np.fft.rfft(x) * np.exp(-2j * np.pi * freq * delay_samples / grid.SAMPLE_RATE_HZ), n)
    return np.stack([x, late], axis=1)


def turns(delays: list[float], rounds: int) -> np.ndarray:
    rng = np.random.default_rng(0)
    total = np.concatenate([talker(rng, TURN_S, d) for _ in range(rounds) for d in delays])
    return np.clip(np.rint(total * spatial.PCM_FULL_SCALE), spatial.PCM_MIN, spatial.PCM_MAX).astype(np.int16)


def test_variants_take_the_output_settings_apart_from_bss() -> None:
    runs = spatial.bss_variants(
        {"variants": {"learn": {"mode": 1}, "late": {"mode": 1, "delay_s": 2, "lookahead_s": 0.1}}}
    )
    assert runs["learn"] == spatial.BssRun(bss.BssConfig(mode=bss.MODE_LEARN))
    assert runs["learn"].delay_s == 0.0 and runs["learn"].lookahead_s == afe.BSS_LOOKAHEAD_S
    assert runs["late"].cfg.mode == bss.MODE_LEARN and (runs["late"].delay_s, runs["late"].lookahead_s) == (2.0, 0.1)


@pytest.mark.parametrize("lookahead_s", [0.0, 0.5])
def test_delayed_gates_line_up_with_the_live_ones(lookahead_s: float) -> None:
    pcm = turns([1.4, -1.4], 2)
    fixed = bss.BssConfig(mode=bss.MODE_FIXED)
    live, _ = spatial.turn_gates(pcm, spatial.BssRun(fixed))
    late, _ = spatial.turn_gates(pcm, spatial.BssRun(fixed, delay_s=0.5, lookahead_s=lookahead_s))
    ahead = round(lookahead_s / spatial.HOP_S)
    assert len(np.unique(live)) > 2
    np.testing.assert_array_equal(late[: len(late) - ahead], live[ahead:])


def test_delayed_output_gates_a_talker_before_learn_splits() -> None:
    near, far = 1.6, 0.6  # both on one side of broadside, a min_gap and more apart
    pcm = turns([near, far], 3)
    # The second microphone hearing the talker late puts it past 90 deg: doa's delay is negative there.
    keep_deg = float(np.degrees(np.arccos(-near / TAU_MAX)))
    learn = bss.BssConfig(mode=bss.MODE_LEARN, keep_deg=keep_deg)
    live, split = spatial.turn_gates(pcm, spatial.BssRun(learn))
    late, _ = spatial.turn_gates(pcm, spatial.BssRun(learn, delay_s=4.0))
    hop_s = np.arange(len(live)) * spatial.HOP_S
    mid_turn = (hop_s % TURN_S >= 0.5) & (hop_s % TURN_S < TURN_S - 0.5)
    far_early = ((hop_s // TURN_S) % 2 == 1) & (hop_s < split) & mid_turn
    assert split is not None and np.any(far_early)
    assert live[far_early].mean() > 0.9 and late[far_early].mean() < 0.1
