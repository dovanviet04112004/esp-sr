"""Reference engines: DNSMOS scores a clip's bytes once, keeps the scores across runs, and refuses weights whose sha256
is not the pin."""

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
