"""Check srpipe.dsp.afe.chain: with every module off against what the plain chain must do, and with the product's
modules against what each of them must add."""

from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from srpipe.dsp.afe import chain
from srpipe.dsp.emit_golden import speechlike
from srpipe.generated import afe, array, grid

HOPS = 32
AMPLITUDE = 12000.0
TONE_HZ = 440.0
MAX_ERROR_LSB = 1
PLAIN = chain.ChainConfig(modules=())


def tone(n_samples: int) -> np.ndarray:
    t = np.arange(n_samples) / grid.SAMPLE_RATE_HZ
    return np.rint(AMPLITUDE * np.sin(2 * np.pi * TONE_HZ * t)).astype(np.int16)


def interleave(*channels: np.ndarray) -> np.ndarray:
    return np.stack(channels, axis=-1).reshape(-1, grid.HOP_SAMPLES * len(channels))


def run(ch: chain.Chain, hops: np.ndarray) -> list[chain.Frame]:
    return [ch.process(h) for h in hops]


def test_identical_microphones_come_back_one_hop_late() -> None:
    x = tone(HOPS * grid.HOP_SAMPLES)
    frames = run(chain.Chain("MM", PLAIN), interleave(x, x))
    out = np.concatenate([f.pcm for f in frames])
    assert np.max(np.abs(out[grid.HOP_SAMPLES :].astype(int) - x[: -grid.HOP_SAMPLES])) <= MAX_ERROR_LSB
    assert [f.seq for f in frames] == list(range(HOPS))
    assert all(f.flags == 0 and f.doa_deg == -1 and f.vad == 0 and f.gain_db == 0 for f in frames)


def test_opposite_microphones_cancel_in_the_mean() -> None:
    x = tone(HOPS * grid.HOP_SAMPLES)
    frames = run(chain.Chain("MM", PLAIN), interleave(x, -x))
    assert max(int(np.max(np.abs(f.pcm.astype(int)))) for f in frames) <= MAX_ERROR_LSB


def test_reset_marks_only_the_next_hop_and_seq_runs_on() -> None:
    ch = chain.Chain("MM", PLAIN)
    silence = np.zeros(grid.HOP_SAMPLES * array.N_MICS, dtype=np.int16)
    first = ch.process(silence)
    ch.reset()
    after, later = ch.process(silence), ch.process(silence)
    assert (first.flags, after.flags, later.flags) == (0, chain.FLAG_GAP, 0)
    assert (after.seq, later.seq) == (1, 2)


def test_full_scale_on_either_microphone_flags_a_clip() -> None:
    ch = chain.Chain("MM", PLAIN)
    hop = np.zeros(grid.HOP_SAMPLES * array.N_MICS, dtype=np.int16)
    hop[1] = np.iinfo(np.int16).min
    assert ch.process(hop).flags == chain.FLAG_CLIPPED
    assert ch.process(np.zeros_like(hop)).flags == 0


def test_level_is_int8_dbfs_with_silence_at_the_floor() -> None:
    assert chain.level_dbfs(np.zeros(grid.HOP_SAMPLES, dtype=np.float32)) == chain.LEVEL_MIN_DBFS
    assert chain.level_dbfs(np.full(grid.HOP_SAMPLES, 0.1, dtype=np.float32)) == -20
    assert chain.level_dbfs(np.full(grid.HOP_SAMPLES, 1.0, dtype=np.float32)) == 0


def test_to_pcm_rounds_half_to_even_and_saturates() -> None:
    hop = np.array([0.5, 1.5, -2.5, 40000.0, -40000.0], dtype=np.float32) / chain.PCM_FULL_SCALE
    assert chain.to_pcm(hop).tolist() == [0, 2, -2, 32767, -32768]


def test_formats_the_default_build_cannot_run_are_refused() -> None:
    with pytest.raises(ValueError):
        chain.Chain("XY")
    with pytest.raises(NotImplementedError):
        chain.Chain("MMR")


def test_the_default_modules_are_the_products() -> None:
    assert chain.Chain().cfg.modules == afe.MODULES


def test_a_module_without_a_python_reference_is_refused() -> None:
    with pytest.raises(NotImplementedError):
        chain.Chain("MM", chain.ChainConfig(modules=("hpf", "gsc")))


def test_balance_gains_of_minus_one_cancel_identical_microphones() -> None:
    x = tone(HOPS * grid.HOP_SAMPLES)
    cfg = chain.ChainConfig(modules=("balance",), balance_gains=-np.ones(grid.N_BINS, dtype=np.complex64))
    frames = run(chain.Chain("MM", cfg), interleave(x, x))
    assert all(not f.pcm.any() for f in frames)


