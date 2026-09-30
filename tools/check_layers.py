#!/usr/bin/env python3
"""Enforce the firmware layering of KEHOACH 4.5.4 and keep ESP-SR out of every build but one test app.

Reads REQUIRES and PRIV_REQUIRES from every component CMakeLists.txt, builds the dependency
graph, and fails on an edge that points sideways or upwards, on a forbidden edge, on a cycle,
on a component that requires espressif/esp-sr, or on an idf_component.yml that pulls it anywhere
outside test_apps/espsr_compare, the app that measures ESP-SR against dsp_afe (KEHOACH 4.5.1).
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIRMWARE_ROOT = REPO_ROOT / "firmware"

LAYERS: dict[str, int] = {
    "common": 0,
    "dsp_spec": 1,
    "lang_vi": 1,
    "bsp_board": 1,
    "dsp_afe": 2,
    "drv_audio": 2,
    "drv_led": 2,
    "sys_storage": 2,
    "sys_time": 2,
    "ai_engine": 3,
    "net_wifi": 3,
    "net_mqtt": 3,
    "net_stream": 3,
    "net_ota": 3,
    "svc_front": 4,
    "svc_listen": 4,
    "svc_speak": 4,
    "svc_report": 4,
    "svc_dialog": 5,
    "main": 6,
}

# Downward on paper but banned by rule 2: these meet through queues wired in main.
FORBIDDEN_EDGES: set[tuple[str, str]] = {
    ("svc_dialog", "svc_listen"),
    ("svc_dialog", "svc_speak"),
    ("svc_dialog", "svc_front"),
    ("svc_dialog", "svc_report"),
}

BANNED_DEPENDENCY_RE = re.compile(r"esp[-_]sr\b")
ESP_SR_APP = ("test_apps", "espsr_compare")
REGISTER_RE = re.compile(r"idf_component_register\s*\((.*?)\)", re.DOTALL)
REQUIRES_RE = re.compile(r"\b(PRIV_REQUIRES|REQUIRES)\b(.*?)(?=\b[A-Z_]{3,}\b\s|$)", re.DOTALL)
KEYWORD_RE = re.compile(r"^[A-Z_]{3,}$")


@dataclass
class Problem:
    component: str
    rule: str
    detail: str

    def render(self) -> str:
        return f"{self.component}: [{self.rule}] {self.detail}"


def strip_cmake_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def parse_requires(cmake_path: Path) -> set[str]:
    text = strip_cmake_comments(cmake_path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for block in REGISTER_RE.findall(text):
        for _, raw in REQUIRES_RE.findall(block):
            for token in raw.replace('"', " ").split():
                if KEYWORD_RE.match(token):
                    break
                found.add(token)
    return found


def collect_components(firmware_root: Path) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    candidates = []
    components_dir = firmware_root / "components"
    if components_dir.is_dir():
        candidates += [d for d in sorted(components_dir.iterdir()) if d.is_dir()]
    if (firmware_root / "main").is_dir():
        candidates.append(firmware_root / "main")
    for directory in candidates:
        cmake = directory / "CMakeLists.txt"
        if cmake.is_file():
            graph[directory.name] = parse_requires(cmake)
    return graph


def check_graph(graph: dict[str, set[str]]) -> list[Problem]:
    problems: list[Problem] = []
    for component, deps in sorted(graph.items()):
        if component not in LAYERS:
            problems.append(Problem(component, "4.5.4", "component is absent from the layer table"))
            continue
        own = LAYERS[component]
        for dep in sorted(deps):
            if BANNED_DEPENDENCY_RE.search(dep):
                problems.append(Problem(component, "4.5.1", f"requires {dep}; ESP-SR is banned (TONG QUAN 1)"))
                continue
            if dep not in LAYERS:
                continue
            if (component, dep) in FORBIDDEN_EDGES:
                problems.append(Problem(component, "4.5.4", f"must reach {dep} through a queue, never REQUIRES"))
            elif LAYERS[dep] == own:
                problems.append(Problem(component, "4.5.4", f"sideways dependency on {dep} (both L{own})"))
            elif LAYERS[dep] > own:
                problems.append(Problem(component, "4.5.4", f"upward dependency on {dep} (L{own} -> L{LAYERS[dep]})"))
    return problems


def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    cycles: list[list[str]] = []
    state: dict[str, int] = {}
    stack: list[str] = []

    def walk(node: str) -> None:
        state[node] = 1
        stack.append(node)
        for dep in sorted(graph.get(node, ())):
            if dep not in graph:
                continue
            if state.get(dep, 0) == 0:
                walk(dep)
            elif state.get(dep) == 1:
                cycles.append([*stack[stack.index(dep) :], dep])
        stack.pop()
        state[node] = 2

    for node in sorted(graph):
        if state.get(node, 0) == 0:
            walk(node)
    return cycles


def check_manifests(firmware_root: Path) -> list[Problem]:
    problems = []
    for manifest in sorted(firmware_root.rglob("idf_component.yml")):
        if "managed_components" in manifest.parts or any(p.startswith("build") for p in manifest.parts):
            continue
        if manifest.relative_to(firmware_root).parts[: len(ESP_SR_APP)] == ESP_SR_APP:
            continue
        for lineno, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
            if BANNED_DEPENDENCY_RE.search(line.split("#", 1)[0]):
                where = f"{manifest.relative_to(firmware_root)}:{lineno}"
                problems.append(Problem(where, "4.5.1", "pulls ESP-SR outside test_apps/espsr_compare (TONG QUAN 1)"))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--firmware-root", type=Path, default=FIRMWARE_ROOT)
    args = parser.parse_args()

    graph = collect_components(args.firmware_root)
    problems = check_graph(graph)
    for cycle in find_cycles(graph):
        problems.append(Problem(cycle[0], "4.5.4", "dependency cycle " + " -> ".join(cycle)))
    if args.firmware_root.is_dir():
        problems += check_manifests(args.firmware_root)

    for problem in problems:
        print(problem.render())
    print(f"check_layers: {len(graph)} component(s), {len(problems)} problem(s)", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
