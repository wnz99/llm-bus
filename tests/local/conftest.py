"""local isolation."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_codex_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep installer tests in their temporary homes, not the running host settings."""
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
