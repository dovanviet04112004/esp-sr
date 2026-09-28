"""Calls into ml/tts/<name>/run.py through `uv run`, one batch of requests per call; models cache under cache/hf/."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from srpipe.tts import PROJECTS


def run(name: str, *args: str, cache: Path) -> str:
    """Run ml/tts/<name>/run.py in that project's environment, not srpipe's; its stdout, its stderr passed through."""
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"} | {"HF_HOME": str(cache / "hf")}
    project = PROJECTS / name
    cmd = ["uv", "run", "--project", str(project), "python", str(project / "run.py"), *args]
    return subprocess.run(cmd, env=env, check=True, stdout=subprocess.PIPE, text=True).stdout


def engine_args(engine: str, tts: dict) -> list[str]:
    """What the engine's run.py takes ahead of its command: VieNeu two pinned repos and its dtype, F5 its pinned
    checkpoint and vocoder."""
    spec = tts["engines"][engine]
    if engine == "vieneu":
        return [spec["checkpoint"], spec["codec"], spec["dtype"]]
    return [spec["checkpoint"], spec["vocoder"]]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def presets(engine: str, tts: dict, cache: Path) -> list[dict]:
    """The engine's built-in voices as [{label, id}]."""
    return json.loads(run(engine, *engine_args(engine, tts), "voices", cache=cache))


def synthesise(engine: str, requests: list[dict], tts: dict, work: Path, cache: Path) -> None:
    """Write each request's clip to its out path; the keys a request carries are those of ml/tts/<engine>/run.py."""
    listing = work / f"{engine}_requests.jsonl"
    # One voice back to back: VieNeu enrolls a reference on the CPU and keeps only the last 32 (REF_CACHE_MAX).
    write_jsonl(listing, sorted(requests, key=lambda r: r.get("ref_audio") or r.get("voice") or ""))
    run(engine, *engine_args(engine, tts), str(listing), cache=cache)


def hear(clips: list[dict], tts: dict, work: Path, cache: Path) -> dict[str, dict]:
    """What the checker makes of every clip {id, wav, targets}, by id: {text, logp, targets}, the text it heard and the
    log-probability of that text and of each target given the clip. Answers are kept in cache/tts/heard.jsonl by the
    clip's bytes and the checker, its settings and run.py, so a clip is heard again only for a target it lacks."""
    store = cache / "tts" / "heard.jsonl"
    settings = json.dumps(tts["asr"], sort_keys=True).encode()
    checker_id = hashlib.sha256(settings + (PROJECTS / "asr" / "run.py").read_bytes()).hexdigest()
    known: dict[str, dict] = {}
    if store.exists():
        for row in map(json.loads, store.read_text(encoding="utf-8").splitlines()):
            if row["checker"] == checker_id:
                known[row["sha256"]] = row
    digest = {c["id"]: hashlib.sha256(Path(c["wav"]).read_bytes()).hexdigest() for c in clips}
    todo = [c for c in clips if not set(c["targets"]) <= known.get(digest[c["id"]], {}).get("targets", {}).keys()]
    if todo:
        listing, heard = work / "asr_clips.jsonl", work / "asr_heard.jsonl"
        write_jsonl(listing, todo)
        run("asr", tts["asr"]["model"], str(tts["asr"]["batch"]), str(listing), str(heard), cache=cache)
        answers = [json.loads(line) for line in heard.read_text(encoding="utf-8").splitlines()]
        rows = [{"checker": checker_id, "sha256": digest[c["id"]], **a} for c, a in zip(todo, answers, strict=True)]
        known |= {r["sha256"]: r for r in rows}
        store.parent.mkdir(parents=True, exist_ok=True)
        with store.open("a", encoding="utf-8") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    return {c["id"]: known[digest[c["id"]]] for c in clips}
