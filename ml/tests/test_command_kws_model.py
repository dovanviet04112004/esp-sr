"""The kws DS-CNN: each size costs what the paper's table 7 gives on its own input (Zhang et al. 2017: 49 frames of
10 MFCC, 12 classes, an operation half a MAC), pads as TensorFlow's SAME, and maps a window of the configured shape
to one logit a class."""

from __future__ import annotations

import pytest

from srpipe.core.config import load_yaml
from srpipe.tasks import command
from srpipe.tasks.command import kws

torch = pytest.importorskip("torch")

from srpipe.tasks.command.kws.model import dscnn  # noqa: E402

PAPER_WINDOW, PAPER_CLASSES = (49, 10), 12
PAPER_MOPS = {"S": 5.4, "M": 19.8, "L": 56.9}


@pytest.mark.parametrize("size", sorted(PAPER_MOPS))
def test_each_size_costs_what_the_paper_gives(size: str) -> None:
    net = dscnn.build(load_yaml(kws.CONFIG), PAPER_WINDOW, PAPER_CLASSES, size)
    assert 2 * net.macs / 1e6 == pytest.approx(PAPER_MOPS[size], rel=0.03)


def test_same_padding_leaves_the_ceiling_of_size_over_stride() -> None:
    pads, size = dscnn.same_pads((94, 43), (10, 4), (2, 2))
    assert size == (47, 22)
    assert pads == (1, 2, 4, 4)
    assert dscnn.same_pads((47, 43), (3, 3), (1, 1)) == ((1, 1, 1, 1), (47, 43))


def test_a_window_gives_one_logit_a_class_at_every_size() -> None:
    cfg = load_yaml(kws.CONFIG)
    names = kws.classes(load_yaml(kws.CONFIG), load_yaml(command.CONFIG))
    assert names[-2:] == [kws.OTHER, kws.SILENCE]
    assert "chup_anh" not in names
    window = (cfg["window_hops"], kws.n_dims(cfg))
    assert window == (94, 43)
    torch.manual_seed(1)
    for size in cfg["model"]["sizes"]:
        net = dscnn.build(cfg, window, len(names), size).eval()
        with torch.no_grad():
            assert net(torch.randn(3, 1, *window)).shape == (3, len(names))


def test_the_probe_records_have_the_layout_the_board_reads() -> None:
    from srpipe.tasks.command.kws import quant

    assert quant.WINDOWS_HEAD.size == 12 and quant.RECORD.size == 28
