"""Wake's chip scoring: the permille table of the int8 logits and the integer smoothing ai_engine mirrors."""

from __future__ import annotations

import numpy as np

from srpipe.tasks.wake.postproc.smooth import chip_scores, permille_table, smooth


def test_the_table_rises_through_half_at_zero_to_its_ends() -> None:
    table = permille_table(-4)
    assert table.dtype == np.uint16 and len(table) == 256
    assert np.all(np.diff(table.astype(int)) >= 0)
    assert table[128] == 500 and table[0] == 0 and table[255] == 1000
    assert not np.array_equal(table, permille_table(-3))


def test_integer_smoothing_follows_the_float_one_to_a_permille() -> None:
    rng = np.random.default_rng(7)
    logits = rng.integers(-128, 128, size=400)
    chip = chip_scores(logits, -4, 5).astype(int)
    probs = 1.0 / (1.0 + np.exp(-logits * 2.0**-4))
    assert np.abs(chip - np.round(1000 * smooth(probs, 5))).max() <= 1
    assert chip_scores(logits, -4, 3).tolist() != chip.tolist()


def test_the_first_hops_average_what_exists_rounding_half_up() -> None:
    assert chip_scores(np.array([127, -128, -128]), -4, 5).tolist() == [1000, 500, 333]
    assert chip_scores(np.array([127, -128, 127, 127]), -4, 2).tolist() == [1000, 500, 500, 1000]
