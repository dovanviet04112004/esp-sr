"""Host end of test_apps/components/test_report: check each line's CRC32 and ask the board again for lines lost.

A report is lines "<tag> <seq> <text> crc=<8 hex>", the CRC32 taken over "<seq> <text>", ending with "end <n> lines". On
the board, test_report_serve answers "resend <seq> ..." and "resend end". A host log may hold several reports in a row
(KEHOACH 4.5.7).
"""

from __future__ import annotations

import re
import zlib

ASKS = 10
LINE_TIMEOUT_S = 120
ANSWER_TIMEOUT_S = 3
SEQS_PER_ASK = 40
END = re.compile(r"end (\d+) lines$")


def pattern(tag: str) -> re.Pattern[str]:
    return re.compile(rf"{re.escape(tag)} (\d+ [^\r\n]*?) crc=([0-9a-f]{{8}})$")


def checked(body: str, crc_hex: str) -> tuple[int, str] | None:
    """(seq, text) when the CRC holds, None for a line damaged on the way."""
    if zlib.crc32(body.encode()) != int(crc_hex, 16):
        return None
    seq, _, text = body.partition(" ")
    return int(seq), text


def collect(dut, tag: str, line_timeout_s: float = LINE_TIMEOUT_S) -> list[str]:
    """Every line of the board's report in order, asking again for any the serial link lost or damaged."""
    import pexpect

    line = re.compile(rf"{re.escape(tag)} (\d+ [^\r\n]*?) crc=([0-9a-f]{{8}})(?=\r?\n)".encode())
    got: dict[int, str] = {}
    total: list[int] = []

    def read(timeout: float) -> None:
        match = dut.expect(line, timeout=timeout)
        found = checked(match.group(1).decode(errors="replace"), match.group(2).decode())
        if found is None:
            return
        seq, text = found
        got.setdefault(seq, text)
        end = END.match(text)
        if end and not total:
            total.append(int(end.group(1)))

    try:
        while not total:
            read(line_timeout_s)
    except pexpect.TIMEOUT:
        pass
    for _ in range(ASKS):
        missing = ["end"] if not total else [str(s) for s in range(total[0]) if s not in got]
        if not missing:
            return [got[s] for s in range(total[0])]
        for k in range(0, len(missing), SEQS_PER_ASK):
            dut.write("resend " + " ".join(missing[k : k + SEQS_PER_ASK]) + "\n")
        try:
            while True:
                read(ANSWER_TIMEOUT_S)
        except pexpect.TIMEOUT:
            pass
    raise AssertionError(f"{tag} report incomplete after {ASKS} asks: have {sorted(got)}, end {total}")


def from_log(lines: list[str], tag: str) -> list[str]:
    """The text of every report in a log, in order; a gap or a damaged line fails, as a log cannot be asked again."""
    line = pattern(tag)
    texts: list[str] = []
    report: dict[int, str] = {}
    for raw in lines:
        match = line.search(raw.rstrip("\r\n"))
        if not match:
            continue
        found = checked(match.group(1), match.group(2))
        if found is None:
            raise ValueError(f"damaged line: {raw.strip()}")
        seq, text = found
        report.setdefault(seq, text)
        end = END.match(text)
        if end:
            missing = [s for s in range(int(end.group(1))) if s not in report]
            if missing:
                raise ValueError(f"{tag} report is missing lines {missing}")
            texts += [report[s] for s in range(int(end.group(1)))]
            report = {}
    if report:
        raise ValueError(f"{tag} report never ended")
    return texts
