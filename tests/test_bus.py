"""Focused local mailbox and host identity tests."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING, cast

import pytest

from llm_bus import host_identity, main
from llm_bus_store import BusError, Store

if TYPE_CHECKING:
    from pathlib import Path


def test_messages_survive_reopen_until_recipient_acknowledges(tmp_path: Path) -> None:
    """Both providers share mailbox; reads never consume messages."""
    path = tmp_path / "bus.sqlite3"
    store = Store(path)
    codex = store.register("codex", "codex-session", "/project")
    claude = store.register("claude", "claude-session", "/project")

    sent = store.send(codex, claude, "Review migration")
    reopened = Store(path)
    assert reopened.inbox(claude) == [sent]
    assert reopened.inbox(claude) == [sent]
    assert reopened.inbox(codex) == []
    with pytest.raises(BusError, match="do not belong"):
        reopened.acknowledge(codex, [sent["id"]])
    assert reopened.acknowledge(claude, [sent["id"]]) == 1
    assert reopened.acknowledge(claude, [sent["id"]]) == 0
    assert Store(path).inbox(claude) == []


def test_unknown_recipient_and_oversized_message_are_rejected(tmp_path: Path) -> None:
    """Typos and unbounded payloads cannot silently fill mailbox."""
    store = Store(tmp_path / "bus.sqlite3")
    sender = store.register("claude", "one", "/project")
    recipient = store.register("codex", "two", "/project")
    with pytest.raises(BusError, match="Unknown recipient"):
        store.send(sender, "codex:missing", "hello")
    with pytest.raises(BusError, match="64 KiB"):
        store.send(sender, recipient, "x" * 65537)
    assert store.inbox(recipient) == []


def test_mixed_acknowledgement_batch_rolls_back(tmp_path: Path) -> None:
    """A foreign message ID cannot consume an earlier valid item in the same batch."""
    store = Store(tmp_path / "bus.sqlite3")
    sender = store.register("claude", "one", "/project")
    first = store.register("codex", "first", "/project")
    second = store.register("codex", "second", "/project")
    own_message = store.send(sender, first, "one")
    foreign_message = store.send(sender, second, "two")
    with pytest.raises(BusError, match="do not belong"):
        store.acknowledge(first, [own_message["id"], foreign_message["id"]])
    assert store.inbox(first) == [own_message]


def test_host_identity_uses_native_session_id() -> None:
    """No explicit name or launch argument is required."""
    assert host_identity({"CODEX_THREAD_ID": "thread-1"}) == ("codex", "thread-1")
    assert host_identity({"CLAUDE_CODE_SESSION_ID": "session-2"}) == ("claude", "session-2")
    with pytest.raises(BusError, match="No host session ID"):
        host_identity({})


def test_codex_hook_registers_and_reports_pending_without_message_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Hook gives Codex address and count without elevating message body into context."""
    path = tmp_path / "bus.sqlite3"
    monkeypatch.setenv("LLM_BUS_DB", str(path))
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"session_id": "thread-1", "hook_event_name": "SessionStart"})),
    )
    assert main(["hook", "codex"]) == 0
    output = cast("dict[str, dict[str, str]]", json.loads(capsys.readouterr().out))
    assert "codex:thread-1" in output["hookSpecificOutput"]["additionalContext"]
    assert "uv run llm-bus inbox" in output["hookSpecificOutput"]["additionalContext"]

    store = Store(path)
    claude = store.register("claude", "session-2", "/project")
    store.send(claude, "codex:thread-1", "Untrusted message body")
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"session_id": "thread-1", "hook_event_name": "UserPromptSubmit"})),
    )
    assert main(["hook", "codex"]) == 0
    notice = cast("dict[str, dict[str, str]]", json.loads(capsys.readouterr().out))[
        "hookSpecificOutput"
    ]["additionalContext"]
    assert "1 pending message" in notice
    assert "Untrusted message body" not in notice


def test_claude_hook_uses_same_session_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Claude hook and shell environment resolve to one durable address."""
    monkeypatch.setenv("LLM_BUS_DB", str(tmp_path / "bus.sqlite3"))
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            json.dumps({"session_id": "claude-session", "hook_event_name": "SessionStart"})
        ),
    )
    assert main(["hook", "claude"]) == 0
    notice = capsys.readouterr().out
    assert "claude:claude-session" in notice
    assert "uv run llm-bus inbox" in notice
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-session")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    assert main(["whoami"]) == 0
    assert json.loads(capsys.readouterr().out) == {"address": "claude:claude-session"}
