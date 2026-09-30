"""The reference engines of KEHOACH 3.16 on the PC, each a pinned uv project ml/afe_ref/<name>/ run through its run.py
as srpipe.tts runs the TTS engines, since their dependencies clash with srpipe's. Weights come to cache/afe_ref/<name>/
from the pinned URL in compare.yaml's refs and must match its sha256. dnsmos scores clips, kept by the clip's bytes in
cache/afe_ref/dnsmos/scores.jsonl so a clip is scored once; nsnet2 and rnnoise clean clips into new files."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.parse
from pathlib import Path

import requests

from srpipe.core.config import ML_ROOT

PROJECTS = ML_ROOT / "afe_ref"
CHUNK_BYTES = 1 << 20
TIMEOUT_S = 120


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def weights(name: str, spec: dict, cache: Path) -> Path:
    """The engine's weights under cache/afe_ref/<name>/, fetched from spec's URL when absent; refuse a file whose
    sha256 is not spec's."""
    path = cache / "afe_ref" / name / Path(urllib.parse.urlsplit(spec["url"]).path).name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(path.suffix + ".part")
        with requests.get(spec["url"], stream=True, timeout=TIMEOUT_S) as r:
            r.raise_for_status()
            with part.open("wb") as f:
                for chunk in r.iter_content(CHUNK_BYTES):
                    f.write(chunk)
        part.rename(path)
    if sha256_of(path) != spec["sha256"]:
        raise ValueError(f"{path}: sha256 {sha256_of(path)}, the pin is {spec['sha256']}")
    return path


def run(name: str, *args: str) -> None:
    """ml/afe_ref/<name>/run.py in that project's environment, not srpipe's."""
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    project = PROJECTS / name
    subprocess.run(
        ["uv", "run", "--project", str(project), "python", str(project / "run.py"), *args], env=env, check=True
    )


def dnsmos(clips: dict[str, Path], spec: dict, cache: Path, work: Path) -> dict[str, dict[str, float]]:
    """DNSMOS P.835 SIG, BAK and OVRL of every clip by id; a clip whose bytes were scored before is not run again."""
    store = cache / "afe_ref" / "dnsmos" / "scores.jsonl"
    model = weights("dnsmos", spec, cache)
    pin = spec["sha256"]
    known = {}
    if store.exists():
        for row in map(json.loads, store.read_text(encoding="utf-8").splitlines()):
            if row["model"] == pin:
                known[row["sha256"]] = {k: row[k] for k in ("sig", "bak", "ovrl")}
    digest = {cid: sha256_of(wav) for cid, wav in clips.items()}
    todo = {digest[cid]: wav for cid, wav in clips.items() if digest[cid] not in known}
    if todo:
        work.mkdir(parents=True, exist_ok=True)
        listing, out = work / "dnsmos_clips.jsonl", work / "dnsmos_scores.jsonl"
        listing.write_text("".join(json.dumps({"id": s, "wav": str(w)}) + "\n" for s, w in todo.items()), "utf-8")
        run("dnsmos", str(model), str(listing), str(out))
        rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        known |= {r["id"]: {k: r[k] for k in ("sig", "bak", "ovrl")} for r in rows}
        store.parent.mkdir(parents=True, exist_ok=True)
        with store.open("a", encoding="utf-8") as f:
            f.writelines(json.dumps({"model": pin, "sha256": r["id"], **r}) + "\n" for r in rows)
    return {cid: known[digest[cid]] for cid in clips}


def enhance(name: str, jobs: list[tuple[Path, Path]], spec: dict | None, cache: Path, work: Path) -> None:
    """Clean every (in, out) pair of mono wavs with the engine name, its weights from spec when it takes any."""
    missing = [str(src) for src, _ in jobs if not src.exists()]
    if missing:
        raise FileNotFoundError(f"{name}: {len(missing)} inputs missing, e.g. {missing[0]}")
    work.mkdir(parents=True, exist_ok=True)
    listing = work / f"{name}_jobs.jsonl"
    listing.write_text("".join(json.dumps({"in": str(a), "out": str(b)}) + "\n" for a, b in jobs), "utf-8")
    run(name, *([str(weights(name, spec, cache))] if spec else []), str(listing))
