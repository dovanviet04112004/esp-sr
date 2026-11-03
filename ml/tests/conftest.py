"""Shared guard of the ml tests: no test reaches the real model lock or firmware/models."""

from __future__ import annotations

from pathlib import Path

import pytest

from srpipe.export import pack_models, update_lock


@pytest.fixture(autouse=True)
def models_in_tmp(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The lock and firmware/models of pack_models and update_lock moved into a temp folder for every test, so a
    refusal that stops refusing writes there (E11-T42); a test that sets its own paths overrides these."""
    root = tmp_path_factory.mktemp("models_guard")
    for module in (pack_models, update_lock):
        monkeypatch.setattr(module, "LOCK", root / "models.lock.json")
        monkeypatch.setattr(module, "MODELS", root / "models")
    return root
