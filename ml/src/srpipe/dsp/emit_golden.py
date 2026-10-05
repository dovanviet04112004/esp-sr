"""Write the golden cases of the pure dsp blocks into contracts/golden/<block>/ (KEHOACH 3.14, 4.2).

Run from ml/: uv run python -m srpipe.dsp.emit_golden. Output is deterministic, so a re-run on an
unchanged reference rewrites the same bytes, which ml/tests/test_emit_golden.py checks.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import numpy as np

from srpipe.dsp.afe import agc, balance, chain, doa, gsc, hpf, ns_omlsa, vad
from srpipe.dsp.spec import mel, pitch, stft
from srpipe.generated import afe, array, grid, listen
from srpipe.golden.gold import write_gold

REPO_ROOT = Path(__file__).resolve().parents[4]
GOLDEN_ROOT = REPO_ROOT / "contracts" / "golden"
STFT_HOPS = 16
NEGATIVE_HOPS = 4
SEED = 20260926
MEL_FRAMES = 8
CHAIN_HOPS = 16
CHAIN_RESET_HOP = 8
CHAIN_MODULES_HOPS = 192
HPF_HOPS = 16
BALANCE_HOPS = 16
# Every vad case outlives the 100-hop window of the minimum tracker.
VAD_HOPS = 160
AGC_LONG_HOPS = 128
AGC_HOPS = 48
# ns cases outlive two minimum windows of IMCRA (2 x 64 hops), what a rise of the noise takes to follow.
NS_HOPS = 200
NS_RISE_HOPS = 240
NS_RISE_AT_HOP = 60
NS_ECHO_HOPS = 150
NS_SILENT_LEAD_HOPS = 20
NS_BURST_HOPS = 40
DOA_HOPS = 64
DOA_SILENT_HOPS = 16
GSC_HOPS = 64
GSC_LEARN_HOPS = 16
PITCH_HOPS = 96
PITCH_WINDOW_HOPS = 300  # past ballast_window_s: the ring of the ballast turns
PITCH_RESET_HOP = 60
LSB = 1.0 / 32768.0
MEL_CASES = (
    (mel.MelConfig(n_bands=40, f_min_hz=20.0, f_max_hz=7600.0, log_floor=1e-6), 13),
    (mel.MelConfig(n_bands=80, f_min_hz=0.0, f_max_hz=8000.0, log_floor=1e-10), 20),
    (mel.MelConfig(n_bands=24, f_min_hz=100.0, f_max_hz=4000.0, log_floor=1e-3), 24),
)


def _signals(rng: np.random.Generator, hops: int) -> dict[str, np.ndarray]:
    n = hops * grid.HOP_SAMPLES
    t = np.arange(n) / grid.SAMPLE_RATE_HZ
    clicks = np.zeros(n)
    clicks[:: grid.SAMPLE_RATE_HZ // 120] = 0.9
    return {
        "noise": rng.uniform(-0.5, 0.5, n),
        "tone_and_clicks": 0.4 * np.sin(2 * np.pi * 440.0 * t) + clicks * 0.5,
        "chirp": 0.5 * np.sin(2 * np.pi * (50.0 * t + (7900.0 - 50.0) / 2.0 * t**2 / t[-1])),
        "near_full_scale": rng.uniform(-0.99, 0.99, n),
    }


def stft_case(signal: np.ndarray) -> dict[str, np.ndarray]:
    """signal (hops * HOP), the bins the analyser gives (hops, N_BINS, 2) and the synthesis of those bins."""
    signal = signal.astype(np.float32)
    spectra = stft.analyze_signal(signal)
    rebuilt = stft.synthesize_signal(spectra)
    bins = np.stack([spectra.real, spectra.imag], axis=-1).astype(np.float32)
    return {"signal": signal, "bins": bins, "rebuilt": rebuilt}


def emit_stft(root: Path) -> list[Path]:
    """Four cases of STFT_HOPS hops, then a negative control whose rebuilt output is one sample late."""
    rng = np.random.default_rng(SEED)
    written = []
    for index, (_, signal) in enumerate(_signals(rng, STFT_HOPS).items()):
        path = root / "stft" / f"case_{index:03d}.gold"
        write_gold(path, stft_case(signal))
        written.append(path)
    negative = stft_case(rng.uniform(-0.5, 0.5, NEGATIVE_HOPS * grid.HOP_SAMPLES))
    negative["rebuilt"] = np.concatenate([[0.0], negative["rebuilt"][:-1]]).astype(np.float32)
    path = root / "stft" / "case_neg_000.gold"
    write_gold(path, negative)
    written.append(path)
    return written


def mel_case(cfg: mel.MelConfig, n_ceps: int, signal: np.ndarray) -> dict[str, np.ndarray]:
    """The configuration as floats, the bins of each frame, and the log-mel and MFCC the reference gives."""
    spectra = stft.analyze_signal(signal.astype(np.float32))
    bank = mel.Mel(cfg)
    log_mel = np.stack([bank.log(s) for s in spectra])
    return {
        "config": np.array([cfg.n_bands, cfg.f_min_hz, cfg.f_max_hz, cfg.log_floor, n_ceps], dtype=np.float32),
        "bins": np.stack([spectra.real, spectra.imag], axis=-1).astype(np.float32),
        "log_mel": log_mel,
        "mfcc": np.stack([bank.mfcc(frame, n_ceps) for frame in log_mel]),
    }


def emit_mel(root: Path) -> list[Path]:
    """Three configurations on noise, two tones and near silence, then a negative control one band off."""
    rng = np.random.default_rng(SEED + 1)
    n = MEL_FRAMES * grid.HOP_SAMPLES
    t = np.arange(n) / grid.SAMPLE_RATE_HZ
    inputs = (
        rng.uniform(-0.5, 0.5, n),
        0.4 * np.sin(2 * np.pi * 300.0 * t) + 0.3 * np.sin(2 * np.pi * 2500.0 * t),
        rng.uniform(-1e-4, 1e-4, n),
    )
    written = []
    for index, ((cfg, n_ceps), signal) in enumerate(zip(MEL_CASES, inputs, strict=True)):
        path = root / "mel" / f"case_{index:03d}.gold"
        write_gold(path, mel_case(cfg, n_ceps, signal))
        written.append(path)
    negative = mel_case(*MEL_CASES[0], rng.uniform(-0.5, 0.5, n))
    negative["log_mel"] = np.roll(negative["log_mel"], -1, axis=1)
    path = root / "mel" / "case_neg_000.gold"
    write_gold(path, negative)
    written.append(path)
    return written


def pitch_case(cfg: pitch.PitchConfig, signal: np.ndarray, reset: np.ndarray) -> dict[str, np.ndarray]:
    """The configuration as floats, the samples, a reset flag per hop taken before that hop, and per hop the three
    features and the newest frame's [NCCF, F0 Hz] the reference gives."""
    tracker = pitch.PitchTracker(cfg)
    hops = len(reset)
    features = np.zeros((hops, pitch.N_FEATURES), dtype=np.float32)
    raw = np.zeros((hops, 2), dtype=np.float32)
    since_reset = 0
    for h in range(hops):
        if reset[h]:
            tracker.reset()
            since_reset = 0
        features[h] = tracker.step(signal[h * grid.HOP_SAMPLES : (h + 1) * grid.HOP_SAMPLES])
        if since_reset >= pitch.LEAD_HOPS:
            raw[h] = tracker.latest
        since_reset += 1
    return {
        "config": np.array([getattr(cfg, f) for f in cfg.__dataclass_fields__], dtype=np.float32),
        "pcm": signal.astype(np.float32),
        "reset": reset.astype(np.uint8),
        "features": features,
        "raw": raw,
    }


