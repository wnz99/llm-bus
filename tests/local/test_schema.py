"""schema."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, cast

import pytest

from llm_bus.mailbox.models import SenderContext
from llm_bus.mailbox.schema import SCHEMA_VERSION, ensure_schema
from llm_bus.mailbox.store import Store

from .support import linked_worktree

if TYPE_CHECKING:
    from pathlib import Path


def test_existing_git_worktree_agents_get_repository_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, worktree, _git = linked_worktree(tmp_path)
    nested = worktree / "nested"
    nested.mkdir()
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE agents (address TEXT PRIMARY KEY, kind TEXT, project TEXT, "
            "last_seen TEXT NOT NULL, ended_at TEXT)"
        )
        db.executemany(
            "INSERT INTO agents VALUES (?, 'codex', ?, 'now', NULL)",
            [("codex:main", str(repo)), ("codex:linked", str(nested))],
        )

    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(worktree))
    migrated = Store(path)
    assert {agent["address"] for agent in migrated.agents(str(repo))} == {
        "codex:main",
        "codex:linked",
    }
    sent = migrated.send(SenderContext("codex", "main", str(repo)), "codex:linked", "hello")
    assert sent["recipient_cwd"] == str(nested)


def test_reply_schema_upgrade_preserves_legacy_roots(tmp_path: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    store.register("claude", "two", "/project")
    store.send(SenderContext("codex", "one", "/project"), "claude:two", "retained")
    with store.transaction() as db:
        db.execute("DROP INDEX messages_reply_to")
        db.execute("ALTER TABLE messages DROP COLUMN reply_to")
        db.execute("PRAGMA user_version=3")
    reopened = Store(store.path)
    assert reopened.history(folder=None)[0]["reply_to"] is None
    with reopened.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE name='messages_reply_to'"
        ).fetchone()


def test_migration_reopen_skips_ddl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # pylint: disable=protected-access,clean-code-commented-out-code
    # Trace the real constructor connection to detect repeated migration statements.
    path = tmp_path / "migration.sqlite3"
    Store(path)
    statements: list[str] = []
    original = Store._connect  # pyright: ignore[reportPrivateUsage]

    def connect(store: Store) -> sqlite3.Connection:
        db = original(store)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(Store, "_connect", connect)
    Store(path)
    assert not any(
        statement.startswith(("CREATE", "ALTER", "BEGIN IMMEDIATE", "PRAGMA table_info"))
        for statement in statements
    )


def test_migration_rollback_and_future_version(tmp_path: Path) -> None:
    path = tmp_path / "legacy-rollback.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE agents (address TEXT PRIMARY KEY, kind TEXT, project TEXT, last_seen TEXT)"
        )
        db.execute("INSERT INTO agents VALUES ('codex:old', 'codex', '/repo', 'now')")
        db.commit()

        def unavailable_scope(_folder: str) -> str:
            raise OSError("scope unavailable")

        with pytest.raises(OSError, match="scope unavailable"):
            ensure_schema(db, unavailable_scope)
        assert db.execute("PRAGMA user_version").fetchone() == (0,)
        columns = cast(
            "list[tuple[int, str, str, int, object, int]]",
            db.execute("PRAGMA table_info(agents)").fetchall(),
        )
        assert [row[1] for row in columns] == [
            "address",
            "kind",
            "project",
            "last_seen",
        ]
        assert db.execute("SELECT name FROM sqlite_master WHERE name='messages'").fetchone() is None
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
        with pytest.raises(ValueError, match="newer"):
            ensure_schema(db, str)
        assert db.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION + 1,)


def test_migration_concurrent_openers_preserve_history(tmp_path: Path) -> None:
    path = tmp_path / "concurrent.sqlite3"
    with ThreadPoolExecutor(max_workers=2) as pool:
        stores = list(pool.map(Store, [path, path]))
    recipient = stores[0].register("codex", "recipient", "/project")
    message = stores[0].send(SenderContext("claude", "sender", "/project"), recipient, "retained")
    assert stores[1].inbox(recipient) == [message]
    with stores[1].transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
