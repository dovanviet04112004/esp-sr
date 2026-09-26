#!/usr/bin/env python3
"""Enforce the comment rules of CLAUDE.md sections 2.3, 2.4 and 2.6.

Run with no arguments to scan the whole repo, or pass paths to scan a subset.
Exit code is 1 when any rule is violated.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SKIP_DIR_PARTS = {
    ".git",
    ".claude",
    "build",
    "dist",
    "managed_components",
    "third_party",
    "generated",
    "__pycache__",
    ".venv",
    ".ruff_cache",
    ".pytest_cache",
    "artifacts",
    "data",
}

C_HEADER_SUFFIXES = {".h", ".hpp"}
C_BODY_SUFFIXES = {".c", ".cpp", ".cc"}
PY_SUFFIXES = {".py"}
YAML_SUFFIXES = {".yaml", ".yml"}
SCANNED_SUFFIXES = C_HEADER_SUFFIXES | C_BODY_SUFFIXES | PY_SUFFIXES | YAML_SUFFIXES

MAX_CONSECUTIVE_BODY_COMMENTS = 2
MAX_DOC_COMMENT_LINES = 6
MAX_TRAILING_COMMENT_CHARS = 60
MAX_COMMENT_DENSITY = 0.10

PROCESS_WORDS = [
    "previously",
    "used to",
    "was",
    "were",
    "before",
    "originally",
    "changed",
    "updated",
    "fixed",
    "refactored",
    "instead of",
    "now we",
    "no longer",
    "note that i",
    "trước đây",
    "đã sửa",
    "thay vì",
]
PROCESS_RE = re.compile(
    r"(?<![\w])(" + "|".join(re.escape(w) for w in PROCESS_WORDS) + r")(?![\w])",
    re.IGNORECASE,
)

BANNER_RE = re.compile(r"^\s*(//|#)\s*[=*\-#~_+─━═]{2,}")
STAR_BANNER_RE = re.compile(r"/\*{3,}")
END_OF_RE = re.compile(r"^\s*(//|#)\s*end of\b", re.IGNORECASE)
# Code is recognised by its shape, not by a leading word: "for the next hop" is prose, "for (" is code.
CODE_LIKE_RE = re.compile(
    r"^\s*(//|#)\s*"
    r"("
    r".*;\s*$"
    r"|.*\{\s*$"
    r"|\}\s*;?\s*$"
    r"|(if|for|while|switch)\s*\("
    r"|return\b.*;"
    r"|#\s*include\s*[<\"]"
    r"|(import|from)\s+[\w.]+(\s+import\b.*)?\s*$"
    r"|(def|class)\s+\w+\s*[(:]"
    r")"
)
GENERATED_FIRST_LINES = {"// GENERATED FILE - DO NOT EDIT.", "# GENERATED FILE - DO NOT EDIT."}


@dataclass
class Problem:
    path: Path
    line: int
    rule: str
    detail: str

    def render(self, root: Path) -> str:
        try:
            shown: Path | str = self.path.relative_to(root)
        except ValueError:
            shown = self.path
        return f"{shown}:{self.line}: [{self.rule}] {self.detail}"


def is_skipped(path: Path) -> bool:
    """True for a path under SKIP_DIR_PARTS, measured from the repo root.

    The parts above the root belong to whoever cloned it and must not decide
    whether a file is scanned.
    """
    try:
        parts = path.relative_to(REPO_ROOT).parts
    except ValueError:
        parts = path.parts
    if SKIP_DIR_PARTS.intersection(parts):
        return True
    # idf.py -B puts a second profile in build_<name>, generated the same way.
    return any(part.startswith("build_") for part in parts)


def tracked_files() -> set[Path] | None:
    """Files git tracks or has staged; untracked work in progress must not block an unrelated commit."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "--cached"], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return {(REPO_ROOT / line).resolve() for line in out.splitlines() if line}


def iter_source_files(targets: list[Path], only: set[Path] | None = None) -> list[Path]:
    files: list[Path] = []
    for target in targets:
        if target.is_file():
            if target.suffix in SCANNED_SUFFIXES and not is_skipped(target):
                files.append(target)
            continue
        for path in sorted(target.rglob("*")):
            if path.suffix not in SCANNED_SUFFIXES or not path.is_file():
                continue
            if is_skipped(path) or (only is not None and path.resolve() not in only):
                continue
            files.append(path)
    return files


def strip_string_literals(line: str) -> str:
    # Backticks count: a url inside a template literal carries // and would
    # otherwise read as the start of a comment.
    return re.sub(
        r"(\"(\\.|[^\"\\])*\"|'(\\.|[^'\\])*'|`(\\.|[^`\\])*`)",
        '""',
        line,
    )


