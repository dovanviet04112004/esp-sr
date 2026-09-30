"""Wake word clips from the desktop TTS engines (E11-T7, KEHOACH 1.2, 3.11), heard back through srpipe.tts.

pilot compares the engines on a few VIVOS voices (make eval-tts); positives reads the wake word in every preset voice
and in voices cloned from training material; negatives reads its near misses (ADR-0007) and hard its near-miss families
(KEHOACH 3.11) in voices of the same pool; select keeps the clips the checker's margins allow. Each set writes
interim/wake/synth_<set>/manifest.yaml. Run: python -m srpipe.tasks.wake.synth {pilot,positives,negatives,hard,select}
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
import yaml

from srpipe.core import corpus, phrases, screen
from srpipe.core.config import data_paths, load_yaml
from srpipe.tasks.wake import CONFIG
from srpipe.tts import CONFIG as TTS_CONFIG
from srpipe.tts import clips, engines

SETS = {"pilot": "synth_pilot", "positives": "synth_pos", "negatives": "synth_neg", "hard": "synth_hard"}
SETS_KEPT = ("positives", "negatives", "hard")


def positive_requests(cfg: dict, spec: dict, presets: list[dict], refs: list[clips.Reference], out: Path) -> dict:
    """VieNeu's presets and clones over their own seeds, F5's clones over seeds and speeds; every text must spell the
    wake word, which every clip must say."""
    for engine in ("vieneu", "f5"):
        for text in spec[engine]["texts"]:
            if clips.spelled(text) != clips.spelled(cfg["word"]):
                raise ValueError(f"{engine} text {text!r} is not the wake word {cfg['word']!r}")
    requests: dict[str, list[dict]] = {"vieneu": [], "f5": []}
    vieneu, f5 = spec["vieneu"], spec["f5"]
    for n, text in enumerate(vieneu["texts"]):
        for seed in vieneu["preset_seeds"]:
            requests["vieneu"] += [
                clips.preset_request(k, v, n, text, seed, out / "vieneu") for k, v in enumerate(presets)
            ]
        for seed in vieneu["clone_seeds"]:
            requests["vieneu"] += [clips.clone_request("vieneu", r, n, text, seed, 1.0, out / "vieneu") for r in refs]
    for n, text in enumerate(f5["texts"]):
        for ref in refs:
            for seed in f5["seeds"]:
                requests["f5"] += [clips.clone_request("f5", ref, n, text, seed, v, out / "f5") for v in f5["speeds"]]
    return {engine: [r | {"say": cfg["word"]} for r in reqs] for engine, reqs in requests.items()}


def negative_texts(cfg: dict, stream: np.ndarray, vocab: list[str], codes: np.ndarray, tables: dict) -> list[str]:
    """The corpus neighbours of the wake word, commonest first, then its commonest openings, then the hand-picked
    phrases, without repeats and without any that sounds like the wake word, however it is spelled."""
    spec, word = cfg["synth"]["negatives"], corpus.sounds(cfg["word"])
    near = phrases.neighbours(cfg["word"], spec["misses"], stream, vocab, codes, tables)
    opening = [p for p, _ in phrases.openings(cfg["word"], stream, vocab).most_common()]
    opening = [p for p in opening if not corpus.says(corpus.sounds(p), word)][: spec["openings"]]
    texts = dict.fromkeys([*(p for p, _ in near.most_common()), *opening, *spec["phrases"]])
    return [t for t in texts if not corpus.says(corpus.sounds(t), word)]


def negative_requests(
    cfg: dict,
    texts: list[str],
    presets: list[dict],
    refs: list[clips.Reference],
    rng: np.random.Generator,
    out: Path,
    counts: dict[str, int],
) -> dict:
    """Each text read by counts of VieNeu voices drawn from presets and clones, and of F5 clones, at seed 0 and speed
    1; the wake word is each clip's rival."""
    requests: dict[str, list[dict]] = {"vieneu": [], "f5": []}
    pool = len(presets) + len(refs)
    for n, text in enumerate(texts):
        for i in sorted(rng.choice(pool, counts["vieneu"], replace=False)):
            requests["vieneu"].append(
                clips.preset_request(i, presets[i], n, text, 0, out / "vieneu")
                if i < len(presets)
                else clips.clone_request("vieneu", refs[i - len(presets)], n, text, 0, 1.0, out / "vieneu")
            )
        for i in sorted(rng.choice(len(refs), counts["f5"], replace=False)):
            requests["f5"].append(clips.clone_request("f5", refs[i], n, text, 0, 1.0, out / "f5"))
    return {engine: [r | {"rivals": [cfg["word"]]} for r in reqs] for engine, reqs in requests.items()}


