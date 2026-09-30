"""Command clips from the desktop TTS engines for both command tracks (E11-T7, KEHOACH 1.2, 3.12), heard back.

positives reads every learned command of contracts/commands/default_vi.json, never an unseen one; negatives reads
the corpus near misses of each command, the hand-picked phrases and every shorter run of a command's words said alone,
with every command as each clip's rival; pilot does a few of both; select keeps what the checker's margins allow.
Run: python -m srpipe.tasks.command.synth {pilot,positives,negatives,select}
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import yaml

from srpipe.core import corpus, phrases, screen
from srpipe.core.config import data_paths, load_yaml
from srpipe.tasks.command import CONFIG, learned
from srpipe.tts import CONFIG as TTS_CONFIG
from srpipe.tts import clips, engines

SETS = {"pilot": "synth_pilot", "positives": "synth_pos", "negatives": "synth_neg"}
SETS_KEPT = ("positives", "negatives")
HELD_ROLES = ("val", "test")
KINDS = ("near", "opening", "half", "phrase")


def forms(text: str, templates: list[str]) -> list[str]:
    """text in each template: {text} as written, {Text} with its first letter capitalised."""
    return [t.format(text=text, Text=text[:1].upper() + text[1:]) for t in templates]


def held_speakers(split: Path) -> set[str]:
    """Every speaker the command split gives to a held-out role; their voices must not reach train (KEHOACH 1.3)."""
    held: set[str] = set()
    for role in HELD_ROLES:
        for listing in sorted(split.glob(f"{role}*.txt")):
            held |= {line.split("\t")[1] for line in listing.read_text(encoding="utf-8").splitlines() if "\t" in line}
    return held


def clone_speakers(corpus_root: Path, split: Path) -> list[str]:
    """The VIVOS train speakers the command split gives to train."""
    held = held_speakers(split)
    return sorted(d.name for d in (corpus_root / "waves").iterdir() if d.is_dir() and d.name not in held)


def positive_requests(
    cfg: dict, commands: list[dict], spec: dict, presets: list[dict], refs: list[clips.Reference], out: Path
) -> dict:
    """Each command in each form of its engine: VieNeu over presets and clones and their seeds, F5 over clones, seeds
    and speeds; each clip must say its command."""
    templates = cfg["synth"]["forms"]
    for command in commands:
        for text in forms(command["text"], templates):
            if clips.spelled(text) != clips.spelled(command["text"]):
                raise ValueError(f"form {text!r} does not spell the command {command['text']!r}")
    requests: dict[str, list[dict]] = {"vieneu": [], "f5": []}
    for c, command in enumerate(commands):
        for engine, s in spec.items():
            for f in s["forms"]:
                n = c * len(templates) + f
                text = forms(command["text"], templates)[f]
                if engine == "vieneu":
                    for seed in s["preset_seeds"]:
                        requests[engine] += [
                            clips.preset_request(k, v, n, text, seed, out / engine) for k, v in enumerate(presets)
                        ]
                    for seed in s["clone_seeds"]:
                        requests[engine] += [
                            clips.clone_request(engine, r, n, text, seed, 1.0, out / engine) for r in refs
                        ]
                else:
                    for ref in refs:
                        for seed in s["seeds"]:
                            requests[engine] += [
                                clips.clone_request(engine, ref, n, text, seed, v, out / engine) for v in s["speeds"]
                            ]
    by_text = {t: c["text"] for c in commands for t in forms(c["text"], templates)}
    return {engine: [r | {"say": by_text[r["text"]]} for r in reqs] for engine, reqs in requests.items()}


def halves(text: str) -> list[str]:
    """Every run of consecutive words of text shorter than text, longest first."""
    w = corpus.words(text)
    return [" ".join(w[i : i + n]) for n in range(len(w) - 1, 0, -1) for i in range(len(w) - n + 1)]


def negative_texts(
    cfg: dict, commands: list[dict], stream: np.ndarray, vocab: list[str], codes: np.ndarray, tables: dict
) -> list[dict]:
    """Per command its commonest corpus neighbours and openings, then every half of every command, then the
    hand-picked phrases, without repeats and without any that sounds like a whole command however it is spelled:
    [{text, kind}]."""
    spec = cfg["synth"]["negatives"]
    found: dict[str, str] = {}
    for command in commands:
        near = phrases.neighbours(command["text"], spec["misses"], stream, vocab, codes, tables)
        found |= {p: "near" for p, _ in near.most_common(spec["neighbours"]) if p not in found}
        opening = phrases.openings(command["text"], stream, vocab)
        found |= {p: "opening" for p, _ in opening.most_common(spec["openings"]) if p not in found}
    for command in commands:
        found |= {h: "half" for h in halves(command["text"]) if h not in found}
    found |= {p: "phrase" for p in spec["phrases"] if p not in found}
    whole = [corpus.sounds(c["text"]) for c in commands]
    return [{"text": t, "kind": k} for t, k in found.items() if corpus.sounds(t) not in whole]


def negative_requests(
    commands: list[dict],
    texts: list[dict],
    presets: list[dict],
    refs: list[clips.Reference],
    rng: np.random.Generator,
    out: Path,
    counts: dict[str, int],
    first: int = 0,
) -> dict:
    """Each text read by counts of VieNeu voices drawn from presets and clones, and of F5 clones, at seed 0 and speed
    1; every command is each clip's rival. Text numbers start at first, so a set holding positives too keeps ids
    apart."""
    requests: dict[str, list[dict]] = {"vieneu": [], "f5": []}
    pool = len(presets) + len(refs)
    for n, entry in enumerate(texts, start=first):
        text = entry["text"]
        for i in sorted(rng.choice(pool, counts["vieneu"], replace=False)):
            requests["vieneu"].append(
                clips.preset_request(i, presets[i], n, text, 0, out / "vieneu")
                if i < len(presets)
                else clips.clone_request("vieneu", refs[i - len(presets)], n, text, 0, 1.0, out / "vieneu")
            )
        for i in sorted(rng.choice(len(refs), counts["f5"], replace=False)):
            requests["f5"].append(clips.clone_request("f5", refs[i], n, text, 0, 1.0, out / "f5"))
    rivals = [c["text"] for c in commands]
    return {engine: [r | {"rivals": rivals} for r in reqs] for engine, reqs in requests.items()}


def nearest(clip: dict) -> float:
    """How much more the checker believes what it heard than the command it most nearly heard."""
    return min(clip["rivals"].values())


def threshold(negatives: list[dict], false_accept: float) -> float:
    """The margin to the nearest command under which only false_accept of the negatives heard right fall."""
    return float(np.quantile([nearest(c) for c in negatives if c["passed"]], false_accept))


def select(cfg: dict, interim: Path) -> float:
    """Mark kept on every set made: a positive heard as its command or within the threshold of it, a negative heard
    right and beyond it from every command, and none when an earlier kept clip has the same bytes; the threshold, set
    on the negatives and returned, goes into every manifest."""
    false_accept = cfg["synth"]["false_accept"]
    made = [s for s in SETS_KEPT if (interim / SETS[s] / "manifest.yaml").exists()]
    body = {s: yaml.safe_load((interim / SETS[s] / "manifest.yaml").read_text(encoding="utf-8")) for s in made}
    margin = threshold(body["negatives"]["clips"], false_accept)
    for c in body["positives"]["clips"]:
        c["kept"] = c["passed"] or c["margin"] <= margin
    for c in body["negatives"]["clips"]:
        c["kept"] = c["passed"] and nearest(c) > margin
    seen: set[str] = set()
    for s in made:
        for c in body[s]["clips"]:
            c["kept"] = c["kept"] and c["sha256"] not in seen
            seen |= {c["sha256"]} if c["kept"] else set()
        body[s]["threshold"] = {"false_accept": false_accept, "margin": round(margin, 3)}
        clips.write_manifest(interim / SETS[s], body[s])
    return margin


def measure(rows: list[dict], quality: dict) -> list[dict]:
    """Each row with the edges of its clip (clips.edges), to hear before an overnight run whether starts or ends are
    cut."""
    return [r | clips.edges(Path(r["wav"]), quality["frame_s"], quality["below_peak_db"]) for r in rows]


def table(rows: list[dict], key) -> list[str]:
    """Clips heard as their own text, kept once selected, and the median edges, per group of key(row)."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    kept = all("kept" in r for r in rows)
    head = ["Nhóm", "Nghe đúng chữ", *(["Giữ"] if kept else []), "Lặng đầu (s)", "Lặng cuối (s)", "Đỉnh (dBFS)"]
    lines = [f"| {' | '.join(head)} |", "|" + "---|" * len(head)]
    for k, g in sorted(groups.items()):
        cells = [
            " / ".join(map(str, k)),
            f"{sum(r['passed'] for r in g)}/{len(g)}",
            *([f"{sum(r['kept'] for r in g)}/{len(g)}"] if kept else []),
            f"{np.median([r['lead_s'] for r in g]):.2f}",
            f"{np.median([r['tail_s'] for r in g]):.2f}",
            f"{np.max([r['peak_dbfs'] for r in g]):.1f}",
        ]
        lines.append(f"| {' | '.join(cells)} |")
    return lines


