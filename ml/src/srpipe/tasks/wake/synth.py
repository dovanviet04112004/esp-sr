"""Wake positives (E11-T7, KEHOACH 1.2, 3.11): the texts, voices and seeds of configs/models/wake.yaml, synthesised and
heard back through srpipe.tts.

Run: python -m srpipe.tasks.wake.synth pilot  (writes interim/wake/synth_pilot/ and prints the engine table)
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import yaml

from srpipe.core.config import data_paths, load_yaml
from srpipe.tasks.wake import CONFIG
from srpipe.tts import CONFIG as TTS_CONFIG
from srpipe.tts import clips, engines


def pilot_requests(cfg: dict, presets: list[dict], refs: list[clips.Reference], out: Path) -> dict[str, list[dict]]:
    """Per engine, the clips of the pilot: VieNeu's presets and clones, F5's clones over seeds; every text must spell
    the wake word."""
    synth = cfg["synth"]
    for engine in ("vieneu", "f5"):
        for text in synth[engine]["texts"]:
            if clips.spelled(text) != clips.spelled(cfg["word"]):
                raise ValueError(f"{engine} text {text!r} is not the wake word {cfg['word']!r}")
    requests: dict[str, list[dict]] = {"vieneu": [], "f5": []}
    for n, text in enumerate(synth["vieneu"]["texts"]):
        for k, voice in enumerate(presets):
            vid = f"preset_{k:02d}_t{n}"
            requests["vieneu"].append(
                {
                    "id": vid,
                    "speaker": voice["label"],
                    "text": text,
                    "voice": voice["id"],
                    "out": str(out / "vieneu" / f"{vid}.wav"),
                }
            )
        for ref in refs:
            vid = f"clone_{ref.speaker}_t{n}"
            requests["vieneu"].append(
                {
                    "id": vid,
                    "speaker": ref.speaker,
                    "text": text,
                    "ref_audio": str(ref.wav),
                    "out": str(out / "vieneu" / f"{vid}.wav"),
                }
            )
    f5 = synth["f5"]
    for n, text in enumerate(f5["texts"]):
        for ref in refs:
            for seed in f5["seeds"]:
                vid = f"clone_{ref.speaker}_t{n}_s{seed}"
                requests["f5"].append(
                    {
                        "id": vid,
                        "speaker": ref.speaker,
                        "text": text,
                        "ref_audio": str(ref.wav),
                        "ref_text": ref.text,
                        "seed": seed,
                        "speed": f5["speed"],
                        "out": str(out / "f5" / f"{vid}.wav"),
                    }
                )
    return requests


def pilot(cfg: dict, tts: dict, raw_root: Path, out: Path, cache: Path) -> Path:
    """Synthesise the pilot with every engine, hear it back, and write out/manifest.yaml; returns the manifest."""
    spec = cfg["synth"]["pilot"]
    refs = clips.references(raw_root / spec["references"], spec["speakers"], spec["ref_seconds"])
    requests = pilot_requests(cfg, engines.presets("vieneu", tts, cache), refs, out)
    rows = clips.render(requests, tts, out / "work", cache)
    manifest = out / "manifest.yaml"
    body = {
        "word": cfg["word"],
        "synth": cfg["synth"],
        "tts": tts,
        "references": [str(r.wav) for r in refs],
        "clips": rows,
    }
    manifest.write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return manifest


def table(manifest: Path) -> list[str]:
    """Pass counts per engine, voice kind and text, as markdown rows."""
    clip_rows = yaml.safe_load(manifest.read_text(encoding="utf-8"))["clips"]
    groups: dict[tuple[str, str, str], list[bool]] = defaultdict(list)
    for c in clip_rows:
        groups[(c["engine"], c["id"].split("_")[0], c["text"])].append(c["passed"])
    rows = ["| Bộ | Giọng | Chữ đưa vào | Qua PhoWhisper |", "|---|---|---|---|"]
    for (engine, kind, text), passed in sorted(groups.items()):
        rows.append(f"| {engine} | {kind} | {text} | {sum(passed)}/{len(passed)} |")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pilot", help="compare the engines on a few voices; writes interim/wake/synth_pilot/")
    parser.parse_args(argv)
    paths = data_paths()
    out = paths["interim"] / "wake" / "synth_pilot"
    manifest = pilot(load_yaml(CONFIG), load_yaml(TTS_CONFIG), paths["raw"], out, paths["cache"])
    print("\n".join(table(manifest)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