def commonest_syllables(stream: np.ndarray, vocab: list[str], codes: np.ndarray, tables: dict, onsets, count: int):
    """The count commonest corpus syllables whose onset is one of onsets, or of any onset when onsets is None."""
    onset_of = {code: name for name, code in tables["onset"].items()}
    counts = np.bincount(stream[stream >= 0], minlength=len(vocab))
    picked = []
    for i in np.argsort(-counts, kind="stable"):
        if codes[i, 0] >= 0 and (onsets is None or onset_of[codes[i, 0]] in onsets):
            picked.append(vocab[i])
        if len(picked) == count:
            break
    return picked


def hard_texts(cfg: dict, stream: np.ndarray, vocab: list[str], codes: np.ndarray, tables: dict) -> list[str]:
    """Every family of the hard config filled with its syllables, without repeats or any text that holds the wake
    word or a held-out phrase."""
    spec, word = cfg["synth"]["hard"], cfg["word"]
    fills = {
        "near": commonest_syllables(stream, vocab, codes, tables, set(spec["onsets"]), spec["near_syllables"]),
        "any": commonest_syllables(stream, vocab, codes, tables, None, spec["any_syllables"]),
    }
    held = [corpus.words(t) for t in [word, *spec["held_out"]]]
    texts = [f["text"].format(x=x) for f in spec["families"] for x in fills[f["fill"]]]
    return [t for t in dict.fromkeys(texts) if not any(corpus.says(corpus.words(t), h) for h in held)]


def training_references(cfg: dict, raw: Path, interim: Path, rejected: set[str]) -> list[clips.Reference]:
    """Every VIVOS train speaker and the parquet draws of the config, among the clips screening kept (clips.py)."""
    spec = cfg["synth"]
    return clips.training_references(
        spec["references"], spec["ref_seconds"], spec["seed"], raw, interim / clips.REFERENCES, rejected, None
    )


def threshold(negatives: list[dict], word: str, false_accept: float) -> float:
    """The margin to the wake word under which only false_accept of the negatives the checker heard right fall."""
    return float(np.quantile([c["rivals"][word] for c in negatives if c["passed"]], false_accept))


def select(cfg: dict, interim: Path) -> float:
    """Mark kept on every set made: a positive heard as the wake word or within the threshold of it, a negative or hard
    near miss heard right and beyond it, and none when an earlier kept clip has the same bytes; the threshold, set on
    the negatives and returned, goes into every manifest."""
    word, false_accept = cfg["word"], cfg["synth"]["false_accept"]
    made = [s for s in SETS_KEPT if (interim / SETS[s] / "manifest.yaml").exists()]
    body = {s: yaml.safe_load((interim / SETS[s] / "manifest.yaml").read_text(encoding="utf-8")) for s in made}
    margin = threshold(body["negatives"]["clips"], word, false_accept)
    for c in body["positives"]["clips"]:
        c["kept"] = c["passed"] or c["margin"] <= margin
    for s in made[1:]:
        for c in body[s]["clips"]:
            c["kept"] = c["passed"] and c["rivals"][word] > margin
    seen: set[str] = set()
    for s in made:
        for c in body[s]["clips"]:
            c["kept"] = c["kept"] and c["sha256"] not in seen
            seen |= {c["sha256"]} if c["kept"] else set()
        body[s]["threshold"] = {"false_accept": false_accept, "margin": round(margin, 3)}
        clips.write_manifest(interim / SETS[s], body[s])
    return margin


