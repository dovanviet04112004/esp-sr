"""vad follows the WebRTC VAD it ports and does what a hop-rate detector with a hangover must (E7-T3)."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import lfilter

from srpipe.dsp.afe import vad
from srpipe.generated import afe, grid

FS = grid.SAMPLE_RATE_HZ
WEBRTC_FRAME = 320
# WebRTC's own hangover at 20 ms frames by mode: frames after a short run, frames after a run of more than six.
WEBRTC_OVERHANG = {0: (4, 7), 1: (4, 7), 2: (3, 5), 3: (3, 5)}
WEBRTC_LONG_RUN = 6


def speechlike(rng: np.random.Generator, seconds: float, peak: float) -> np.ndarray:
    """A voiced harmonic series with a moving pitch, one formant, syllable and word envelopes."""
    t = np.arange(round(seconds * FS)) / FS
    phase = 2 * np.pi * np.cumsum(120 + 40 * np.sin(2 * np.pi * 0.7 * t) + rng.uniform(0, 80)) / FS
    voiced = lfilter([1.0], [1, -1.3, 0.8], sum(np.sin(k * phase) / k for k in range(1, 30)))
    syllables = np.clip(np.sin(2 * np.pi * rng.uniform(3, 5) * t + rng.uniform(0, 6)), 0, None) ** 0.7
    words = np.sin(2 * np.pi * rng.uniform(0.3, 0.6) * t + rng.uniform(0, 6)) > -0.2
    x = voiced * syllables * words
    return peak * x / np.abs(x).max()


def coloured(rng: np.random.Generator, n: int, rms: float, pink: bool) -> np.ndarray:
    w = rng.standard_normal(n)
    if pink:
        w = lfilter([0.049922, -0.095994, 0.050613, -0.004409], [1, -2.494956, 2.017266, -0.522189], w)
    return rms * w / w.std()


def run(detector: vad.Vad, x: np.ndarray) -> list[vad.VadHop]:
    n = detector.frame_samples
    return [detector.process(x[i : i + n].astype(np.float32)) for i in range(0, len(x) - n + 1, n)]


@pytest.mark.parametrize("mode", range(4))
def test_the_port_agrees_with_webrtc_frame_by_frame(mode: int) -> None:
    webrtcvad = pytest.importorskip("webrtcvad")
    rng = np.random.default_rng(10 + mode)
    agree = total = 0
    for peak, pink, snr_db in ((0.1, False, 20), (0.5, True, 5), (0.05, True, 20), (0.3, False, 5)):
        s = speechlike(rng, 8.0, peak)
        pcm = np.clip(
            np.rint((s + coloured(rng, len(s), peak / 4 * 10 ** (-snr_db / 20), pink)) * 32767), -32768, 32767
        )
        pcm = pcm.astype(np.int16)
        reference = webrtcvad.Vad(mode)
        frames = [pcm[i : i + WEBRTC_FRAME] for i in range(0, len(pcm) - WEBRTC_FRAME + 1, WEBRTC_FRAME)]
        want = [reference.is_speech(f.tobytes(), FS) for f in frames]
        port = vad.Vad(mode, hangover_ms=0, frame_samples=WEBRTC_FRAME)
        got = _with_webrtc_overhang([port.process(f.astype(np.float32) / 32768).raw for f in frames], mode)
        agree += sum(a == b for a, b in zip(want, got, strict=True))
        total += len(want)
    # The float model keeps update steps WebRTC's integer arithmetic rounds to 0, so it can settle a few frames apart.
    assert agree / total > 0.97


def _with_webrtc_overhang(raw: list[bool], mode: int) -> list[bool]:
    """WebRTC's hangover counters, a function of the raw decisions only."""
    short, long_ = WEBRTC_OVERHANG[mode]
    out, left, run_length = [], 0, 0
    for r in raw:
        if r:
            run_length = min(run_length + 1, WEBRTC_LONG_RUN + 1)
            left = long_ if run_length > WEBRTC_LONG_RUN else short
            out.append(True)
        else:
            run_length = 0
            out.append(left > 0)
            left = max(left - 1, 0)
    return out


