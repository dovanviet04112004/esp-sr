"""session writes raw/device/<board>/<session>/ and one manifest row as KEHOACH 4.4.1 lays them out."""

from __future__ import annotations

import csv
import hashlib
import json
import threading
from pathlib import Path

import pytest

from conftest import frame
from srhost import session
from srhost.config import RecordConfig
from srhost.generated import grid
from srhost.stream_rx import StreamServer

CMD = session.Labels(
    kind="cmd", room="lab", fw="0.1.0+87b0337", pcm_shift=16, spk="spk_001", consent="C001", prompt="bật đèn"
)


def record(tmp_path: Path, board_factory, labels: session.Labels, seqs: list[int]) -> Path:
    cfg = RecordConfig(data_root=tmp_path / "data", board="board_b", stream_bind="127.0.0.1", stream_port=1)
    cfg.data_root.mkdir(exist_ok=True)
    server = StreamServer("127.0.0.1", 0)
    board_factory(server.port, [[frame(s) for s in seqs]])
    stop = threading.Event()
    timer = threading.Timer(10.0, stop.set)
    timer.start()
    try:
        duration_s = len(seqs) * grid.HOP_SAMPLES / grid.SAMPLE_RATE_HZ
        return session.record(labels, cfg, duration_s, stop, server, tmp_path / "board_b.csv", day="20260926")
    finally:
        timer.cancel()


def test_a_session_lands_in_raw_with_its_labels_and_a_manifest_row(tmp_path: Path, board_factory) -> None:
    out = record(tmp_path, board_factory, CMD, [0, 1, 2, 5, 6])
    assert out == tmp_path / "data" / "raw" / "device" / "board_b" / "20260926_lab_001"
    assert sorted(p.name for p in out.iterdir()) == ["ch0.wav", "ch1.wav", "gaps.txt", "ref.wav", "session.json"]
    meta = json.loads((out / "session.json").read_text(encoding="utf-8"))
    assert meta["grid_hash"] == "0x9c914b61" and meta["seq_gaps"] == 1 and meta["prompt"] == "bật đèn"
    assert set(meta) == set(session.MANIFEST_COLUMNS) - {"duration_s", "sha256"}
    rows = list(csv.DictReader((tmp_path / "board_b.csv").open(encoding="utf-8")))
    assert len(rows) == 1 and rows[0]["session"] == "20260926_lab_001"
    assert rows[0]["sha256"] == hashlib.sha256((out / "ch0.wav").read_bytes()).hexdigest()
    assert float(rows[0]["duration_s"]) == pytest.approx(5 * grid.HOP_SAMPLES / grid.SAMPLE_RATE_HZ)


def test_the_next_session_of_the_day_takes_the_next_number(tmp_path: Path, board_factory) -> None:
    record(tmp_path, board_factory, CMD, [0, 1])
    second = record(tmp_path, board_factory, CMD, [0, 1])
    assert second.name == "20260926_lab_002"
    assert len(list(csv.DictReader((tmp_path / "board_b.csv").open(encoding="utf-8")))) == 2


def test_a_noise_session_has_no_speaker(tmp_path: Path, board_factory) -> None:
    noise = session.Labels(kind="noise", room="lab", fw="0.1.0+87b0337", pcm_shift=16)
    meta = json.loads((record(tmp_path, board_factory, noise, [0]) / "session.json").read_text(encoding="utf-8"))
    assert meta["spk"] is None and meta["consent"] is None
    row = next(csv.DictReader((tmp_path / "board_b.csv").open(encoding="utf-8")))
    assert row["spk"] == "" and row["consent"] == ""


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"spk": None}, "needs --spk and --consent"),
        ({"spk": "Nguyen Van A"}, "not spk_NNN"),
        ({"kind": "noise"}, "no speaker"),
        ({"room": "lab_2"}, "names the session"),
        ({"pcm_shift": 20}, "outside 8..16"),
        ({"doa_deg": 181}, "outside 0..180"),
        ({"kind": "chat"}, "not one of"),
    ],
)
def test_labels_that_break_a_rule_are_refused(changes: dict, message: str) -> None:
    labels = session.Labels(**{**CMD.__dict__, **changes})
    with pytest.raises(session.LabelError, match=message):
        labels.check()


def test_session_numbers_skip_to_after_the_highest_taken(tmp_path: Path) -> None:
    for name in ("20260926_lab_001", "20260926_lab_004", "20260926_hall_009", "20260925_lab_007"):
        (tmp_path / name).mkdir()
    assert session.next_session_name(tmp_path, "20260926", "lab") == "20260926_lab_005"
