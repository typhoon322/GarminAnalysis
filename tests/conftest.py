"""Keep tests off the project data directory and off the network."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_local_data(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GARMIN_FITNESS_DISABLE_NETWORK", "1")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    monkeypatch.delenv("GARMIN_PASSWORD", raising=False)
