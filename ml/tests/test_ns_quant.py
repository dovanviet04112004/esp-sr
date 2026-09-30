"""srpipe.tasks.ns.quant: every candidate's step graph quantises with each GRU state in and out at one exponent and
writes a record of every hop; the simulation carries its states only when they are fed back."""

from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import pytest

from srpipe.compress.quant import ptq_espdl
from srpipe.core.config import load_yaml
from srpipe.tasks import ns
from srpipe.tasks.ns import model, quant
from srpipe.tasks.ns.postproc import bands

pytest.importorskip("esp_ppq")

HOPS = 12


def tiny_config() -> dict:
    cfg = copy.deepcopy(load_yaml(ns.CONFIG))
    cfg["probe"] |= {"calib_hops": 8, "hops": HOPS}
    return cfg


def nsnet_hidden(cfg: dict, name: str) -> int:
    return cfg["nsnet"]["sizes"][name.removeprefix(model.NSNET).upper()]["hidden"]


def test_every_candidate_writes_a_record_of_every_hop(tmp_path: Path) -> None:
    cfg = tiny_config()
    _, streams = quant.probe(cfg, tmp_path / "out", tmp_path / "work")
    data = streams.read_bytes()
    magic, count = quant.STREAMS_HEAD.unpack_from(data)
    assert magic == quant.STREAMS_MAGIC and count == len(model.names(cfg))
    rnnoise = (bands.n_features(cfg["rnnoise"]), len(cfg["rnnoise"]["bands_hz"]) + 1, len(cfg["rnnoise"]["gru"]))
    nsnet = (cfg["nsnet"]["bins"], cfg["nsnet"]["bins"], cfg["nsnet"]["layers"])
    at = quant.STREAMS_HEAD.size
    for name in model.names(cfg):
        entry, hops, dims, outputs, states, values, _, _ = quant.RECORD.unpack_from(data, at)
        assert entry.rstrip(b"\0").decode() == quant.ENTRIES[name] and hops == HOPS
        assert (dims, outputs, states) == (rnnoise if name == model.RNNOISE else nsnet)
        assert values == (
            sum(cfg["rnnoise"]["gru"].values()) if name == model.RNNOISE else nsnet[2] * nsnet_hidden(cfg, name)
        )
        at += quant.RECORD.size + -(-hops * (dims + outputs + values) // 4) * 4
    assert at == len(data)


def test_the_simulation_carries_its_states_only_when_they_are_fed_back(tmp_path: Path) -> None:
    graph, inputs, outputs, sizes, dims = quant.step_graph(model.RNNOISE, tiny_config(), tmp_path)
    assert [p.exponent for p in inputs[1:]] == [p.exponent for p in outputs[1:]]
    x = ptq_espdl.to_int8(np.random.default_rng(3).standard_normal((HOPS, dims)), inputs[0].exponent)
    fed, fed_states = quant.stream(graph, x, inputs, outputs, sizes, feed=True)
    cold, _ = quant.stream(graph, x, inputs, outputs, sizes, feed=False)
    assert np.array_equal(fed[0], cold[0]) and not np.array_equal(fed[1:], cold[1:])
    assert fed_states.shape == (HOPS, sum(sizes))
