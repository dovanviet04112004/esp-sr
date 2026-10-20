"""Check that tools/board_link keeps an RFC 2217 port's read timeout off the bridge.

A stand-in serial.rfc2217 renegotiates on every timeout change, as PyPI's pyserial 3.5 does; Debian's build already
keeps the timeout local, so the stand-in is the negative control.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BOARD_LINK = Path(__file__).resolve().parents[1] / "board_link"
STAND_IN = """
class Serial:
    def __init__(self):
        self._timeout = None
        self.is_open = True
        self.renegotiations = 0

    def _reconfigure_port(self):
        self.renegotiations += 1

    @property
    def timeout(self):
        return self._timeout

    @timeout.setter
    def timeout(self, timeout):
        self._timeout = timeout
        if self.is_open:
            self._reconfigure_port()
"""
PROBE = """
import serial.rfc2217
port = serial.rfc2217.Serial()
port._reconfigure_port = lambda: setattr(port, "renegotiations", getattr(port, "renegotiations", 0) + 1)
port.is_open = True
port.timeout = 3
port.timeout = 0.05
print(getattr(port, "renegotiations", 0), port.timeout)
"""


def renegotiations(*pythonpath: Path) -> str:
    """Renegotiations of an open port for two timeout changes, and the timeout it keeps, in a fresh Python."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    if pythonpath:
        env["PYTHONPATH"] = os.pathsep.join(str(p) for p in pythonpath)
    done = subprocess.run([sys.executable, "-c", PROBE], env=env, capture_output=True, text=True, check=True)
    return done.stdout.strip()


class StandInTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.packages = Path(folder.name)
        (self.packages / "serial").mkdir()
        (self.packages / "serial" / "__init__.py").write_text("")
        (self.packages / "serial" / "rfc2217.py").write_text(STAND_IN)

    def test_stand_in_renegotiates_each_change(self) -> None:
        self.assertEqual(renegotiations(self.packages), "2 0.05")

    def test_board_link_keeps_the_timeout_on_this_side(self) -> None:
        self.assertEqual(renegotiations(BOARD_LINK, self.packages), "0 0.05")


@unittest.skipIf(importlib.util.find_spec("serial") is None, "pyserial is not installed")
class PyserialTests(unittest.TestCase):
    def test_installed_pyserial_keeps_the_timeout_on_this_side(self) -> None:
        self.assertEqual(renegotiations(BOARD_LINK), "0 0.05")


if __name__ == "__main__":
    unittest.main()
