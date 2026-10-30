"""status."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, cast

import pytest

from llm_bus.cli.app import main
from llm_bus.mailbox.models import SenderContext
from llm_bus.mailbox.store import Store
from llm_bus.wake.dispatch import dispatch_pending

if TYPE_CHECKING:
    from pathlib import Path


def test_message_status_read_only_without_identity_or_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    recipient = store.register("claude", "two", "/project")
    sent = store.send(SenderContext("codex", "one", "/project"), recipient, "private body")
    monkeypatch.setenv("LLM_BUS_DB", str(store.path))
    monkeypatch.setenv("LLM_BUS_CONFIG", str(tmp_path / "absent.toml"))

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Status attempted registration, migration, network, or wake")

    monkeypatch.setattr("llm_bus.cli.app.host_identity", forbidden)
    monkeypatch.setattr("llm_bus.mailbox.connection.ensure_schema", forbidden)
    monkeypatch.setattr("llm_bus.cli.commands.require_config", forbidden)
    monkeypatch.setattr("llm_bus.wake.dispatch.wake", forbidden)
    with sqlite3.connect(store.path) as db:
        before = list(db.iterdump())
    assert main(["status", str(sent["id"])]) == 0
    result = cast("dict[str, object]", json.loads(capsys.readouterr().out))
    assert result["transport"] == {
        "status": "stored_local",
        "source": "local",
        "observed_at": sent["sent_at"],
    }
    assert "private body" not in json.dumps(result)
    with sqlite3.connect(store.path) as db:
        assert list(db.iterdump()) == before
    for message_id in (0, -1, 999, 2**100):
        assert main(["status", str(message_id)]) == 1
    assert set(result) == {
        "id",
        "remote_id",
        "sender",
        "recipient",
        "reply_to",
        "transport",
        "wake",
        "acknowledged_at",
        "work_report",
        "synchronization",
    }
    missing = tmp_path / "missing" / "bus.sqlite3"
    monkeypatch.setenv("LLM_BUS_DB", str(missing))
    assert main(["status", "1"]) == 1
    assert not missing.parent.exists()


def test_message_status_legacy_missing_metadata(tmp_path: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    recipient = store.register("claude", "two", "/project")
    sent = store.send(SenderContext("codex", "one", "/project"), recipient, "legacy")
    with store.transaction() as db:
        for table in ("remote_sync_work", "wake_dispatch", "wake_coverage", "remote_reports"):
            db.execute(f"DROP TABLE {table}")
        db.execute("DROP INDEX messages_reply_to")
        db.execute("ALTER TABLE messages DROP COLUMN reply_to")
    result = store.message_status(sent["id"])
    assert result["reply_to"] is None
    assert result["wake"] == dict.fromkeys(
        ("status", "via", "reason", "checked_at", "coverage_status", "retry_at")
    )
    assert result["work_report"] is None
    assert cast("dict[str, object]", result["synchronization"])["stale"] is True


def test_message_status_sanitizes_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    recipient = store.register("claude", "two", "/project")
    sent = store.send(SenderContext("codex", "one", "/project"), recipient, "diagnose")
    with store.transaction() as db:
        db.execute(
            "UPDATE messages SET wake_status='failed',wake_via='codex queue',wake_reason=?",
            ("\x1b[31m secret /private/credential.token HTTP body",),
        )
    result = store.message_status(sent["id"])
    assert cast("dict[str, object]", result["wake"])["reason"] == "wake_failed:codex_queue"
    assert "secret" not in json.dumps(result)
    assert "credential" not in json.dumps(result)

    def unsafe_adapter(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"status": "failed", "via": "codex queue", "reason": "\x1b[31m secret credential"}

    monkeypatch.setattr("llm_bus.wake.dispatch.wake", unsafe_adapter)
    dispatch_pending(store, recipient)
    assert store.history(folder=None)[0]["wake_reason"] == "wake_failed:codex_queue"


def test_message_status_never_borrows_other_message_coverage(tmp_path: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    recipient = store.register("claude", "two", "/project")
    sender = SenderContext("codex", "one", "/project")
    first = store.send(sender, recipient, "first")
    second = store.send(sender, recipient, "second")
    with store.transaction() as db:
        db.execute(
            "INSERT INTO wake_coverage VALUES (?,'test','requested','2026-10-23T00:00:00Z')",
            (first["id"],),
        )
    wake = cast("dict[str, object]", store.message_status(second["id"])["wake"])
    assert wake["coverage_status"] is None
    assert wake["status"] is None
