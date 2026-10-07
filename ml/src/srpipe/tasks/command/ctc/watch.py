"""Score a ctc run's checkpoints as its training writes them (KEHOACH 3.11, E11-T23): Gate 3 on each, its commands
whose text opens with a checked sắc or nặng syllable by that word, and the owner check of tone_flip on every
owner_every-th; one entry a checkpoint in <run>/watch.yaml, which watching the run again goes on from.
Run: python -m srpipe.tasks.command.ctc.watch <run under ml/>"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import yaml

from srpipe.core.config import ML_ROOT, data_paths, load_run_config, load_yaml
from srpipe.generated import lang_vi
from srpipe.lang import g2p
from srpipe.lang.normalize import normalize
from srpipe.tasks import command
from srpipe.tasks.command import ctc
from srpipe.tasks.command import eval as gate
from srpipe.tasks.command.ctc import tone_flip, train


def checked_word(text: str, region: int) -> str | None:
    """The first word of a command's text when its syllable is checked with sắc or nặng, the words tone tells apart."""
    first = g2p.syllables(normalize(text, region), region)[0]
    if first.coda in lang_vi.CHECKED_CODAS and first.tone in lang_vi.CHECKED_TONES:
        return text.split()[0]
    return None


def gate_entry(results: list[gate.Scored], word_of: dict[str, str | None], reject: int, margin: int) -> dict:
    """Gate 3 in figures: commands right with no threshold and accepted at reject and margin, windows to reject
    rejected, and per checked word the utterances of its commands right and accepted."""
    said = [(r.expected, h) for r in results if r.expected != gate.REJECT for h in r.decided]
    others = [h for r in results if r.expected == gate.REJECT for h in r.decided]
    by_word: dict[str, Counter] = {}
    for expected, h in said:
        if word := word_of.get(expected):
            c = by_word.setdefault(word, Counter())
            c["utterances"] += 1
            c["right"] += h.command == expected
            c["accepted"] += h.command == expected and h.accepted(reject, margin)
    return {
        "commands": len(said),
        "right": sum(h.command == e for e, h in said),
        "accepted": sum(h.command == e and h.accepted(reject, margin) for e, h in said),
        "to_reject": len(others),
        "rejected": sum(not h.accepted(reject, margin) for h in others),
        "words": {word: dict(c) for word, c in by_word.items()},
    }


def by_day(rows: list[dict]) -> dict[str, dict]:
    """Utterances, right and accepted per day and word, the distances summed."""
    days: dict[str, Counter] = {}
    for r in rows:
        c = days.setdefault(f"{r['day']} {r['word']}", Counter())
        for k in ("utterances", "right", "accepted"):
            c[k] += r[k]
    return {key: dict(c) for key, c in days.items()}


def owner_entry(report: dict) -> dict:
    """The owner check in figures: per word the utterances right with F0 kept that moving it turned, and per day
    and word every utterance right and accepted as recorded and with each set of pitch dims held."""
    held = {name: by_day(rows) for name, rows in report["held"].items()}
    return {"turned": report["flips"], "recorded": by_day(report["recorded"]), "held": held}


def landed(path: Path, poll_s: float, settled_s: float) -> None:
    """Wait for a checkpoint the training writes, then until it has been left alone settled_s, so it is whole."""
    while not path.exists() or time.time() - path.stat().st_mtime < settled_s:
        time.sleep(poll_s if not path.exists() else settled_s)


def watch(run: Path, paths: dict) -> Path:
    """Every checkpoint of run's schedule scored as it lands, into <run>/watch.yaml."""
    cfg, spec = load_run_config(run), load_yaml(ctc.CONFIG)
    flip, every = spec["tone_flip"], spec["watch"]["owner_every"]
    board = load_yaml(command.CONFIG)["eval"]["board"]
    owner_set = ML_ROOT.parent / flip["owner_set"]
    listed = json.loads(owner_set.read_text(encoding="utf-8"))["commands"]
    region = lang_vi.DIALECTS.index(cfg["train"]["dialect"])
    default = json.loads(command.COMMANDS.read_text(encoding="utf-8"))["commands"]
    word_of = {c["id"]: checked_word(c["text"], region) for c in default}
    reject, margin = flip["accept_permille"]
    out = run / "watch.yaml"
    entries = (yaml.safe_load(out.read_text(encoding="utf-8")) if out.exists() else None) or []
    done = {e["step"] for e in entries}
    eval_every = cfg["train"]["eval_every"]
    for n, step in enumerate(range(eval_every, cfg["train"]["steps"] + 1, eval_every), start=1):
        if step in done:
            continue
        weights = train.checkpoint(run, step)
        landed(weights, spec["watch"]["poll_s"], spec["watch"]["settled_s"])
        entry = {"step": step}
        results = gate.ctc_board(gate.load_ctc(run, weights=weights), board, paths)
        entry["gate"] = gate_entry(results, word_of, reject, margin)
        if n % every == 0:
            owner = tone_flip.owner_check(gate.load_ctc(run, owner_set, weights), listed, flip, paths)
            entry["owner"] = owner_entry(owner)
        entries.append(entry)
        out.write_text(yaml.safe_dump(entries, allow_unicode=True, sort_keys=False), encoding="utf-8")
        print(yaml.safe_dump([entry], allow_unicode=True, sort_keys=False), flush=True)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path, help="a run directory the ctc train is writing")
    args = parser.parse_args(argv)
    print(watch(args.run, data_paths()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
