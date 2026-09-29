"""Calls into ml/tts/<name>/run.py through `uv run`, one batch of requests per call, models cached under cache/hf/;
and into the forced aligner's Docker image, models cached under cache/mfa/."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
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


def align(clips: list[dict], tts: dict, work: Path, cache: Path) -> dict[str, list[dict]]:
    """Every word of each clip {id, wav, text} by forced alignment with tts['align'], by id: [{word, start, end}],
    times in seconds; a clip the aligner cannot align is left out. The text is normalised syllables. Runs in Docker, as
    the aligner needs Kaldi; one folder per set of clips and settings, so a rerun of the same set reads its answers."""
    spec = tts["align"]
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True).encode())
    for c in clips:
        digest.update(json.dumps([c["id"], c["text"]]).encode() + Path(c["wav"]).read_bytes())
    folder = work / f"align_{digest.hexdigest()[:16]}"
    for c in clips:
        speaker = folder / "corpus" / c["id"]
        speaker.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(c["wav"], speaker / f"{c['id']}.wav")
        (speaker / f"{c['id']}.lab").write_text(c["text"], encoding="utf-8")
    models = cache / "mfa" / spec["version"]
    kinds = {"acoustic": f"{spec['acoustic']}.zip", "dictionary": f"{spec['dictionary']}.dict"}
    steps = [
        f"mfa model download {kind} {spec[kind]} --version {spec['version']}"
        for kind, name in kinds.items()
        if not (models / "pretrained_models" / kind / name).exists()
    ]
    steps.append(
        f"mfa align /data/corpus {spec['acoustic']} {spec['dictionary']} /data/aligned --output_format json"
        f" -j {spec['jobs']} --use_mp --clean"
    )
    models.mkdir(parents=True, exist_ok=True)
    mounts = ["-v", f"{models}:/mfa", "-v", f"{folder}:/data", "-e", "MFA_ROOT_DIR=/mfa", "-e", "HOME=/mfa"]
    docker = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", *mounts, spec["image"]]
    subprocess.run([*docker, "bash", "-c", " && ".join(steps)], check=True)
    times = {}
    for c in clips:
        answer = folder / "aligned" / c["id"] / f"{c['id']}.json"
        if answer.exists():
            entries = json.loads(answer.read_text(encoding="utf-8"))["tiers"]["words"]["entries"]
            times[c["id"]] = [{"word": word, "start": start, "end": end} for start, end, word in entries]
    return times


def hear(clips: list[dict], tts: dict, work: Path, cache: Path) -> dict[str, dict]:
    """What the checker makes of every clip {id, wav, targets}, by id: {text, logp, targets}, the text it heard and the
    log-probability of that text and of each target given the clip. Answers are kept in cache/tts/heard.jsonl by the
    clip's bytes and the checker, its settings and run.py: clips with the same bytes are heard once, for the targets of
    them all, and a clip is heard again only for a target it lacks."""
    store = cache / "tts" / "heard.jsonl"
    settings = json.dumps(tts["asr"], sort_keys=True).encode()
    checker_id = hashlib.sha256(settings + (PROJECTS / "asr" / "run.py").read_bytes()).hexdigest()
    known: dict[str, dict] = {}

    def merged(row: dict) -> dict:
        return row | {"targets": known.get(row["sha256"], {}).get("targets", {}) | row["targets"]}

    if store.exists():
        for row in map(json.loads, store.read_text(encoding="utf-8").splitlines()):
            if row["checker"] == checker_id:
                known[row["sha256"]] = merged(row)
    digest = {c["id"]: hashlib.sha256(Path(c["wav"]).read_bytes()).hexdigest() for c in clips}
    todo: dict[str, dict] = {}
    for c in clips:
        sha = digest[c["id"]]
        lacking = [t for t in c["targets"] if t not in known.get(sha, {}).get("targets", {})]
        if lacking:
            ask = todo.setdefault(sha, {"id": sha, "wav": c["wav"], "targets": []})
            ask["targets"] += [t for t in lacking if t not in ask["targets"]]
    if todo:
        listing, heard = work / "asr_clips.jsonl", work / "asr_heard.jsonl"
        write_jsonl(listing, list(todo.values()))
        run("asr", tts["asr"]["model"], str(tts["asr"]["batch"]), str(listing), str(heard), cache=cache)
        answers = [json.loads(line) for line in heard.read_text(encoding="utf-8").splitlines()]
        rows = [merged({"checker": checker_id, "sha256": a["id"], **a}) for a in answers]
        known |= {r["sha256"]: r for r in rows}
        store.parent.mkdir(parents=True, exist_ok=True)
        with store.open("a", encoding="utf-8") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    return {c["id"]: known[digest[c["id"]]] for c in clips}


def fast_model(tts: dict, cache: Path) -> Path:
    """The CTranslate2 checker of tts['asr_fast'] under cache/ct2/, converted from its pinned Whisper the first time."""
    spec = tts["asr_fast"]
    repo, revision = spec["model"].split("@")
    folder = cache / "ct2" / f"{repo.split('/')[-1]}-{revision[:8]}-{spec['quantization']}"
    if not (folder / "model.bin").exists():
        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"} | {"HF_HOME": str(cache / "hf")}
        convert = PROJECTS / "asr_ct2" / "convert.py"
        cmd = ["uv", "run", "--project", str(PROJECTS / "asr"), "--with", spec["converter"], "python", str(convert)]
        subprocess.run([*cmd, spec["model"], spec["quantization"], str(folder)], env=env, check=True)
    return folder


def hear_text(clips: list[dict], tts: dict, work: Path, cache: Path) -> dict[str, str]:
    """The text the fast checker hears in every clip {id, wav}, by id. Answers are kept in cache/tts/heard_fast.jsonl
    by the clip's bytes and the checker, so a clip with the same bytes is heard once."""
    store = cache / "tts" / "heard_fast.jsonl"
    checker_id = hashlib.sha256(
        json.dumps(tts["asr_fast"], sort_keys=True).encode() + (PROJECTS / "asr_ct2" / "run.py").read_bytes()
    ).hexdigest()
    known = {}
    if store.exists():
        for row in map(json.loads, store.read_text(encoding="utf-8").splitlines()):
            if row["checker"] == checker_id:
                known[row["sha256"]] = row["text"]
    digest = {c["id"]: hashlib.sha256(Path(c["wav"]).read_bytes()).hexdigest() for c in clips}
    todo = {digest[c["id"]]: c["wav"] for c in clips if digest[c["id"]] not in known}
    if todo:
        listing, heard = work / "fast_clips.jsonl", work / "fast_heard.jsonl"
        write_jsonl(listing, [{"id": sha, "wav": wav} for sha, wav in todo.items()])
        folder = fast_model(tts, cache)
        run("asr_ct2", str(folder), str(tts["asr_fast"]["batch"]), str(listing), str(heard), cache=cache)
        answers = {r["id"]: r["text"] for r in map(json.loads, heard.read_text(encoding="utf-8").splitlines())}
        known |= answers
        store.parent.mkdir(parents=True, exist_ok=True)
        with store.open("a", encoding="utf-8") as f:
            f.writelines(
                json.dumps({"checker": checker_id, "sha256": sha, "text": text}, ensure_ascii=False) + "\n"
                for sha, text in answers.items()
            )
    return {c["id"]: known[digest[c["id"]]] for c in clips}
