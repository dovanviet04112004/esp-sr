"""Calls into ml/tts/<name>/run.py through `uv run`, one batch of requests per call; models cache under cache/hf/."""

from __future__ import annotations

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
    log-probability of that text and of each target given the clip."""
    listing, heard = work / "asr_clips.jsonl", work / "asr_heard.jsonl"
    write_jsonl(listing, clips)
    run("asr", tts["asr"]["model"], str(tts["asr"]["batch"]), str(listing), str(heard), cache=cache)
    return {r["id"]: r for r in map(json.loads, heard.read_text(encoding="utf-8").splitlines())}