def find_line_comment(line: str, markers: tuple[str, ...]) -> tuple[int, str] | None:
    masked = strip_string_literals(line)
    for marker in markers:
        idx = masked.find(marker)
        if idx != -1:
            return idx, line[idx:].rstrip()
    return None


def is_generated(lines: list[str]) -> bool:
    return bool(lines) and lines[0].strip() in GENERATED_FIRST_LINES


def check_generated_banner(path: Path, lines: list[str]) -> list[Problem]:
    if len(lines) < 3:
        return [Problem(path, 1, "2.9", "generated file needs the three-line banner")]
    mark = lines[0].split()[0]
    problems = []
    if not lines[1].strip().startswith(f"{mark} Source:"):
        problems.append(Problem(path, 2, "2.9", f"second banner line must be '{mark} Source: ...'"))
    if not lines[2].strip().startswith(f"{mark} Regenerate:"):
        problems.append(Problem(path, 3, "2.9", f"third banner line must be '{mark} Regenerate: ...'"))
    return problems


def check_common(path: Path, comments: list[tuple[int, str]]) -> list[Problem]:
    problems = []
    for lineno, text in comments:
        hit = PROCESS_RE.search(text)
        if hit:
            problems.append(Problem(path, lineno, "2.4", f"process comment, drop the word {hit.group(1)!r}"))
        if BANNER_RE.match(text) or STAR_BANNER_RE.search(text):
            problems.append(Problem(path, lineno, "2.6", "banner or ASCII art comment"))
        if END_OF_RE.match(text):
            problems.append(Problem(path, lineno, "2.6", "'end of ...' comment"))
        if CODE_LIKE_RE.match(text):
            problems.append(Problem(path, lineno, "2.6", "commented-out code, delete it"))
    return problems


def check_runs_and_density(
    path: Path,
    lines: list[str],
    comment_lines: set[int],
    is_header: bool,
    density: bool = True,
) -> list[Problem]:
    problems = []
    if not is_header:
        run_start = None
        run_len = 0
        for lineno in range(1, len(lines) + 1):
            if lineno in comment_lines and not lines[lineno - 1].strip().startswith(("//", "#")):
                continue
            if lineno in comment_lines:
                run_start = run_start or lineno
                run_len += 1
            else:
                if run_len > MAX_CONSECUTIVE_BODY_COMMENTS:
                    problems.append(
                        Problem(
                            path,
                            run_start,
                            "2.3",
                            f"{run_len} consecutive comment lines, limit is {MAX_CONSECUTIVE_BODY_COMMENTS}",
                        )
                    )
                run_start = None
                run_len = 0
        if run_len > MAX_CONSECUTIVE_BODY_COMMENTS:
            problems.append(Problem(path, run_start, "2.3", f"{run_len} consecutive comment lines"))

        code_lines = [i for i, ln in enumerate(lines, 1) if ln.strip()]
        if density and len(code_lines) >= 20:
            share = len(comment_lines) / len(code_lines)
            if share > MAX_COMMENT_DENSITY:
                problems.append(
                    Problem(
                        path,
                        1,
                        "2.3",
                        f"comment density {share:.0%} exceeds {MAX_COMMENT_DENSITY:.0%}",
                    )
                )
    return problems


def check_c_like(path: Path, lines: list[str], is_header: bool, allows_doc: bool | None = None) -> list[Problem]:
    if allows_doc is None:
        allows_doc = is_header
    problems: list[Problem] = []
    comments: list[tuple[int, str]] = []
    comment_lines: set[int] = set()

    in_block = False
    block_start = 0
    block_len = 0
    block_is_doc = False

    for lineno, raw in enumerate(lines, 1):
        line = raw.rstrip("\n")
        if in_block:
            comments.append((lineno, line))
            comment_lines.add(lineno)
            block_len += 1
            if "*/" in line:
                in_block = False
                if block_is_doc and block_len > MAX_DOC_COMMENT_LINES:
                    problems.append(
                        Problem(
                            path,
                            block_start,
                            "2.3",
                            f"doc comment is {block_len} lines, limit is {MAX_DOC_COMMENT_LINES}",
                        )
                    )
            continue

        masked = strip_string_literals(line)
        block_idx = masked.find("/*")
        if block_idx != -1:
            doc = masked[block_idx:].startswith("/**")
            closes = "*/" in masked[block_idx + 2 :]
            comments.append((lineno, line[block_idx:]))
            comment_lines.add(lineno)
            if doc and not allows_doc:
                problems.append(Problem(path, lineno, "2.3", "doc comment in a body file"))
            elif not doc and not is_header:
                problems.append(Problem(path, lineno, "2.6", "block comment in a body file"))
            if not closes:
                in_block = True
                block_start = lineno
                block_len = 1
                block_is_doc = doc
            elif doc:
                pass
            continue

        found = find_line_comment(line, ("//",))
        if found:
            idx, text = found
            comments.append((lineno, text))
            comment_lines.add(lineno)
            trailing = line[:idx].strip() != ""
            if trailing and len(text) > MAX_TRAILING_COMMENT_CHARS:
                problems.append(
                    Problem(
                        path,
                        lineno,
                        "2.6",
                        f"trailing comment is {len(text)} chars, limit is {MAX_TRAILING_COMMENT_CHARS}",
                    )
                )

    problems += check_common(path, comments)
    problems += check_runs_and_density(path, lines, comment_lines, is_header)
    return problems