def emit_pitch(root: Path, cfg: pitch.PitchConfig) -> list[Path]:
    """A glide, voiced bursts between pauses, noise, a quiet lead into a tone with a reset part way, a loud voice then
    2.5 s of quiet then a softer one, whose ballast forgets the loud part, then a negative control whose features
    come one hop late."""
    rng = np.random.default_rng(SEED + 11)
    n = PITCH_HOPS * grid.HOP_SAMPLES
    t = np.arange(n) / grid.SAMPLE_RATE_HZ
    glide = 2.0 * np.pi * np.cumsum(np.linspace(110.0, 240.0, n)) / grid.SAMPLE_RATE_HZ
    voiced = sum(np.sin(k * glide) / k for k in range(1, 6))
    bursts = (np.sin(2.0 * np.pi * 3.0 * t) > 0.2) * sum(np.sin(2 * np.pi * k * 180.0 * t) / k for k in range(1, 6))
    quiet_then_tone = np.where(t < t[-1] / 3, rng.uniform(-1e-3, 1e-3, n), 0.3 * np.sin(2 * np.pi * 95.0 * t))
    no_reset = np.zeros(PITCH_HOPS, dtype=np.uint8)
    reset_part_way = no_reset.copy()
    reset_part_way[PITCH_RESET_HOP] = 1
    inputs = (
        (0.3 * voiced + rng.normal(0.0, 0.01, n), no_reset),
        (0.3 * bursts + rng.normal(0.0, 0.003, n), no_reset),
        (rng.uniform(-0.5, 0.5, n), no_reset),
        (quiet_then_tone, reset_part_way),
    )
    long_t = np.arange(PITCH_WINDOW_HOPS * grid.HOP_SAMPLES) / grid.SAMPLE_RATE_HZ
    loud = 0.5 * sum(np.sin(2 * np.pi * k * 150.0 * long_t) / k for k in range(1, 6))
    soft = 0.2 * sum(np.sin(2 * np.pi * k * 120.0 * long_t) / k for k in range(1, 6))
    quiet = rng.uniform(-1e-3, 1e-3, len(long_t))
    loud_quiet_soft = np.where(long_t < 1.5, loud, np.where(long_t < 4.0, quiet, soft))
    inputs += ((loud_quiet_soft, np.zeros(PITCH_WINDOW_HOPS, dtype=np.uint8)),)
    written = []
    for index, (signal, reset) in enumerate(inputs):
        path = root / "pitch" / f"case_{index:03d}.gold"
        write_gold(path, pitch_case(cfg, signal, reset))
        written.append(path)
    negative = pitch_case(cfg, inputs[0][0], no_reset)
    negative["features"] = np.concatenate([np.zeros((1, pitch.N_FEATURES)), negative["features"][:-1]]).astype(
        np.float32
    )
    path = root / "pitch" / "case_neg_000.gold"
    write_gold(path, negative)
    written.append(path)
    return written


