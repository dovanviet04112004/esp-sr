"""Check srpipe.dsp.spec.spk_fbank against ReDimNet's TFMelBanks taken in float64 (KEHOACH 3.17)."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest
import scipy.signal

from srpipe.dsp.spec import spk_fbank
from srpipe.generated import grid, speaker

RNG = np.random.default_rng(20261011)
CFG = spk_fbank.contract_config()
# The mirror is float32 with its own FFT and log; TFMelBanks in float64 sits within this of it on a +-20 range.
MAX_GAP_TO_FLOAT64 = 1e-3


def redimnet_filterbanks(cfg: spk_fbank.SpkFbankConfig) -> np.ndarray:
    """get_filterbanks of ReDimNet's features_tf.py at the pin, vectorised in numpy as it is written there."""

    def hz2mel(hz: np.ndarray | float) -> np.ndarray:
        return 2595 * np.log10(1 + np.asarray(hz) / 700.0)

    nfft = cfg.fft_size // 2
    melpoints = np.linspace(hz2mel(cfg.f_min_hz), hz2mel(cfg.f_max_hz), cfg.n_bands + 2)
    lower, centre, upper = melpoints[:-2][None], melpoints[1:-1][None], melpoints[2:][None]
    bins_mel = hz2mel(np.linspace(0, grid.SAMPLE_RATE_HZ // 2, nfft))[1:][:, None]
    rising = (bins_mel - lower) / (centre - lower)
    falling = (upper - bins_mel) / (upper - centre)
    weights = np.maximum(0.0, np.minimum(rising, falling))
    return np.vstack([np.zeros((1, cfg.n_bands)), weights]).astype(np.float32)


def float64_tfmelbanks(pcm: np.ndarray, cfg: spk_fbank.SpkFbankConfig) -> np.ndarray:
    """TFMelBanks of the pin in float64: normalise, pre-emphasise with a reflected first sample, frame with padding,
    DFT, power, mel, log, less each band's mean over time; (frames, bands)."""
    eps = cfg.eps
    x = pcm.astype(np.float64) / 32768.0
    x = (x - x.mean()) / (x.std() + eps)
    x = np.concatenate([x[1:2], x])
    y = x[1:] - cfg.preemphasis * x[:-1]
    pad = cfg.hop_samples // 2
    y = np.pad(y, (pad, pad))
    window = scipy.signal.windows.hamming(cfg.frame_samples)
    count = spk_fbank.n_frames(cfg)
    frames = np.stack([y[t * cfg.hop_samples : t * cfg.hop_samples + cfg.frame_samples] for t in range(count)])
    spectrum = np.fft.rfft(frames * window, cfg.fft_size)[:, : cfg.fft_size // 2]
    power = np.clip(np.abs(spectrum) ** 2, eps, 1 / eps)
    bands = np.clip(power @ redimnet_filterbanks(cfg).astype(np.float64), eps, 1 / eps)
    logs = np.log(bands + eps)
    return logs - logs.mean(axis=0, keepdims=True)


def speechlike(n: int) -> np.ndarray:
    """Pitched pulses through a few resonances, amplitude-modulated like syllables, as int16."""
    t = np.arange(n) / grid.SAMPLE_RATE_HZ
    pulses = np.sin(2 * np.pi * 140 * t) ** 31
    b, a = scipy.signal.butter(2, [400, 2800], btype="band", fs=grid.SAMPLE_RATE_HZ)
    voiced = scipy.signal.lfilter(b, a, pulses) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    noisy = voiced + 0.02 * RNG.standard_normal(n)
    return np.round(noisy / np.max(np.abs(noisy)) * 12000).astype(np.int16)


def test_the_contract_gives_b0s_window_and_frames() -> None:
    fb = spk_fbank.SpkFbank(CFG)
    assert CFG.window_samples == speaker.WINDOW_SAMPLES == round(speaker.WINDOW_S * grid.SAMPLE_RATE_HZ)
    assert fb.n_frames == speaker.N_FRAMES == 149
    assert fb.run(speechlike(CFG.window_samples)).shape == (149, speaker.N_BANDS)


def test_window_is_scipys_symmetric_hamming_bit_for_bit() -> None:
    want = scipy.signal.windows.hamming(CFG.frame_samples).astype(np.float32)
    assert np.array_equal(spk_fbank.hamming(CFG.frame_samples), want)


def test_weights_are_redimnets_tensorflow_style_filterbank_bit_for_bit() -> None:
    weights = spk_fbank.tf_mel_weights(CFG)
    assert weights.shape == (CFG.fft_size // 2, CFG.n_bands)
    assert np.array_equal(weights, redimnet_filterbanks(CFG))
    assert not weights[0].any()


@pytest.mark.parametrize("kind", ["speech", "noise", "short"])
def test_features_match_tfmelbanks_in_float64(kind: str) -> None:
    n = CFG.window_samples
    pcm = {
        "speech": speechlike(n),
        "noise": (RNG.standard_normal(n) * 2000).astype(np.int16),
        "short": spk_fbank.tail(speechlike(n // 3), n),
    }[kind]
    got = spk_fbank.SpkFbank(CFG).run(pcm)
    want = float64_tfmelbanks(pcm, CFG)
    assert got.dtype == np.float32
    assert np.max(np.abs(got - want)) < MAX_GAP_TO_FLOAT64


def test_silence_gives_flat_finite_features() -> None:
    got = spk_fbank.SpkFbank(CFG).run(np.zeros(CFG.window_samples, dtype=np.int16))
    assert np.all(np.isfinite(got))
    # Every frame logs about -18, where float32's step is 2e-6; the mean of 149 of them drifts a few steps.
    assert np.max(np.abs(got)) < 1e-4


def test_tail_keeps_the_last_samples_and_pads_zeros_ahead() -> None:
    long = np.arange(30000, dtype=np.int16)
    assert np.array_equal(spk_fbank.tail(long, 24000), long[-24000:])
    short = np.arange(1, 101, dtype=np.int16)
    padded = spk_fbank.tail(short, 24000)
    assert padded.dtype == np.int16 and not padded[:-100].any() and np.array_equal(padded[-100:], short)


@pytest.mark.parametrize(
    "change",
    [
        {"fft_size": 500},
        {"frame_samples": 600},
        {"n_bands": 0},
        {"f_max_hz": 9000.0},
        {"window_samples": 1},
        {"eps": 0.0},
        {"preemphasis": 1.0},
    ],
)
def test_configs_the_c_side_refuses_are_refused(change: dict) -> None:
    with pytest.raises(ValueError):
        spk_fbank.SpkFbank(dataclasses.replace(CFG, **change))


def test_a_window_of_another_length_is_refused() -> None:
    with pytest.raises(ValueError):
        spk_fbank.SpkFbank(CFG).run(np.zeros(CFG.window_samples - 1, dtype=np.int16))