def test_without_calib_balance_leaves_the_mean() -> None:
    x = tone(HOPS * grid.HOP_SAMPLES)
    plain = run(chain.Chain("MM", PLAIN), interleave(x, x // 2))
    uncalibrated = run(chain.Chain("MM", chain.ChainConfig(modules=("balance",))), interleave(x, x // 2))
    assert all(np.array_equal(a.pcm, b.pcm) for a, b in zip(plain, uncalibrated, strict=True))


def test_speech_raises_vad_and_the_agc_gain() -> None:
    rng = np.random.default_rng(3)
    x = np.rint(32768 * speechlike(rng, 4 * HOPS * grid.HOP_SAMPLES, 0.01)).astype(np.int16)
    frames = run(chain.Chain(), interleave(x, x))
    assert sum(f.vad for f in frames) > len(frames) // 2
    assert frames[-1].gain_db > frames[0].gain_db == 0


def test_reset_starts_every_module_again() -> None:
    rng = np.random.default_rng(4)
    x = np.rint(32768 * speechlike(rng, 2 * HOPS * grid.HOP_SAMPLES, 0.01)).astype(np.int16)
    hops = interleave(x, x)
    ch = chain.Chain()
    run(ch, hops[:HOPS])
    ch.reset()
    again = run(ch, hops[HOPS:])
    fresh = run(chain.Chain(), hops[HOPS:])
    assert all(np.array_equal(a.pcm, b.pcm) for a, b in zip(again, fresh, strict=True))
    assert [(a.vad, a.gain_db, a.level_dbfs) for a in again] == [(b.vad, b.gain_db, b.level_dbfs) for b in fresh]


def test_resume_keeps_every_estimate_and_clears_only_what_holds_samples() -> None:
    rng = np.random.default_rng(5)
    x = np.rint(32768 * speechlike(rng, 2 * HOPS * grid.HOP_SAMPLES, 0.01)).astype(np.int16)
    hops = interleave(x, x)
    ch = chain.Chain()
    run(ch, hops[:HOPS])
    agc, vad = ch._agc, ch._vad
    kept = (agc.gain, agc.speech_power, vad.hops_modelled, [m.copy() for m in vad.models], ch._ns.lambda_d.copy())
    ch.resume()
    assert (agc.gain, agc.speech_power, vad.hops_modelled) == kept[:3]
    assert all(np.array_equal(a, b) for a, b in zip(vad.models, kept[3], strict=True))
    assert np.array_equal(ch._ns.lambda_d, kept[4])
    assert not agc.delay.any() and not ch._hpf.state.any()
    after = run(ch, hops[HOPS:])
    fresh = run(chain.Chain(), hops[HOPS:])
    assert after[0].flags == chain.FLAG_GAP and after[1].flags == 0
    assert kept[0] != np.float32(1.0), "the speech before the gap never moved agc, so nothing shows it kept"
    assert any(a.gain_db != b.gain_db for a, b in zip(after, fresh, strict=True))


def test_gsc_steered_at_broadside_passes_identical_microphones_as_the_mean() -> None:
    x = tone(HOPS * grid.HOP_SAMPLES)
    hops = interleave(x, x)
    plain = run(chain.Chain("MM", PLAIN), hops)
    steered = run(chain.Chain("MM", chain.ChainConfig(modules=(), spatial="gsc")), hops)
    assert all(np.array_equal(a.pcm, b.pcm) for a, b in zip(plain, steered, strict=True))


def test_gsc_learns_to_cancel_what_only_one_microphone_hears() -> None:
    rng = np.random.default_rng(5)
    noise = np.rint(rng.normal(0.0, 3000.0, 8 * HOPS * grid.HOP_SAMPLES)).astype(np.int16)
    hops = interleave(noise, np.zeros_like(noise))
    plain = run(chain.Chain("MM", PLAIN), hops)
    steered = run(chain.Chain("MM", chain.ChainConfig(modules=(), spatial="gsc")), hops)
    tail = slice(-HOPS, None)
    power = [np.mean(np.stack([f.pcm for f in frames[tail]]).astype(np.float64) ** 2) for frames in (plain, steered)]
    assert power[1] < 0.5 * power[0]
    with pytest.raises(NotImplementedError):
        chain.Chain("MM", chain.ChainConfig(spatial="beam"))


def test_render_writes_every_whole_hop_of_a_recording(tmp_path) -> None:
    x = tone(HOPS * grid.HOP_SAMPLES + 100)
    sf.write(tmp_path / "in.wav", np.stack([x, x], axis=-1), grid.SAMPLE_RATE_HZ, subtype="PCM_16")
    chain.main([str(tmp_path / "in.wav"), str(tmp_path / "out.wav"), "--spatial", "gsc"])
    info = sf.info(str(tmp_path / "out.wav"))
    assert (info.channels, info.frames) == (1, HOPS * grid.HOP_SAMPLES)