def table(manifest: Path, by_text: bool) -> list[str]:
    """Clips heard as their own text, and kept once selected, per engine and voice kind, and per text when by_text."""
    groups: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for c in yaml.safe_load(manifest.read_text(encoding="utf-8"))["clips"]:
        key = (c["engine"], c["id"].split("_")[0], c["text"]) if by_text else (c["engine"], c["id"].split("_")[0])
        groups[key].append(c)
    kept = all("kept" in c for g in groups.values() for c in g)
    head = [
        *(["Bộ", "Giọng", "Chữ đưa vào"] if by_text else ["Bộ", "Giọng"]),
        "Nghe đúng chữ",
        *(["Giữ"] if kept else []),
    ]
    rows = [f"| {' | '.join(head)} |", "|" + "---|" * len(head)]
    for key, g in sorted(groups.items()):
        counts = [
            f"{sum(c['passed'] for c in g)}/{len(g)}",
            *([f"{sum(c['kept'] for c in g)}/{len(g)}"] if kept else []),
        ]
        rows.append(f"| {' | '.join([*key, *counts])} |")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("set", choices=[*SETS, "select"])
    which = parser.parse_args(argv).set
    cfg, tts, paths = load_yaml(CONFIG), load_yaml(TTS_CONFIG), data_paths()
    if which == "hard" and "hard" not in cfg["synth"]:
        parser.error("synth.hard is off in wake.yaml: no hard near-miss families to read (KEHOACH 3.11)")
    interim, cache = paths["interim"] / "wake", paths["cache"]
    if which == "select":
        print(f"margin threshold {select(cfg, interim):.3f}")
        for s in SETS_KEPT:
            if (interim / SETS[s] / "manifest.yaml").exists():
                print("\n".join(table(interim / SETS[s] / "manifest.yaml", by_text=False)))
        return 0
    out = interim / SETS[which]
    rejected = set(screen.rejected(paths["interim"]))
    presets = engines.presets("vieneu", tts, cache)
    body = {"word": cfg["word"], "synth": cfg["synth"], "tts": tts}
    if which == "pilot":
        spec = cfg["synth"]["pilot"]
        corpus = paths["raw"] / cfg["synth"]["references"]["vivos"]
        refs = clips.speaker_references(corpus, spec["speakers"], cfg["synth"]["ref_seconds"], paths["raw"], rejected)
        requests = positive_requests(cfg, spec, presets, refs, out)
    else:
        refs = training_references(cfg, paths["raw"], interim, rejected)
        if which == "positives":
            requests = positive_requests(cfg, cfg["synth"]["positives"], presets, refs, out)
        elif which == "hard":
            stream, vocab = phrases.token_stream(paths["raw"] / "speech", cache / phrases.CACHE)
            texts = hard_texts(cfg, stream, vocab, *phrases.component_codes(vocab))
            rng = np.random.default_rng(cfg["synth"]["seed"])
            requests = negative_requests(cfg, texts, presets, refs, rng, out, cfg["synth"]["hard"]["voices"])
            body["texts"] = texts
        else:
            stream, vocab = phrases.token_stream(paths["raw"] / "speech", cache / phrases.CACHE)
            texts = negative_texts(cfg, stream, vocab, *phrases.component_codes(vocab))
            rng = np.random.default_rng(cfg["synth"]["seed"])
            requests = negative_requests(cfg, texts, presets, refs, rng, out, cfg["synth"]["negatives"]["voices"])
            body["texts"] = texts
    body |= {"references": [str(r.wav) for r in refs], "clips": clips.render(requests, tts, out / "work", cache)}
    print("\n".join(table(clips.write_manifest(out, body), by_text=which == "pilot")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
