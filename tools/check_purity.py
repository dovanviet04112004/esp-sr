#!/usr/bin/env python3
"""Keep the pure layer pure: common, dsp_* and lang_* (KEHOACH 4.5.3 rules 7-10).

A pure component builds on a PC and runs inside the frame deadline, so its sources must not
include FreeRTOS, drivers, Wi-Fi, timers, logging or heap headers, and must not allocate.
Test apps are exempt; they are where the board-only harness lives.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPONENTS = REPO_ROOT / "firmware" / "components"

PURE_RE = re.compile(r"^(common|dsp_[a-z0-9_]+|lang_[a-z0-9_]+)$")
SOURCE_SUFFIXES = {".c", ".h", ".cpp", ".hpp"}

FORBIDDEN_INCLUDE_RE = re.compile(
    r"^\s*#\s*include\s*[<\"]("
    r"freertos/.*|driver/.*|esp_driver_.*|hal/.*|soc/.*"
    r"|esp_wifi.*|esp_event.*|esp_netif.*|lwip/.*|mqtt.*"
    r"|esp_timer\.h|esp_log\.h|esp_heap_caps\.h|esp_system\.h|esp_partition\.h|nvs.*"
    r")[>\"]"
)
# The FFT libraries allocate their own tables at init (KEHOACH 4.5.3 rule 7); only these wrappers may say where.
FFT_TABLE_WRAPPERS = {"dsp_spec/src/fft_dl.c", "dsp_spec/src/fft_dsp.c"}
HEAP_CAPS_INCLUDE_RE = re.compile(r"^\s*#\s*include\s*[<\"]esp_heap_caps\.h[>\"]")
ALLOCATION_RE = re.compile(r"\b(malloc|calloc|realloc|free|heap_caps_\w+|pvPortMalloc|vPortFree)\s*\(")
LOG_RE = re.compile(r"\b(ESP_LOG[EWIDV]|ESP_EARLY_LOG[EWIDV]|printf|puts)\s*\(")


@dataclass
class Problem:
    path: Path
    line: int
    detail: str

    def render(self) -> str:
        return f"{self.path.relative_to(REPO_ROOT)}:{self.line}: [4.5.3] {self.detail}"


def strip_comments(line: str) -> str:
    return re.sub(r"//.*$", "", re.sub(r"/\*.*?\*/", "", line))


def pure_sources(components_dir: Path) -> list[Path]:
    if not components_dir.is_dir():
        return []
    files = []
    for component in sorted(d for d in components_dir.iterdir() if d.is_dir() and PURE_RE.match(d.name)):
        for path in sorted(component.rglob("*")):
            if path.suffix in SOURCE_SUFFIXES and "test_apps" not in path.relative_to(component).parts:
                files.append(path)
    return files


def is_fft_table_wrapper(path: Path) -> bool:
    return "/".join(path.parts[-3:]) in FFT_TABLE_WRAPPERS


def check_file(path: Path) -> list[Problem]:
    problems = []
    wrapper = is_fft_table_wrapper(path)
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = strip_comments(raw)
        if wrapper and HEAP_CAPS_INCLUDE_RE.match(line):
            continue
        if FORBIDDEN_INCLUDE_RE.match(line):
            problems.append(Problem(path, lineno, f"pure layer includes a platform header: {line.strip()}"))
        if hit := ALLOCATION_RE.search(line):
            problems.append(
                Problem(path, lineno, f"pure layer allocates with {hit.group(1)}(); the caller owns memory")
            )
        if hit := LOG_RE.search(line):
            problems.append(Problem(path, lineno, f"pure layer logs with {hit.group(1)}(); count into stats instead"))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--components", type=Path, default=COMPONENTS)
    args = parser.parse_args()

    files = pure_sources(args.components)
    problems = [p for f in files for p in check_file(f)]
    for problem in problems:
        print(problem.render())
    print(f"check_purity: {len(files)} file(s), {len(problems)} problem(s)", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
