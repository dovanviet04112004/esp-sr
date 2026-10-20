"""Restart board B and print what the product loaded: thresholds, roster and commands (make product, KEHOACH 6.2).

A "command thresholds ... seeded" line means this boot replaced the owner's thresholds with the models' suggestion.
"""

from __future__ import annotations

import argparse
import re
import sys

import serial

from srhost.wifi import PROMPT, Console

BOOT_TIMEOUT_S = 60.0
LISTENING = re.compile(rb"listening: [^\r\n]*")
SEEDED = re.compile(rb"command thresholds [^\r\n]* seeded: [^\r\n]*")
SHOWN = (
    LISTENING,
    SEEDED,
    re.compile(rb"speaker check [^\r\n]*"),
    re.compile(rb"commands v\d+: [^\r\n]*"),
    re.compile(rb"no models in [^\r\n]*"),
    re.compile(rb"[^\r\n]*: not listening[^\r\n]*"),
)


def boot_lines(text: bytes) -> list[str]:
    """The lines of a boot log a restore is judged on, in the order the board printed them."""
    found = sorted((m.start(), m.group(0)) for pattern in SHOWN for m in pattern.finditer(text))
    return [line.decode(errors="replace").strip() for _, line in found]


def judged(text: bytes) -> str | None:
    """Why the restore failed, or None when the product listens with the thresholds it already held."""
    if SEEDED.search(text):
        return "this boot seeded the models' suggested thresholds over the board's; nvs set kws ... puts them back"
    if not LISTENING.search(text):
        return "the product is not listening: see the lines above"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", required=True, help="board B's serial port, the bridge's URL by default")
    args = parser.parse_args(argv)
    try:
        console = Console(args.port)
        try:
            prompt = console.wait(PROMPT, BOOT_TIMEOUT_S)
        finally:
            console.close()
    except serial.SerialException as err:
        print(f"boot: {err}", file=sys.stderr)
        return 1
    if prompt is None:
        print(f"boot: no 'sr> ' prompt in {BOOT_TIMEOUT_S:.0f} s: is the product flashed?", file=sys.stderr)
        return 1
    text = prompt.string[: prompt.start()]
    for line in boot_lines(text):
        print(line)
    problem = judged(text)
    if problem:
        print(f"boot: {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