def _chain_inputs(rng: np.random.Generator) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """(ch0, ch1, reset flags) per case: two tones, clipped noise, a chirp with a reset, near silence."""
    n = CHAIN_HOPS * grid.HOP_SAMPLES
    t = np.arange(n) / grid.SAMPLE_RATE_HZ
    no_reset = np.zeros(CHAIN_HOPS, dtype=np.uint8)
    with_reset = no_reset.copy()
    with_reset[CHAIN_RESET_HOP] = 1
    chirp = 9000.0 * np.sin(2 * np.pi * (50.0 * t + (7900.0 - 50.0) / 2.0 * t**2 / t[-1]))
    quiet = rng.integers(-3, 4, n)
    quiet[: n // 2] = 0
    return [
        (
            8000.0 * np.sin(2 * np.pi * 440.0 * t),
            6000.0 * np.sin(2 * np.pi * 1000.0 * t) + rng.normal(0, 300, n),
            no_reset,
        ),
        (rng.uniform(-40000, 40000, n), rng.uniform(-40000, 40000, n), no_reset),
        (chirp, chirp, with_reset),
        (quiet, quiet, no_reset),
    ]


def chain_case(ch0: np.ndarray, ch1: np.ndarray, reset: np.ndarray, cfg: chain.ChainConfig) -> dict[str, np.ndarray]:
    """Interleaved int16 input, the hops to reset before, the afe/* settings (ns floor dB, agc target dBFS, vad
    mode), calib/bal as re, im pairs when the case has one, and every field of the frames the chain gives."""
    pcm_min, pcm_max = np.iinfo(np.int16).min, np.iinfo(np.int16).max
    mics = np.stack([ch0, ch1], axis=-1)
    interleaved = np.clip(np.rint(mics), pcm_min, pcm_max).astype(np.int16).reshape(reset.size, -1)
    ch = chain.Chain("MM", cfg)
    frames = []
    for hop, flag in zip(interleaved, reset, strict=True):
        if flag:
            ch.reset()
        frames.append(ch.process(hop))
    settings = [cfg.ns_floor_db, cfg.agc_target_dbfs, cfg.vad_aggressiveness]
    calib = {} if cfg.balance_gains is None else {"gains": _pairs(cfg.balance_gains)}
    return {
        "input": interleaved,
        "reset": reset,
        "config": np.array(settings, dtype=np.float32),
        **calib,
        "pcm": np.stack([f.pcm for f in frames]),
        "seq": np.array([f.seq for f in frames], dtype=np.int32),
        "doa_deg": np.array([f.doa_deg for f in frames], dtype=np.int16),
        "doa_conf": np.array([f.doa_conf for f in frames], dtype=np.uint8),
        "vad": np.array([f.vad for f in frames], dtype=np.uint8),
        "level_dbfs": np.array([f.level_dbfs for f in frames], dtype=np.int8),
        "gain_db": np.array([f.gain_db for f in frames], dtype=np.int8),
        "flags": np.array([f.flags for f in frames], dtype=np.int32),
    }


def emit_chain(root: Path) -> list[Path]:
    """Four cases through the facade with every module off, then a negative control whose output is one sample
    late."""
    rng = np.random.default_rng(SEED + 2)
    plain = chain.ChainConfig(modules=())
    written = []
    for index, inputs in enumerate(_chain_inputs(rng)):
        path = root / "chain" / f"case_{index:03d}.gold"
        write_gold(path, chain_case(*inputs, plain))
        written.append(path)
    n = CHAIN_HOPS * grid.HOP_SAMPLES
    negative = chain_case(rng.normal(0, 4000, n), rng.normal(0, 4000, n), np.zeros(CHAIN_HOPS, dtype=np.uint8), plain)
    late = np.concatenate([[0], negative["pcm"].reshape(-1)[:-1]]).astype(np.int16)
    negative["pcm"] = late.reshape(negative["pcm"].shape)
    path = root / "chain" / "case_neg_000.gold"
    write_gold(path, negative)
    written.append(path)
    return written


def _chain_modules_inputs(
    rng: np.random.Generator,
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray, chain.ChainConfig]]:
    """(ch0, ch1, reset flags, configuration) per case, through the product's modules: quiet speech with pauses on
    a board whose ch1 is 11 dB down, calibrated; loud speech with bursts to full scale on a board never calibrated;
    speech on hum and DC, reset halfway; noise alone under a lower target."""
    n = CHAIN_MODULES_HOPS * grid.HOP_SAMPLES
    t = np.arange(n) / grid.SAMPLE_RATE_HZ
    no_reset = np.zeros(CHAIN_MODULES_HOPS, dtype=np.uint8)
    with_reset = no_reset.copy()
    with_reset[CHAIN_MODULES_HOPS // 2] = 1
    ch1_down = 10 ** (-11.0 / 20.0)
    pauses = np.repeat(np.arange(CHAIN_MODULES_HOPS) % 64 < 40, grid.HOP_SAMPLES)
    quiet = speechlike(rng, n, 0.01) * pauses
    floor = 3e-4 * rng.standard_normal((2, n))
    loud = speechlike(rng, n, 0.7)
    for start in rng.integers(0, n - 64, 6):
        loud[start : start + 32] = rng.uniform(-1.5, 1.5, 32)
    hum = 0.05 * np.sin(2 * np.pi * 50.0 * t) + 0.02
    voice = speechlike(rng, n, 0.1)
    board = chain.ChainConfig(balance_gains=board_like_gains())
    return [
        (32768 * (quiet + floor[0]), 32768 * (ch1_down * quiet + floor[1]), no_reset, board),
        (32768 * loud, 32768 * 0.8 * loud, no_reset, chain.ChainConfig(agc_target_dbfs=-20.0, vad_aggressiveness=3)),
        (
            32768 * (voice + hum),
            32768 * (ch1_down * voice + hum),
            with_reset,
            chain.ChainConfig(balance_gains=board_like_gains(), vad_aggressiveness=0),
        ),
        (
            32768 * 0.003 * rng.standard_normal(n),
            32768 * 0.003 * rng.standard_normal(n),
            no_reset,
            chain.ChainConfig(balance_gains=board_like_gains(), agc_target_dbfs=-30.0, vad_aggressiveness=1),
        ),
    ]


def emit_chain_modules(root: Path) -> list[Path]:
    """Four cases through the facade with the product's modules, then a negative control that carries calib/bal
    but was computed without it."""
    rng = np.random.default_rng(SEED + 7)
    written = []
    for index, inputs in enumerate(_chain_modules_inputs(rng)):
        path = root / "chain_modules" / f"case_{index:03d}.gold"
        write_gold(path, chain_case(*inputs))
        written.append(path)
    n = CHAIN_MODULES_HOPS * grid.HOP_SAMPLES
    voice = 32768 * speechlike(rng, n, 0.05)
    no_reset = np.zeros(CHAIN_MODULES_HOPS, dtype=np.uint8)
    negative = chain_case(voice, 10 ** (-11.0 / 20.0) * voice, no_reset, chain.ChainConfig())
    negative["gains"] = _pairs(board_like_gains())
    path = root / "chain_modules" / "case_neg_000.gold"
    write_gold(path, negative)
    written.append(path)
    return written


def _hpf_inputs(rng: np.random.Generator) -> list[np.ndarray]:
    """(N_MICS, samples) per case: speech-like with DC, strong 50 Hz hum with DC, a chirp, near full scale."""
    n = HPF_HOPS * grid.HOP_SAMPLES
    t = np.arange(n) / grid.SAMPLE_RATE_HZ
    voiced = 0.03 * np.sin(2 * np.pi * 150.0 * t) * np.clip(np.sin(2 * np.pi * 4.0 * t), 0, None)
    hum = 0.3 * np.sin(2 * np.pi * 50.0 * t)
    chirp = 0.5 * np.sin(2 * np.pi * (20.0 * t + (7900.0 - 20.0) / 2.0 * t**2 / t[-1]))
    return [
        np.stack([voiced - 0.5 * LSB, voiced + rng.normal(0, 1e-4, n) - 1.5 * LSB]),
        np.stack([hum + 0.1, hum - 0.05]),
        np.stack([chirp, -chirp]),
        rng.uniform(-0.99, 0.99, (array.N_MICS, n)),
    ]


def hpf_case(signal: np.ndarray, cutoff_hz: float = afe.HPF_CUTOFF_HZ) -> dict[str, np.ndarray]:
    """Input and output per channel, filtered one hop per call as dsp_afe runs it, and the cutoff used."""
    signal = signal.astype(np.float32)
    filt = hpf.Hpf(cutoff_hz, signal.shape[0])
    out = np.stack(
        [
            np.concatenate([filt.process(ch, hop) for hop in np.split(signal[ch], HPF_HOPS)])
            for ch in range(signal.shape[0])
        ]
    )
    return {"input": signal, "output": out.astype(np.float32), "cutoff_hz": np.array([cutoff_hz], dtype=np.float32)}


def emit_hpf(root: Path) -> list[Path]:
    """Four cases at the contract's cutoff, then a negative control whose output is one sample late."""
    rng = np.random.default_rng(SEED + 3)
    written = []
    for index, signal in enumerate(_hpf_inputs(rng)):
        path = root / "hpf" / f"case_{index:03d}.gold"
        write_gold(path, hpf_case(signal))
        written.append(path)
    negative = hpf_case(rng.uniform(-0.5, 0.5, (array.N_MICS, HPF_HOPS * grid.HOP_SAMPLES)))
    negative["output"] = np.roll(negative["output"], 1, axis=1)
    negative["output"][:, 0] = 0.0
    path = root / "hpf" / "case_neg_000.gold"
    write_gold(path, negative)
    written.append(path)
    return written


def board_like_gains() -> np.ndarray:
    """Gains shaped like an estimate of board B: about -11 dB with a bump near 3 kHz, -0.1 sample and -2.2 degrees."""
    freqs_hz = np.arange(grid.N_BINS) * grid.SAMPLE_RATE_HZ / grid.FFT_SIZE
    level = 10 ** (-11.0 / 20.0) * (1.0 + 0.3 * np.exp(-(((freqs_hz - 3000.0) / 2400.0) ** 2)))
    omega = 2.0 * np.pi * np.arange(grid.N_BINS) / grid.FFT_SIZE
    phase = -(np.radians(-2.2) + omega * -0.1)
    phase[0] = phase[-1] = 0.0
    return (level * np.exp(1j * phase)).astype(np.complex64)


def _balance_inputs(rng: np.random.Generator) -> list[tuple[np.ndarray, np.ndarray]]:
    """(ch1 signal, gains) per case: board-like gains on noise, unit gains on a chirp, wide gains near full scale,
    board-like gains on near silence."""
    n = BALANCE_HOPS * grid.HOP_SAMPLES
    t = np.arange(n) / grid.SAMPLE_RATE_HZ
    chirp = 0.5 * np.sin(2 * np.pi * (20.0 * t + (7900.0 - 20.0) / 2.0 * t**2 / t[-1]))
    wide = 10 ** rng.uniform(-2.0, 1.0, grid.N_BINS) * np.exp(1j * rng.uniform(-np.pi, np.pi, grid.N_BINS))
    return [
        (rng.uniform(-0.5, 0.5, n), board_like_gains()),
        (chirp, np.ones(grid.N_BINS, dtype=np.complex64)),
        (rng.uniform(-0.99, 0.99, n), wide.astype(np.complex64)),
        (rng.uniform(-1e-4, 1e-4, n), board_like_gains()),
    ]


def _pairs(values: np.ndarray) -> np.ndarray:
    return np.stack([values.real, values.imag], axis=-1).astype(np.float32)


def balance_case(signal: np.ndarray, gains: np.ndarray) -> dict[str, np.ndarray]:
    """ch1 bins of each hop as the analyser gives them, the gains, and the bins after balance, as re, im pairs."""
    spectra = stft.analyze_signal(signal.astype(np.float32))
    return {"bins": _pairs(spectra), "gains": _pairs(gains), "output": _pairs(balance.apply(spectra, gains))}


def emit_balance(root: Path) -> list[Path]:
    """Four cases, then a negative control that multiplies by the conjugate of wide random gains."""
    rng = np.random.default_rng(SEED + 4)
    written = []
    for index, (signal, gains) in enumerate(_balance_inputs(rng)):
        path = root / "balance" / f"case_{index:03d}.gold"
        write_gold(path, balance_case(signal, gains))
        written.append(path)
    gains = np.exp(1j * rng.uniform(-np.pi, np.pi, grid.N_BINS)).astype(np.complex64)
    negative = balance_case(rng.uniform(-0.5, 0.5, BALANCE_HOPS * grid.HOP_SAMPLES), gains)
    spectra = negative["bins"][..., 0] + 1j * negative["bins"][..., 1]
    negative["output"] = _pairs(balance.apply(spectra, np.conj(gains)))
    path = root / "balance" / "case_neg_000.gold"
    write_gold(path, negative)
    written.append(path)
    return written


def speechlike(rng: np.random.Generator, n: int, peak: float) -> np.ndarray:
    """A voiced harmonic series with a moving pitch and syllable envelopes: speech enough for a VAD, and no voice."""
    t = np.arange(n) / grid.SAMPLE_RATE_HZ
    phase = 2 * np.pi * np.cumsum(120 + 40 * np.sin(2 * np.pi * 0.7 * t) + rng.uniform(0, 80)) / grid.SAMPLE_RATE_HZ
    voiced = sum(np.sin(k * phase) * np.exp(-k / 8) for k in range(1, 30))
    syllables = np.clip(np.sin(2 * np.pi * rng.uniform(3, 5) * t + rng.uniform(0, 6)), 0, None) ** 0.7
    x = voiced * syllables
    return peak * x / np.abs(x).max()


def _vad_inputs(rng: np.random.Generator) -> list[tuple[np.ndarray, int]]:
    """(int16 samples, aggressiveness) per case: speech over white noise, quiet speech over red noise, silence that
    is never modelled then loud bursts, noise whose level jumps."""
    n = VAD_HOPS * grid.HOP_SAMPLES
    red = np.cumsum(rng.standard_normal(n))
    red = red - np.convolve(red, np.ones(64) / 64, mode="same")
    bursts = np.zeros(n)
    bursts[n // 4 :] = speechlike(rng, n - n // 4, 0.9)
    jump = rng.standard_normal(n) * np.where(np.arange(n) < n // 2, 0.002, 0.05)
    cases = [
        (speechlike(rng, n, 0.3) + 0.01 * rng.standard_normal(n), 0),
        (speechlike(rng, n, 0.01) + 0.0005 * red / red.std(), 2),
        (bursts, 3),
        (jump, 1),
    ]
    pcm_min, pcm_max = np.iinfo(np.int16).min, np.iinfo(np.int16).max
    return [(np.clip(np.rint(x * 32768.0), pcm_min, pcm_max).astype(np.int16), a) for x, a in cases]


def vad_case(pcm: np.ndarray, aggressiveness: int, hangover_ms: int = afe.VAD_HANGOVER_MS) -> dict[str, np.ndarray]:
    """int16 hops, the configuration, and per hop the six band levels, the raw decision and speech after the
    hangover; each hop enters as int16 / 32768, exact in float32 on both sides."""
    hops = pcm.reshape(-1, grid.HOP_SAMPLES)
    detector = vad.Vad(aggressiveness, hangover_ms)
    out = [detector.process(h.astype(np.float32) / np.float32(32768.0)) for h in hops]
    return {
        "pcm": hops,
        "config": np.array([aggressiveness, hangover_ms], dtype=np.int32),
        "features": np.stack([o.features for o in out]).astype(np.float32),
        "raw": np.array([o.raw for o in out], dtype=np.uint8),
        "speech": np.array([o.speech for o in out], dtype=np.uint8),
    }


def emit_vad(root: Path) -> list[Path]:
    """Four cases, then a negative control whose speech flags come one hop late."""
    rng = np.random.default_rng(SEED + 5)
    written = []
    for index, (pcm, aggressiveness) in enumerate(_vad_inputs(rng)):
        path = root / "vad" / f"case_{index:03d}.gold"
        write_gold(path, vad_case(pcm, aggressiveness))
        written.append(path)
    n = VAD_HOPS * grid.HOP_SAMPLES
    noisy = speechlike(rng, n, 0.3) + 0.01 * rng.standard_normal(n)
    negative = vad_case(np.clip(np.rint(noisy * 32768.0), -32768, 32767).astype(np.int16), 0)
    negative["speech"] = np.concatenate([[0], negative["speech"][:-1]]).astype(np.uint8)
    path = root / "vad" / "case_neg_000.gold"
    write_gold(path, negative)
    written.append(path)
    return written


def _pcm(x: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(x * 32768.0), -32768, 32767).astype(np.int16)


def _agc_inputs(rng: np.random.Generator) -> list[tuple[np.ndarray, np.ndarray, float]]:
    """(int16 samples, speech flag per hop, target dBFS) per case: quiet speech the gain climbs to, then freezes on,
    then climbs again; loud speech with bursts to full scale the limiter must hold; noise under a -20 dBFS target."""
    long_n, n = AGC_LONG_HOPS * grid.HOP_SAMPLES, AGC_HOPS * grid.HOP_SAMPLES
    quiet_flags = np.ones(AGC_LONG_HOPS, dtype=np.uint8)
    quiet_flags[64:96] = 0
    loud = speechlike(rng, n, 0.6)
    for start in rng.integers(0, n - 64, 6):
        loud[start : start + 32] = rng.uniform(-1.0, 1.0, 32)
    noise_flags = (np.arange(AGC_HOPS) % 6 < 4).astype(np.uint8)
    return [
        (_pcm(speechlike(rng, long_n, 0.01) + 1e-4 * rng.standard_normal(long_n)), quiet_flags, afe.AGC_TARGET_DBFS),
        (_pcm(loud), np.ones(AGC_HOPS, dtype=np.uint8), afe.AGC_TARGET_DBFS),
        (_pcm(0.03 * rng.standard_normal(n)), noise_flags, -20.0),
    ]


def agc_case(
    pcm: np.ndarray, speech: np.ndarray, target_dbfs: float, heed_speech: bool = True
) -> dict[str, np.ndarray]:
    """int16 hops, their speech flags and the target; every output sample and the gain of each hop."""
    hops = pcm.reshape(-1, grid.HOP_SAMPLES)
    control = agc.Agc(agc.AgcConfig(target_dbfs=target_dbfs))
    outs, gains = [], []
    for hop, flag in zip(hops, speech, strict=True):
        out, gain_db = control.process(hop.astype(np.float32) / np.float32(32768.0), bool(flag) and heed_speech)
        outs.append(out)
        gains.append(gain_db)
    return {
        "pcm": hops,
        "speech": speech.astype(np.uint8),
        "config": np.array([target_dbfs], dtype=np.float32),
        "out": np.stack(outs).astype(np.float32),
        "gain_db": np.array(gains, dtype=np.float32),
    }


def emit_agc(root: Path) -> list[Path]:
    """Three cases, then a negative control computed as if every hop were silence, so the gain never moves."""
    rng = np.random.default_rng(SEED + 6)
    written = []
    for index, (pcm, speech, target) in enumerate(_agc_inputs(rng)):
        path = root / "agc" / f"case_{index:03d}.gold"
        write_gold(path, agc_case(pcm, speech, target))
        written.append(path)
    n = 32 * grid.HOP_SAMPLES
    negative = agc_case(
        _pcm(speechlike(rng, n, 0.01)), np.ones(32, dtype=np.uint8), afe.AGC_TARGET_DBFS, heed_speech=False
    )
    path = root / "agc" / "case_neg_000.gold"
    write_gold(path, negative)
    written.append(path)
    return written


def ns_power(x: np.ndarray) -> np.ndarray:
    """Power of every bin of every hop, as the facade hands the ns slot."""
    spectra = stft.analyze_signal(x.astype(np.float32))
    return (spectra.real * spectra.real + spectra.imag * spectra.imag).astype(np.float32)


def ns_omlsa_case(
    power: np.ndarray, floor_db: float, echo: np.ndarray | None = None, cfg: ns_omlsa.OmlsaConfig | None = None
) -> dict[str, np.ndarray]:
    """Power per hop, the gain floor, residual echo when the case has one; the gains and speech probability."""
    model = ns_omlsa.Omlsa(cfg)
    model.set_floor(floor_db)
    echoes = echo if echo is not None else [None] * len(power)
    outs = [model.process(p, e) for p, e in zip(power, echoes, strict=True)]
    case = {"power": power, "config": np.array([floor_db], dtype=np.float32)}
    if echo is not None:
        case["echo"] = echo
    case["gain"] = np.stack([o.gain for o in outs]).astype(np.float32)
    case["speech_prob"] = np.array([o.speech_prob for o in outs], dtype=np.float32)
    return case


def _ns_inputs(rng: np.random.Generator) -> list[tuple[np.ndarray, float, np.ndarray | None]]:
    """(power, floor dB, residual echo) per case: speech bursts over white noise; quiet speech over red noise at a -6 dB
    floor; noise rising 20 dB; digital silence then faint speech at a -18 dB floor; speech with residual echo."""
    n = NS_HOPS * grid.HOP_SAMPLES
    bursts = np.repeat((np.arange(NS_HOPS) // NS_BURST_HOPS) % 2 == 1, grid.HOP_SAMPLES)
    red = np.cumsum(rng.standard_normal(n))
    red = red - np.convolve(red, np.ones(64) / 64, mode="same")
    rise = 0.003 * rng.standard_normal(NS_RISE_HOPS * grid.HOP_SAMPLES)
    rise[NS_RISE_AT_HOP * grid.HOP_SAMPLES :] *= 10.0
    lead = NS_SILENT_LEAD_HOPS * grid.HOP_SAMPLES
    faint = (0.002 * rng.standard_normal(n) + speechlike(rng, n, 0.05) * bursts) * (np.arange(n) >= lead)
    m = NS_ECHO_HOPS * grid.HOP_SAMPLES
    residual = speechlike(rng, m, 0.03)
    near = speechlike(rng, m, 0.1) * bursts[:m] + 0.005 * rng.standard_normal(m)
    return [
        (ns_power(0.01 * rng.standard_normal(n) + speechlike(rng, n, 0.2) * bursts), -12.0, None),
        (ns_power(0.02 * red / red.std() + speechlike(rng, n, 0.05) * bursts), -6.0, None),
        (ns_power(rise), -12.0, None),
        (ns_power(faint), -18.0, None),
        (ns_power(near + residual), -12.0, ns_power(residual)),
    ]


def emit_ns_omlsa(root: Path) -> list[Path]:
    """Five cases, then a negative control computed with the paper's 8 ms smoothing left unsquared at the 16 ms hop."""
    rng = np.random.default_rng(SEED + 8)
    written = []
    for index, (power, floor_db, echo) in enumerate(_ns_inputs(rng)):
        path = root / "ns_omlsa" / f"case_{index:03d}.gold"
        write_gold(path, ns_omlsa_case(power, floor_db, echo))
        written.append(path)
    n = NS_HOPS * grid.HOP_SAMPLES
    bursts = np.repeat((np.arange(NS_HOPS) // NS_BURST_HOPS) % 2 == 1, grid.HOP_SAMPLES)
    power = ns_power(0.01 * rng.standard_normal(n) + speechlike(rng, n, 0.2) * bursts)
    unsquared = ns_omlsa.OmlsaConfig(hop_s=0.008)
    path = root / "ns_omlsa" / "case_neg_000.gold"
    write_gold(path, {**ns_omlsa_case(power, -12.0, cfg=unsquared), "config": np.array([-12.0], dtype=np.float32)})
    written.append(path)
    return written


def plane_wave(signal: np.ndarray, angle_deg: float) -> np.ndarray:
    """(n, 2) far-field copies of signal from angle_deg: ch0 lags ch1 by tau = d cos(theta) / c (array.yaml)."""
    n = len(signal)
    freqs = np.fft.rfftfreq(2 * n, 1.0 / grid.SAMPLE_RATE_HZ)
    spectrum = np.fft.rfft(signal, 2 * n)
    tau_s = array.SPACING_M * np.cos(np.radians(angle_deg)) / array.SPEED_OF_SOUND_M_S
    shifted = [np.fft.irfft(spectrum * np.exp(-2j * np.pi * freqs * lag), 2 * n)[:n] for lag in (tau_s / 2, -tau_s / 2)]
    return np.stack(shifted, axis=-1)


def on_pcm(x: np.ndarray) -> np.ndarray:
    """x on the int16 grid of the device's PCM, at the scale of ±1: numpy's float64 sin and exp run through SVML on
    AVX-512 CPUs and land a few ulps from libm, which never crosses half a step (KEHOACH 3.14)."""
    return _pcm(x) * LSB


def doa_case(pair: np.ndarray, update: np.ndarray, cfg: doa.DoaConfig) -> dict[str, np.ndarray]:
    """Both channels' bins per hop, the update flags and the configuration; the angle and confidence after each hop."""
    bins = [stft.analyze_signal(pair[:, m].astype(np.float32)) for m in range(array.N_MICS)]
    searcher = doa.Doa(cfg)
    out = [searcher.process(bins[0][h], bins[1][h], bool(update[h])) for h in range(len(update))]
    fields = (
        cfg.spacing_m,
        cfg.speed_of_sound_m_s,
        cfg.band_min_hz,
        cfg.band_max_hz,
        cfg.grid_step_deg,
        cfg.smooth_tau_s,
    )
    return {
        "config": np.array(fields, dtype=np.float32),
        "bins0": _pairs(bins[0]),
        "bins1": _pairs(bins[1]),
        "update": update.astype(np.uint8),
        "angle": np.array([o.angle_deg for o in out], dtype=np.int32),
        "confidence": np.array([o.confidence for o in out], dtype=np.uint8),
    }


def _doa_inputs(rng: np.random.Generator) -> list[tuple[np.ndarray, np.ndarray, doa.DoaConfig]]:
    """(two channels, update flags, configuration): noise from 30 deg searched every second hop; a voice moving from
    150 to 70 deg over the full band on a 3 deg grid; noise at 100 deg under a louder voice at 20 deg on a 4 deg grid,
    whose angles have no middle; silence, then noise from the ch1 end."""
    n = DOA_HOPS * grid.HOP_SAMPLES
    every = np.arange(DOA_HOPS)
    moving = np.concatenate(
        [plane_wave(speechlike(rng, n, 0.3), 150.0)[: n // 2], plane_wave(speechlike(rng, n, 0.3), 70.0)[n // 2 :]]
    )
    late = np.zeros((n, 2))
    late[DOA_SILENT_HOPS * grid.HOP_SAMPLES :] = plane_wave(rng.uniform(-0.3, 0.3, n), 0.0)[
        DOA_SILENT_HOPS * grid.HOP_SAMPLES :
    ]
    base = doa.DoaConfig()
    return [
        (
            on_pcm(plane_wave(rng.uniform(-0.3, 0.3, n), 30.0) + 0.01 * rng.standard_normal((n, 2))),
            (every >= 4) & (every % 2 == 0),
            base,
        ),
        (
            on_pcm(moving),
            np.ones(DOA_HOPS, dtype=bool),
            replace(base, band_min_hz=0.0, band_max_hz=8000.0, grid_step_deg=3.0),
        ),
        (
            on_pcm(plane_wave(rng.uniform(-0.1, 0.1, n), 100.0) + plane_wave(speechlike(rng, n, 0.4), 20.0)),
            every % 3 == 0,
            replace(base, band_min_hz=300.0, band_max_hz=3000.0, grid_step_deg=4.0, smooth_tau_s=0.1),
        ),
        (on_pcm(late), np.ones(DOA_HOPS, dtype=bool), base),
    ]


def emit_doa(root: Path) -> list[Path]:
    """Four cases, then a negative control whose angles sit one grid step off."""
    rng = np.random.default_rng(SEED + 9)
    written = []
    for index, (pair, update, cfg) in enumerate(_doa_inputs(rng)):
        path = root / "doa" / f"case_{index:03d}.gold"
        write_gold(path, doa_case(pair, update, cfg))
        written.append(path)
    n = DOA_HOPS * grid.HOP_SAMPLES
    negative = doa_case(
        on_pcm(plane_wave(rng.uniform(-0.3, 0.3, n), 60.0)), np.ones(DOA_HOPS, dtype=bool), doa.DoaConfig()
    )
    known = negative["angle"] != doa.ANGLE_UNKNOWN_DEG
    negative["angle"][known] += round(afe.DOA_GRID_STEP_DEG)
    path = root / "doa" / "case_neg_000.gold"
    write_gold(path, negative)
    written.append(path)
    return written


def gsc_case(pair: np.ndarray, angle: np.ndarray, adapt: np.ndarray, cfg: gsc.GscConfig) -> dict[str, np.ndarray]:
    """Both channels' bins, the steering angle and learning flag of each hop, the configuration; the output bins."""
    bins = [stft.analyze_signal(pair[:, m].astype(np.float32)) for m in range(array.N_MICS)]
    canceller = gsc.Gsc(cfg)
    out = np.stack(
        [canceller.process(bins[0][h], bins[1][h], float(angle[h]), bool(adapt[h])) for h in range(len(adapt))]
    )
    return {
        "config": np.array(
            [cfg.spacing_m, cfg.speed_of_sound_m_s, cfg.step_size, cfg.leakage, cfg.weight_max], dtype=np.float32
        ),
        "bins0": _pairs(bins[0]),
        "bins1": _pairs(bins[1]),
        "angle": angle.astype(np.float32),
        "adapt": adapt.astype(np.uint8),
        "out": _pairs(out),
    }


def _gsc_inputs(rng: np.random.Generator) -> list[tuple[np.ndarray, np.ndarray, np.ndarray, gsc.GscConfig]]:
    """(two channels, angle and learning flag per hop, configuration): a voice at 90 deg under noise from 30 deg, the
    weights learning while it pauses; a steer sweeping 60 to 120 deg under a 1.0 cap and a large leak; a voice at 150
    deg under noise from 20 deg with a large step; silence, then noise, learning throughout."""
    n = GSC_HOPS * grid.HOP_SAMPLES
    hops = np.arange(GSC_HOPS)
    voice = speechlike(rng, n, 0.3)
    pauses = (hops < GSC_LEARN_HOPS) | (hops % 4 == 0)
    late = np.zeros((n, 2))
    late[n // 2 :] = plane_wave(rng.uniform(-0.3, 0.3, n), 50.0)[n // 2 :]
    base = gsc.GscConfig()
    return [
        (plane_wave(voice, 90.0) + plane_wave(rng.uniform(-0.2, 0.2, n), 30.0), np.full(GSC_HOPS, 90.0), pauses, base),
        (
            plane_wave(rng.uniform(-0.2, 0.2, n), 45.0) + plane_wave(speechlike(rng, n, 0.2), 100.0),
            60.0 + 60.0 * hops / (GSC_HOPS - 1),
            np.ones(GSC_HOPS, dtype=bool),
            replace(base, weight_max=1.0, leakage=0.01),
        ),
        (
            plane_wave(speechlike(rng, n, 0.3), 150.0) + plane_wave(rng.uniform(-0.3, 0.3, n), 20.0),
            np.full(GSC_HOPS, 150.0),
            hops % 2 == 0,
            replace(base, step_size=0.2),
        ),
        (late, np.full(GSC_HOPS, 90.0), np.ones(GSC_HOPS, dtype=bool), base),
    ]


def emit_gsc(root: Path) -> list[Path]:
    """Four cases, then a negative control whose output comes from weights that never learn."""
    rng = np.random.default_rng(SEED + 10)
    written = []
    for index, (pair, angle, adapt, cfg) in enumerate(_gsc_inputs(rng)):
        path = root / "gsc" / f"case_{index:03d}.gold"
        write_gold(path, gsc_case(pair, angle, adapt, cfg))
        written.append(path)
    pair, angle, adapt, cfg = _gsc_inputs(rng)[0]
    negative = gsc_case(pair, angle, adapt, cfg)
    negative["out"] = gsc_case(pair, angle, np.zeros_like(adapt), cfg)["out"]
    path = root / "gsc" / "case_neg_000.gold"
    write_gold(path, negative)
    written.append(path)
    return written


def pitch_config() -> pitch.PitchConfig:
    """The pitch settings the models read, from the features config (KEHOACH 3.11)."""
    return pitch.PitchConfig(**listen.PITCH)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=GOLDEN_ROOT)
    args = parser.parse_args()
    emitted = emit_stft(args.out) + emit_mel(args.out) + emit_chain(args.out) + emit_chain_modules(args.out)
    emitted += emit_hpf(args.out) + emit_balance(args.out) + emit_vad(args.out) + emit_agc(args.out)
    emitted += emit_doa(args.out) + emit_gsc(args.out) + emit_pitch(args.out, pitch_config())
    for path in emitted + emit_ns_omlsa(args.out):
        print(path.relative_to(args.out) if path.is_relative_to(args.out) else path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