def check_python(path: Path, lines: list[str]) -> list[Problem]:
    """Only real comment tokens count: a '#' inside a triple-quoted string is data."""
    import io
    import tokenize

    comments: list[tuple[int, str]] = []
    comment_lines: set[int] = set()
    problems: list[Problem] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO("\n".join(lines) + "\n").readline))
    except (tokenize.TokenError, SyntaxError):
        return []
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        lineno, col = tok.start
        if lineno == 1 and tok.string.startswith("#!"):
            continue
        comments.append((lineno, tok.string))
        comment_lines.add(lineno)
        if lines[lineno - 1][:col].strip() and len(tok.string) > MAX_TRAILING_COMMENT_CHARS:
            problems.append(
                Problem(
                    path,
                    lineno,
                    "2.6",
                    f"trailing comment is {len(tok.string)} chars, limit is {MAX_TRAILING_COMMENT_CHARS}",
                )
            )
    problems += check_common(path, comments)
    problems += check_runs_and_density(path, lines, comment_lines, is_header=False)
    return problems


def check_docstrings(path: Path, source: str) -> list[Problem]:
    """Section 2.3 for Python doc comments, which the line scanners never see."""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    problems = []
    named = [(tree, "module")] + [
        (node, node.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    ]
    for node, name in named:
        doc = ast.get_docstring(node, clean=False)
        if doc is None:
            continue
        length = len(doc.splitlines())
        if length > MAX_DOC_COMMENT_LINES:
            line = node.body[0].lineno if node.body else 1
            problems.append(
                Problem(
                    path,
                    line,
                    "2.3",
                    f"{name} docstring is {length} lines, limit is {MAX_DOC_COMMENT_LINES}",
                )
            )
    return problems


def check_yaml(path: Path, lines: list[str]) -> list[Problem]:
    """Sections 2.3, 2.4 and 2.6 for config files, minus the density rule.

    Section 2.3 sets density per body file, and a config is key-value lines: one
    cited line per tuned constant is what 4.9 asks for, not a budget to spend.
    """
    comments: list[tuple[int, str]] = []
    comment_lines: set[int] = set()
    for lineno, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if not stripped.startswith("#"):
            continue
        comments.append((lineno, stripped.lstrip("#").strip()))
        comment_lines.add(lineno)
    problems = check_common(path, comments)
    header_end = 0
    for lineno in range(1, len(lines) + 1):
        if lineno not in comment_lines:
            break
        header_end = lineno
    body = {n for n in comment_lines if n > header_end}
    if header_end > MAX_DOC_COMMENT_LINES:
        problems.append(Problem(path, 1, "2.3", f"header is {header_end} lines, limit is 6"))
    problems += check_runs_and_density(path, lines, body, is_header=False, density=False)
    return problems


def check_file(path: Path) -> list[Problem]:
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    lines = source.splitlines()
    if is_generated(lines):
        return check_generated_banner(path, lines)
    if path.suffix in YAML_SUFFIXES:
        return check_yaml(path, lines)
    if path.suffix in PY_SUFFIXES:
        return check_python(path, lines) + check_docstrings(path, source)
    is_header = path.suffix in C_HEADER_SUFFIXES
    return check_c_like(path, lines, is_header)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()

    targets = [p.resolve() for p in args.paths] or [REPO_ROOT]
    problems: list[Problem] = []
    files = iter_source_files(targets, None if args.paths else tracked_files())
    for path in files:
        problems.extend(check_file(path))

    problems.sort(key=lambda p: (str(p.path), p.line))
    for problem in problems:
        print(problem.render(REPO_ROOT))

    print(f"check_comments: {len(files)} file(s), {len(problems)} problem(s)", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
