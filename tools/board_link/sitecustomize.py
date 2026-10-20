"""Loaded first by every Python a board goal of the Makefile starts, through PYTHONPATH (KEHOACH 2.1).

pyserial's RFC 2217 client renegotiates the whole port each time the read timeout changes, about 300 ms over board B's
bridge, and esptool changes it around every command. RFC 2217 carries no read timeout, so once serial.rfc2217 loads,
setting it stays on this side.
"""

from __future__ import annotations

import importlib.machinery
import sys
from collections.abc import Sequence
from types import ModuleType

RFC2217 = "serial.rfc2217"


def local_timeout(port: object, timeout: float | None) -> None:
    """Set an RFC 2217 port's read timeout on this side only; its reads already wait on this value."""
    port._timeout = timeout


class LocalTimeoutFinder:
    """Finds serial.rfc2217 as the path finder does and patches its Serial once the module has run."""

    def find_spec(
        self, name: str, path: Sequence[str] | None, target: ModuleType | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        if name != RFC2217:
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        if spec is None or spec.loader is None:
            return spec
        run = spec.loader.exec_module

        def run_and_patch(module: ModuleType) -> None:
            run(module)
            module.Serial.timeout = property(module.Serial.timeout.fget, local_timeout)

        spec.loader.exec_module = run_and_patch
        return spec


sys.meta_path.insert(0, LocalTimeoutFinder())
