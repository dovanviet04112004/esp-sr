#!/usr/bin/env python3
"""Copy the GitHub Actions results of one commit onto the same commit in Gitea (KEHOACH 4.8).

GitHub cannot reach the LAN Gitea, so this runs on the developer's machine after a push. Remotes
come from `git remote`: `github` for the runs, `origin` for Gitea. The Gitea token is read from
GITEA_TOKEN or ~/.config/esp-sr/gitea_token and never leaves this machine.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

TOKEN_FILE = Path.home() / ".config" / "esp-sr" / "gitea_token"
DETACHED_LOG = Path.home() / ".cache" / "esp-sr" / "ci_status.log"
POLL_S = 20
FAILED = {"failure", "timed_out", "cancelled", "startup_failure"}
PASSED = {"success", "neutral", "skipped"}


@dataclass
class Run:
    name: str
    status: str
    conclusion: str | None
    url: str

    @property
    def gitea_state(self) -> str:
        if self.status != "completed":
            return "pending"
        if self.conclusion in PASSED:
            return "success"
        return "failure" if self.conclusion in FAILED else "error"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def parse_remote(url: str) -> tuple[str, str]:
    """Split a remote URL into (base URL, owner/repo), dropping any credentials and .git."""
    match = re.match(r"^(https?://)(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?/?$", url)
    if not match:
        raise SystemExit(f"cannot read remote url {url!r}")
    scheme, host, path = match.groups()
    return f"{scheme}{host}", path


def latest_runs(github_repo: str, sha: str) -> list[Run]:
    raw = subprocess.run(
        ["gh", "api", f"repos/{github_repo}/actions/runs?head_sha={sha}&per_page=50"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    newest: dict[str, Run] = {}
    for r in sorted(json.loads(raw)["workflow_runs"], key=lambda r: r["run_number"]):
        newest[r["name"]] = Run(r["name"], r["status"], r["conclusion"], r["html_url"])
    return list(newest.values())


def post_status(gitea_base: str, gitea_repo: str, sha: str, token: str, run: Run) -> None:
    body = json.dumps(
        {
            "state": run.gitea_state,
            "target_url": run.url,
            "description": f"GitHub Actions: {run.conclusion or run.status}",
            "context": f"github/{run.name}",
        }
    ).encode()
    request = urllib.request.Request(
        f"{gitea_base}/api/v1/repos/{gitea_repo}/statuses/{sha}",
        data=body,
        headers={"Authorization": f"token {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        response.read()


def read_token() -> str:
    token = os.environ.get("GITEA_TOKEN") or (TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else "")
    if not token:
        raise SystemExit(f"no Gitea token: set GITEA_TOKEN or write one to {TOKEN_FILE}")
    return token


def detach_from_pre_push() -> int:
    """Called by the pre-push hook: for a push to GitHub, start a detached waiter and return at once."""
    github_url = git("remote", "get-url", "github")
    if parse_remote(os.environ.get("PRE_COMMIT_REMOTE_URL", ""))[1] != parse_remote(github_url)[1]:
        return 0
    sha = os.environ.get("PRE_COMMIT_TO_REF") or git("rev-parse", "HEAD")
    DETACHED_LOG.parent.mkdir(parents=True, exist_ok=True)
    with DETACHED_LOG.open("a") as log:
        subprocess.Popen(
            [sys.executable, __file__, "--sha", sha, "--wait"],
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    print(f"ci_status: watching GitHub Actions for {sha[:10]}; log {DETACHED_LOG}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sha", default="HEAD", help="commit to report; defaults to HEAD")
    parser.add_argument("--wait", action="store_true", help="poll until no run is pending")
    parser.add_argument("--timeout-s", type=int, default=1800)
    parser.add_argument("--appear-timeout-s", type=int, default=300, help="give up when no run shows up")
    parser.add_argument("--from-pre-push", action="store_true", help="detach and return; used by the hook")
    args = parser.parse_args()
    if args.from_pre_push:
        return detach_from_pre_push()

    sha = git("rev-parse", args.sha)
    _, github_repo = parse_remote(git("remote", "get-url", "github"))
    gitea_base, gitea_repo = parse_remote(git("remote", "get-url", "origin"))
    token = read_token()

    started = time.monotonic()
    deadline = started + args.timeout_s
    posted: dict[str, str] = {}
    while True:
        runs = latest_runs(github_repo, sha)
        for run in runs:
            if posted.get(run.name) != run.gitea_state:
                post_status(gitea_base, gitea_repo, sha, token, run)
                posted[run.name] = run.gitea_state
                print(f"{run.gitea_state:8} github/{run.name}  {run.url}", flush=True)
        settled = runs and all(r.gitea_state != "pending" for r in runs)
        never_started = not runs and time.monotonic() - started >= args.appear_timeout_s
        if not args.wait or settled or never_started or time.monotonic() >= deadline:
            break
        time.sleep(POLL_S)
    if not runs:
        print(f"no GitHub Actions run for {sha[:10]} yet")
        return 2
    if any(r.gitea_state in {"failure", "error"} for r in runs):
        return 1
    return 2 if any(r.gitea_state == "pending" for r in runs) else 0


if __name__ == "__main__":
    sys.exit(main())
