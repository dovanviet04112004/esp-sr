"""gsc against answers known without a room, on bins whose channels differ by exactly the delay's phase: the steered
source passes untouched, one directional noise is cancelled wherever its optimal weight is under the cap and not
where it is over, a talker at the steer survives weights learnt on that noise, the weights never pass the cap."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from srpipe.dsp.afe import gsc
from srpipe.generated import array, grid

HOPS = 400
LEARNT = 300  # hops after which the weights have settled at mu 0.05
FREQS_HZ = np.arange(grid.N_BINS) * grid.SAMPLE_RATE_HZ / grid.FFT_SIZE


def phase(angle_deg: float) -> np.ndarray:
    """X1 / X0 of a far source at angle_deg: ch0 lags by tau = d cos(theta) / c (contracts/array.yaml)."""
    tau_s = array.SPACING_M * np.cos(np.radians(angle_deg)) / array.SPEED_OF_SOUND_M_S
    return np.exp(2j * np.pi * FREQS_HZ * tau_s)


def source(rng: np.random.Generator, angle_deg: float, hops: int = HOPS) -> list[np.ndarray]:
    x0 = rng.standard_normal((hops, grid.N_BINS)) + 1j * rng.standard_normal((hops, grid.N_BINS))
    return [x0.astype(np.complex64), (x0 * phase(angle_deg)).astype(np.complex64)]


def run(canceller: gsc.Gsc, x: list[np.ndarray], angle: float, adapt: bool) -> np.ndarray:
    return np.stack([canceller.process(x[0][h], x[1][h], angle, adapt) for h in range(len(x[0]))])


def optimal_weight(noise_deg: float, steer_deg: float) -> np.ndarray:
    """|W| that removes a far source from the beam: |F / B| once its ch1 is turned by the steer."""
    turned = phase(noise_deg) / phase(steer_deg)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.abs(0.5 * (1 + turned) / (1 - turned))


def test_a_source_on_the_steer_leaves_the_block_empty_and_passes_untouched() -> None:
    x = source(np.random.default_rng(0), 60.0, 64)
    y = run(gsc.Gsc(), x, 60.0, adapt=False)
    assert np.abs(y - x[0]).max() / np.abs(x[0]).max() < 1e-5


def test_a_directional_noise_is_cancelled_wherever_its_weight_is_under_the_cap() -> None:
    x = source(np.random.default_rng(1), 30.0)
    y = run(gsc.Gsc(), x, 90.0, adapt=True)
    plain = 0.5 * (x[0] + x[1])
    per_bin = np.sum(np.abs(y[LEARNT:]) ** 2, axis=0) / np.sum(np.abs(plain[LEARNT:]) ** 2, axis=0)
    needed = optimal_weight(30.0, 90.0)
    cap = gsc.GscConfig().weight_max
    assert 10 * np.log10(per_bin[needed < cap].max()) < -60.0
    assert 10 * np.log10(per_bin[(needed > 1.1 * cap) & (FREQS_HZ > 0)].min()) > -20.0


def test_a_talker_on_the_steer_survives_weights_learnt_on_noise() -> None:
    rng = np.random.default_rng(2)
    noise, talker = source(rng, 30.0), source(rng, 90.0)
    canceller = gsc.Gsc()
    run(canceller, noise, 90.0, adapt=True)
    out = np.stack([canceller.apply(talker[0][h], talker[1][h], 90.0) for h in range(HOPS)])
    assert np.abs(out - talker[0]).max() / np.abs(talker[0]).max() < 1e-4


def test_the_weights_never_pass_their_cap() -> None:
    canceller = gsc.Gsc(replace(gsc.GscConfig(), weight_max=0.5))
    run(canceller, source(np.random.default_rng(3), 80.0, 100), 90.0, adapt=True)
    assert np.sqrt(canceller.w_re**2 + canceller.w_im**2).max() <= 0.5 * (1 + 1e-6)


@pytest.mark.parametrize("field", ["step_size", "weight_max", "spacing_m"])
def test_refuses_what_the_firmware_refuses(field: str) -> None:
    with pytest.raises(ValueError):
        gsc.Gsc(replace(gsc.GscConfig(), **{field: 0.0}))
