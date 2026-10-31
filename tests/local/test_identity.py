"""identity."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from llm_bus.cli.app import host_identity
from llm_bus.errors import BusError
from llm_bus.mailbox.connection import default_path
from llm_bus.mailbox.models import SenderContext
from llm_bus.mailbox.store import Store

if TYPE_CHECKING:
    from pathlib import Path


def test_session_end_hides_peer_but_preserves_pending_message(tmp_path: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    recipient = store.register("codex", "recipient", "/project")
    sent = store.send(SenderContext("claude", "sender", "/project"), recipient, "hello")

    store.end_session("codex", "recipient")
    assert recipient not in {agent["address"] for agent in store.agents("/project")}
    assert store.is_ended(recipient)
    ended = next(agent for agent in store.all_agents() if agent["address"] == recipient)
    assert ended["ended_at"] is not None
    assert store.inbox(recipient) == [sent]

    store.register("codex", "recipient", "/project")
    assert recipient in {agent["address"] for agent in store.agents("/project")}
    assert not store.is_ended(recipient)


def test_idle_session_closes_after_24_hours_and_reopens_on_registration(tmp_path: Path) -> None:
    path = tmp_path / "bus.sqlite3"
    store = Store(path)
    recipient = store.register("codex", "recipient", "/project")
    sent = store.send(SenderContext("claude", "sender", "/project"), recipient, "hello")
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE agents SET last_seen = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-23 hours') "
            "WHERE address = ?",
            (recipient,),
        )
    assert recipient in {agent["address"] for agent in store.agents("/project")}
    assert not store.is_ended(recipient)
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE agents SET last_seen = strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-25 hours') "
            "WHERE address = ?",
            (recipient,),
        )

    assert recipient not in {agent["address"] for agent in store.agents("/project")}
    assert store.is_ended(recipient)
    ended = next(agent for agent in store.all_agents() if agent["address"] == recipient)
    assert ended["ended_at"] is not None
    assert store.inbox(recipient) == [sent]

    store.register("codex", "recipient", "/project")
    assert recipient in {agent["address"] for agent in store.agents("/project")}
    assert not store.is_ended(recipient)
    assert store.inbox(recipient) == [sent]


def test_default_database_path_uses_windows_local_app_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLM_BUS_DB", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("llm_bus.mailbox.connection.sys.platform", "win32")
    assert default_path() == tmp_path / "llm-bus" / "bus.sqlite3"
    monkeypatch.delenv("LOCALAPPDATA")
    monkeypatch.setattr("llm_bus.mailbox.connection.Path.home", lambda: tmp_path)
    assert default_path() == tmp_path / "AppData" / "Local" / "llm-bus" / "bus.sqlite3"


def test_host_identity_uses_native_session_id() -> None:
    """No explicit name or launch argument is required."""
    assert host_identity({"CODEX_THREAD_ID": "thread-1"}) == ("codex", "thread-1")
    assert host_identity({"CLAUDE_CODE_SESSION_ID": "session-2"}) == ("claude", "session-2")
    with pytest.raises(BusError, match="No host session ID"):
        host_identity({})