def test_silence_is_never_speech_and_never_modelled() -> None:
    detector = vad.Vad(3)
    hops = run(detector, np.zeros(2 * FS))
    assert not any(h.speech or h.raw for h in hops)
    assert detector.hops_modelled == 0
    assert np.all(hops[0].features == np.array(afe.VAD_BAND_OFFSET_DB, dtype=np.float32))


def test_speech_holds_for_the_hangover_after_the_last_raw_decision() -> None:
    rng = np.random.default_rng(1)
    burst = speechlike(rng, 1.0, 0.3)
    x = np.concatenate([1e-4 * rng.standard_normal(FS), burst, 1e-4 * rng.standard_normal(FS)])
    hops = run(vad.Vad(0), x)
    last_raw = max(i for i, h in enumerate(hops) if h.raw)
    tail = [h.speech for h in hops[last_raw + 1 :]]
    assert vad.hangover_hops(afe.VAD_HANGOVER_MS) == 15
    assert tail[:15] == [True] * 15 and not any(tail[15:])


def test_a_higher_aggressiveness_never_finds_more_speech() -> None:
    rng = np.random.default_rng(2)
    x = speechlike(rng, 6.0, 0.2) + coloured(rng, 6 * FS, 0.01, pink=True)
    counts = [sum(h.raw for h in run(vad.Vad(a, hangover_ms=0), x)) for a in range(4)]
    assert counts == sorted(counts, reverse=True) and counts[0] > counts[3]


@pytest.mark.parametrize(
    ("freq_hz", "band"), [(170.0, 0), (370.0, 1), (750.0, 2), (1500.0, 3), (3500.0, 4), (2500.0, 5)]
)
def test_a_tone_is_loudest_in_its_band(freq_hz: float, band: int) -> None:
    t = np.arange(FS // 2) / FS
    hops = run(vad.Vad(0), 0.3 * np.sin(2 * np.pi * freq_hz * t))
    levels = hops[-1].features - np.array(afe.VAD_BAND_OFFSET_DB, dtype=np.float32)
    assert int(np.argmax(levels)) == band


def test_log2_linear_is_exact_at_powers_of_two_and_close_between() -> None:
    x = np.float32(2.0) ** np.arange(-10, 30, dtype=np.float32)
    assert all(vad.log2_linear(v) == np.log2(v) for v in x)
    between = np.float32(1.4427) * x
    assert max(abs(float(vad.log2_linear(v)) - float(np.log2(v))) for v in between) < 0.0861


def int16(v: int) -> int:
    return ((v & 0xFFFF) ^ 0x8000) - 0x8000


def webrtc_exp_value(t_steps: int) -> int:
    """GaussianProbability of vad_gmm.c from the Q10 exponent on: exp_value in Q10."""
    minus = int16(-t_steps)
    exp_value = 0x0400 | (minus & 0x03FF)
    shift = (int16(minus ^ 0xFFFF) >> 10) + 1
    return exp_value >> shift


def test_densities_take_webrtc_q10_steps() -> None:
    steps = int(afe.VAD_DENSITY_STEPS)
    for t_steps in [*range(0, 3 * steps, 7), 10 * steps - 1, 10 * steps, 11 * steps, 20 * steps]:
        got = vad.exp2_linear_neg(np.float32(t_steps / steps))
        assert got == np.float32(webrtc_exp_value(t_steps) / steps), t_steps


def test_hangover_hops_round_to_the_nearest_hop() -> None:
    assert vad.hangover_hops(0) == 0
    assert vad.hangover_hops(8) == 1
    assert vad.hangover_hops(7) == 0


@pytest.mark.parametrize("kwargs", [{"aggressiveness": 4}, {"aggressiveness": -1}, {"frame_samples": 250}])
def test_a_bad_configuration_is_refused(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        vad.Vad(**kwargs)


def test_a_frame_of_the_wrong_length_is_refused() -> None:
    with pytest.raises(ValueError, match="samples"):
        vad.Vad().process(np.zeros(grid.HOP_SAMPLES + 1, dtype=np.float32))


def test_reset_starts_the_model_over() -> None:
    rng = np.random.default_rng(3)
    x = speechlike(rng, 2.0, 0.2) + coloured(rng, 2 * FS, 0.01, pink=False)
    detector = vad.Vad(1)
    first = [h.raw for h in run(detector, x)]
    detector.reset()
    assert [h.raw for h in run(detector, x)] == first
