"""Run the parity app on board B and judge every PARITY line against contracts/golden/<block>/tolerance.yaml.

Also runs by hand on a captured log: python3 pytest_parity.py <log>. Cases named case_neg_* are
negative controls and must fail, or the golden set is not checking anything (KEHOACH 3.14).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

GOLDEN = Path(__file__).resolve().parents[3] / "contracts" / "golden"
LINE = re.compile(r"PARITY (\S+) (\S+) (\S+) max_abs=(\S+) snr_db=(\S+)")
DONE = re.compile(r"PARITY done (\d+) cases")
PLAN = re.compile(r"PARITY plan ((?:\S+ ?)+)$")
NEGATIVE_PREFIX = "case_neg_"


def judge(lines: list[str], golden: Path = GOLDEN) -> list[str]:
    """Every broken expectation: a case outside tolerance, a negative control inside it, a case never run.

    A run that prints "PARITY plan <blocks>" answers for those blocks only; without a plan every block counts.
    """
    passed: dict[tuple[str, str], list[bool]] = {}
    tolerances: dict[str, dict] = {}
    for line in lines:
        match = LINE.search(line)
        if not match:
            continue
        block, case, tensor, max_abs, snr_db = match.groups()
        if block not in tolerances:
            tolerances[block] = yaml.safe_load((golden / block / "tolerance.yaml").read_text(encoding="utf-8"))
        limit = tolerances[block]["tensors"][tensor]
        within = float(max_abs) <= limit["max_abs"] and float(snr_db) >= limit["min_snr_db"]
        passed.setdefault((block, case), []).append(within)
    problems = [] if any(DONE.search(line) for line in lines) else ["the app never printed PARITY done"]
    planned = {block for line in lines if (plan := PLAN.search(line.strip())) for block in plan.group(1).split()}
    for block_dir in sorted(p for p in golden.iterdir() if p.is_dir() and (not planned or p.name in planned)):
        for gold in sorted(block_dir.glob("*.gold")):
            key = (block_dir.name, gold.stem)
            if key not in passed:
                problems.append(f"{key[0]}/{key[1]} never ran")
            elif gold.stem.startswith(NEGATIVE_PREFIX) and all(passed[key]):
                problems.append(f"{key[0]}/{key[1]} is a negative control and passed")
            elif not gold.stem.startswith(NEGATIVE_PREFIX) and not all(passed[key]):
                problems.append(f"{key[0]}/{key[1]} is outside tolerance.yaml")
    return problems


@pytest.mark.esp32s3
def test_parity(dut) -> None:
    lines = []
    while not lines or not DONE.search(lines[-1]):
        lines.append(dut.expect(re.compile(rb"PARITY [^\r\n]+(?=\r?\n)"), timeout=120).group(0).decode())
    assert judge(lines) == []


if __name__ == "__main__":
    found = judge(Path(sys.argv[1]).read_text(encoding="utf-8").splitlines())
    print("\n".join(found) if found else "parity: every case as expected")
    raise SystemExit(1 if found else 0)
