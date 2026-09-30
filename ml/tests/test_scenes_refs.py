"""Reference engines: DNSMOS scores a clip's bytes once, keeps the scores across runs, and refuses weights whose sha256
is not the pin; a cleaning engine gets every pair in one run, its weights only when it takes any."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from srpipe.scenes import refs


def test_dnsmos_scores_each_clip_once_and_refuses_other_weights(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    model = cache / "afe_ref" / "dnsmos" / "m.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model")
    spec = {"url": "https://example.org/x/m.onnx", "sha256": hashlib.sha256(b"model").hexdigest()}
    calls = []

    def fake_run(name: str, weights: str, listing: str, out: str) -> None:
        rows = [json.loads(line) for line in Path(listing).read_text().splitlines()]
        calls.append(len(rows))
        body = "".join(json.dumps({"id": r["id"], "sig": 1.0, "bak": 2.0, "ovrl": 3.0}) + "\n" for r in rows)
        Path(out).write_text(body)

    monkeypatch.setattr(refs, "run", fake_run)
    same = [tmp_path / f"{n}.wav" for n in "ab"]
    for path in same:
        path.write_bytes(b"one clip")
    got = refs.dnsmos({"a": same[0], "b": same[1]}, spec, cache, tmp_path / "work")
    assert calls == [1] and got["a"] == got["b"] == {"sig": 1.0, "bak": 2.0, "ovrl": 3.0}
    refs.dnsmos({"a": same[0]}, spec, cache, tmp_path / "work")
    assert calls == [1]
    with pytest.raises(ValueError, match="sha256"):
        refs.dnsmos({"a": same[0]}, spec | {"sha256": "0" * 64}, cache, tmp_path / "work")


def test_enhance_lists_every_pair_once_and_passes_weights_only_when_pinned(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    model = cache / "afe_ref" / "nsnet2" / "n.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"net")
    spec = {"url": "https://example.org/x/n.onnx", "sha256": hashlib.sha256(b"net").hexdigest()}
    calls = []
    monkeypatch.setattr(refs, "run", lambda name, *args: calls.append((name, args)))
    pairs = [(tmp_path / f"{n}.wav", tmp_path / f"{n}_out.wav") for n in "ab"]
    for src, _ in pairs:
        src.write_bytes(b"clip")
    refs.enhance("nsnet2", pairs, spec, cache, tmp_path / "work")
    refs.enhance("rnnoise", pairs, None, cache, tmp_path / "work")
    assert [(name, len(args)) for name, args in calls] == [("nsnet2", 2), ("rnnoise", 1)]
    assert calls[0][1][0] == str(model)
    listing = [json.loads(line) for line in Path(calls[1][1][0]).read_text().splitlines()]
    assert listing == [{"in": str(a), "out": str(b)} for a, b in pairs]
    with pytest.raises(FileNotFoundError, match="1 inputs missing"):
        refs.enhance("rnnoise", [*pairs, (tmp_path / "gone.wav", tmp_path / "x.wav")], None, cache, tmp_path / "w")