def references(cfg: dict, paths: dict, interim: Path) -> list[clips.Reference]:
    """The clone voices: VIVOS train speakers the command split keeps in train, and the parquet draws."""
    spec = cfg["synth"]
    vivos = clone_speakers(paths["raw"] / spec["references"]["vivos"], paths["splits"] / cfg["speaker_split"])
    rejected = set(screen.rejected(paths["interim"]))
    out = interim / clips.REFERENCES
    return clips.training_references(
        spec["references"], spec["ref_seconds"], spec["seed"], paths["raw"], out, rejected, vivos
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("set", choices=[*SETS, "select"])
    which = parser.parse_args(argv).set
    cfg, tts, paths = load_yaml(CONFIG), load_yaml(TTS_CONFIG), data_paths()
    interim, cache = paths["interim"] / "command", paths["cache"]
    if which == "select":
        print(f"margin threshold {select(cfg, interim):.3f}")
        return 0
    out = interim / SETS[which]
    commands = learned(cfg)
    refs = references(cfg, paths, interim)
    presets = engines.presets("vieneu", tts, cache)
    stream, vocab = phrases.token_stream(paths["raw"] / "speech", cache / phrases.CACHE)
    codes, tables = phrases.component_codes(vocab)
    rng = np.random.default_rng(cfg["synth"]["seed"])
    if which == "pilot":
        spec = cfg["synth"]["pilot"]
        commands = [c for c in commands if c["id"] in spec["commands"]]
        refs, presets = refs[:: max(1, len(refs) // spec["clones"])][: spec["clones"]], presets[: spec["presets"]]
        texts = negative_texts(cfg, commands, stream, vocab, codes, tables)
        texts = [t for kind in KINDS for t in [x for x in texts if x["kind"] == kind][: spec["negatives_per_kind"]]]
        requests = positive_requests(cfg, commands, spec["positives"], presets, refs, out)
        first = len(commands) * len(cfg["synth"]["forms"])
        negatives = negative_requests(commands, texts, presets, refs, rng, out, spec["voices"], first)
        requests = {e: requests[e] + negatives[e] for e in requests}
    elif which == "positives":
        requests = positive_requests(cfg, commands, cfg["synth"]["positives"], presets, refs, out)
    else:
        texts = negative_texts(cfg, commands, stream, vocab, codes, tables)
        requests = negative_requests(commands, texts, presets, refs, rng, out, cfg["synth"]["negatives"]["voices"])
    timings: dict[str, dict] = {}
    rows = clips.render(requests, tts, out / "work", cache, timings)
    wav_of = {(e, r["id"]): r["out"] for e, reqs in requests.items() for r in reqs}
    rows = measure([r | {"wav": wav_of[(r["engine"], r["id"])]} for r in rows], cfg["synth"]["quality"])
    body = {"commands": [c["id"] for c in commands], "synth": cfg["synth"], "tts": tts, "timings": timings}
    body |= {"texts": texts} if which != "positives" else {}
    body |= {"references": [str(r.wav) for r in refs], "clips": rows}
    clips.write_manifest(out, body)
    print(json.dumps(timings, indent=1))
    kind_of = {t["text"]: t["kind"] for t in body.get("texts", [])}
    print("\n".join(table(rows, lambda r: (r["engine"], r["id"].split("_")[0], kind_of.get(r["text"], r.get("say"))))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
