"""locks isolation."""

from __future__ import annotations

import pytest

from .support import no_timer as _no_timer


@pytest.fixture(autouse=True)
def isolate_host(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_CODE_MESSAGING_SOCKET",
        "CODEX_THREAD_ID",
        "CODEX_SESSION_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("llm_bus.locks.service.schedule_check", _no_timer)
