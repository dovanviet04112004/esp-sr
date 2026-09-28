"""Wake word clips from the desktop TTS engines (E11-T7, KEHOACH 1.2, 3.11), heard back through srpipe.tts.

pilot compares the engines on a few VIVOS voices (make eval-tts); positives reads the wake word in every preset voice
and in voices cloned from training material; negatives reads its near misses (ADR-0007) in voices of the same pool.
Each writes interim/wake/synth_<set>/manifest.yaml; a rerun makes only the missing clips.
Run: python -m srpipe.tasks.wake.synth {pilot,positives,negatives}
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
import yaml

from srpipe.core.config import data_paths, load_yaml
from srpipe.tasks.wake import CONFIG, candidates
from srpipe.tts import CONFIG as TTS_CONFIG
from srpipe.tts import clips, engines

SETS = {"pilot": "synth_pilot", "positives": "synth_pos", "negatives": "synth_neg"}
REFERENCES = "synth_refs"


def preset_request(k: int, voice: dict, n: int, text: str, seed: int, folder: Path) -> dict:
    rid = f"preset_{k:02d}_t{n}_s{seed}"
    return {
        "id": rid,
        "speaker": voice["label"],
        "text": text,
        "seed": seed,
        "voice": voice["id"],
        "out": str(folder / f"{rid}.wav"),
    }


def clone_request(engine: str, ref: clips.Reference, n: int, text: str, seed: int, speed: float, folder: Path) -> dict:
    rid = f"clone_{ref.speaker}_t{n}_s{seed}" + (f"_v{round(speed * 100)}" if engine == "f5" else "")
    req = {
        "id": rid,
        "speaker": ref.speaker,
        "text": text,
        "seed": seed,
        "ref_audio": str(ref.wav),
        "out": str(folder / f"{rid}.wav"),
    }
    return req | {"ref_text": ref.text, "speed": speed} if engine == "f5" else req


def positive_requests(cfg: dict, spec: dict, presets: list[dict], refs: list[clips.Reference], out: Path) -> dict:
    """VieNeu's presets over every seed and its clones over the first, F5's clones over seeds and speeds; every text
    must spell the wake word."""
    for engine in ("vieneu", "f5"):
        for text in spec[engine]["texts"]:
            if clips.spelled(text) != clips.spelled(cfg["word"]):
                raise ValueError(f"{engine} text {text!r} is not the wake word {cfg['word']!r}")
    requests: dict[str, list[dict]] = {"vieneu": [], "f5": []}
    vieneu, f5 = spec["vieneu"], spec["f5"]
    for n, text in enumerate(vieneu["texts"]):
        for seed in vieneu["seeds"]:
            requests["vieneu"] += [preset_request(k, v, n, text, seed, out / "vieneu") for k, v in enumerate(presets)]
        first = vieneu["seeds"][0]
        requests["vieneu"] += [clone_request("vieneu", r, n, text, first, 1.0, out / "vieneu") for r in refs]
    for n, text in enumerate(f5["texts"]):
        for ref in refs:
            for seed in f5["seeds"]:
                requests["f5"] += [clone_request("f5", ref, n, text, seed, v, out / "f5") for v in f5["speeds"]]
    return requests


def negative_texts(cfg: dict, stream: np.ndarray, vocab: list[str], codes: np.ndarray, tables: dict) -> list[str]:
    """The corpus neighbours of the wake word, commonest first, then its commonest openings, then the hand-picked
    phrases, without repeats; none of them spells the wake word."""
    spec, word = cfg["synth"]["negatives"], cfg["word"]
    near = candidates.neighbours(word, spec["misses"], stream, vocab, codes, tables)
    opening = candidates.openings(word, stream, vocab).most_common(spec["openings"])
    texts = list(dict.fromkeys([*(p for p, _ in near.most_common()), *(p for p, _ in opening), *spec["phrases"]]))
    if any(clips.spelled(t) == clips.spelled(word) for t in texts):
        raise ValueError(f"a negative spells the wake word {word!r}")
    return texts


def negative_requests(
    spec: dict, texts: list[str], presets: list[dict], refs: list[clips.Reference], rng: np.random.Generator, out: Path
) -> dict:
    """Each text read by spec['voices'] VieNeu voices drawn from presets and clones, and F5 clones, at seed 0 and
    speed 1."""
    counts = spec["voices"]
    requests: dict[str, list[dict]] = {"vieneu": [], "f5": []}
    pool = len(presets) + len(refs)
    for n, text in enumerate(texts):
        for i in sorted(rng.choice(pool, counts["vieneu"], replace=False)):
            requests["vieneu"].append(
                preset_request(i, presets[i], n, text, 0, out / "vieneu")
                if i < len(presets)
                else clone_request("vieneu", refs[i - len(presets)], n, text, 0, 1.0, out / "vieneu")
            )
        for i in sorted(rng.choice(len(refs), counts["f5"], replace=False)):
            requests["f5"].append(clone_request("f5", refs[i], n, text, 0, 1.0, out / "f5"))
    return requests


def training_references(cfg: dict, raw: Path, interim: Path) -> list[clips.Reference]:
    """Every VIVOS train speaker and the parquet draws of the config, drawn once and read back from references.yaml."""
    listing = interim / REFERENCES / "references.yaml"
    if listing.exists():
        rows = yaml.safe_load(listing.read_text(encoding="utf-8"))
        return [clips.Reference(r["speaker"], Path(r["wav"]), r["text"]) for r in rows]
    spec, seconds = cfg["synth"]["references"], cfg["synth"]["ref_seconds"]
    rng = np.random.default_rng(cfg["synth"]["seed"])
    refs = clips.speaker_references(raw / spec["vivos"], None, seconds)
    for name, count in spec["parquet"]["counts"].items():
        files = sorted((raw / "speech" / name).glob(spec["parquet"]["files"]))
        refs += clips.parquet_references(name, files, count, seconds, rng, interim / REFERENCES)
    rows = [{"speaker": r.speaker, "wav": str(r.wav), "text": r.text} for r in refs]
    listing.write_text(yaml.safe_dump(rows, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return refs


def write_manifest(out: Path, body: dict) -> Path:
    manifest = out / "manifest.yaml"
    manifest.write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return manifest


def table(manifest: Path, by_text: bool) -> list[str]:
    """Pass counts per engine and voice kind, and per text when by_text, as markdown rows."""
    groups: dict[tuple[str, ...], list[bool]] = defaultdict(list)
    for c in yaml.safe_load(manifest.read_text(encoding="utf-8"))["clips"]:
        key = (c["engine"], c["id"].split("_")[0], c["text"]) if by_text else (c["engine"], c["id"].split("_")[0])
        groups[key].append(c["passed"])
    head = ["Bộ", "Giọng", "Chữ đưa vào"] if by_text else ["Bộ", "Giọng"]
    rows = [f"| {' | '.join(head)} | Qua PhoWhisper |", "|" + "---|" * (len(head) + 1)]
    rows += [f"| {' | '.join(key)} | {sum(p)}/{len(p)} |" for key, p in sorted(groups.items())]
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("set", choices=list(SETS))
    which = parser.parse_args(argv).set
    cfg, tts, paths = load_yaml(CONFIG), load_yaml(TTS_CONFIG), data_paths()
    interim, cache = paths["interim"] / "wake", paths["cache"]
    out = interim / SETS[which]
    presets = engines.presets("vieneu", tts, cache)
    body = {"word": cfg["word"], "synth": cfg["synth"], "tts": tts}
    if which == "pilot":
        spec = cfg["synth"]["pilot"]
        refs = clips.speaker_references(
            paths["raw"] / cfg["synth"]["references"]["vivos"], spec["speakers"], cfg["synth"]["ref_seconds"]
        )
        requests = positive_requests(cfg, spec, presets, refs, out)
    else:
        refs = training_references(cfg, paths["raw"], interim)
        if which == "positives":
            requests = positive_requests(cfg, cfg["synth"]["positives"], presets, refs, out)
        else:
            stream, vocab = candidates.token_stream(paths["raw"] / "speech", cache / "wake_candidates")
            texts = negative_texts(cfg, stream, vocab, *candidates.component_codes(vocab))
            rng = np.random.default_rng(cfg["synth"]["seed"])
            requests = negative_requests(cfg["synth"]["negatives"], texts, presets, refs, rng, out)
            body["texts"] = texts
    body |= {"references": [str(r.wav) for r in refs], "clips": clips.render(requests, tts, out / "work", cache)}
    print("\n".join(table(write_manifest(out, body), by_text=which == "pilot")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
