"""Legacy mailbox backfills, applied within the schema owner transaction."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable
    from sqlite3 import Connection


def _backfill_message_scopes(db: Connection, resolve_scope: Callable[[str], str]) -> None:
    for select_query, update_query in (
        (
            "SELECT DISTINCT sender_cwd FROM messages WHERE sender_cwd IS NOT NULL",
            "UPDATE messages SET sender_scope = ? WHERE sender_cwd = ?",
        ),
        (
            "SELECT DISTINCT recipient_cwd FROM messages WHERE recipient_cwd IS NOT NULL",
            "UPDATE messages SET recipient_scope = ? WHERE recipient_cwd = ?",
        ),
    ):
        folders = cast(
            "list[tuple[str]]",
            db.execute(select_query).fetchall(),
        )
        for (folder,) in folders:
            db.execute(update_query, (resolve_scope(folder), folder))


def _migrate_agents(db: Connection, resolve_scope: Callable[[str], str]) -> None:
    agent_columns = {
        row[1]
        for row in cast(
            "list[tuple[int, str, str, int, object, int]]",
            db.execute("PRAGMA table_info(agents)").fetchall(),
        )
    }
    if "ended_at" not in agent_columns:
        db.execute("ALTER TABLE agents ADD COLUMN ended_at TEXT")
    if "messaging_socket" not in agent_columns:
        db.execute("ALTER TABLE agents ADD COLUMN messaging_socket TEXT")
    if "scope" not in agent_columns:
        db.execute("ALTER TABLE agents ADD COLUMN scope TEXT")
        projects = cast(
            "list[tuple[str]]",
            db.execute("SELECT DISTINCT project FROM agents").fetchall(),
        )
        for (project,) in projects:
            db.execute(
                "UPDATE agents SET scope = ? WHERE project = ?",
                (resolve_scope(project), project),
            )


def migrate(db: Connection, resolve_scope: Callable[[str], str]) -> None:
    _migrate_agents(db, resolve_scope)
    columns = message_columns(db)
    if "sender_cwd" not in columns:
        db.execute("ALTER TABLE messages ADD COLUMN sender_cwd TEXT")
    if "recipient_cwd" not in columns:
        db.execute("ALTER TABLE messages ADD COLUMN recipient_cwd TEXT")
    for column in (
        "sender_scope",
        "recipient_scope",
        "wake_status",
        "wake_via",
        "wake_reason",
        "wake_checked_at",
    ):
        if column not in columns:
            db.execute(f"ALTER TABLE messages ADD COLUMN {column} TEXT")
    if "sender_scope" not in columns or "recipient_scope" not in columns:
        _backfill_message_scopes(db, resolve_scope)
    db.execute("CREATE INDEX IF NOT EXISTS messages_sender_folder ON messages(sender_cwd, id)")
    db.execute("CREATE INDEX IF NOT EXISTS msg_recipient_folder ON messages(recipient_cwd, id)")
    db.execute("CREATE INDEX IF NOT EXISTS messages_sender_scope ON messages(sender_scope, id)")
    db.execute(
        "CREATE INDEX IF NOT EXISTS messages_recipient_scope ON messages(recipient_scope, id)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS remote_imports_local_id ON remote_imports(local_id)")


def create_lock_catalog(db: Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS resource_catalog (scope TEXT NOT NULL, resource TEXT NOT NULL, "
        "description TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL, "
        "last_used_at TEXT NOT NULL, PRIMARY KEY(scope,resource))"
    )
    db.execute(
        "INSERT OR IGNORE INTO resource_catalog"
        "(scope,resource,description,created_by,created_at,last_used_at) "
        "SELECT scope,resource,COALESCE(reason,'(no description)'),holder,acquired_at,acquired_at "
        "FROM resource_locks"
    )


def add_host_process(db: Connection) -> None:
    """Record each session's host process, and why a lock left a holder against its will."""
    columns = {
        row[1]
        for row in cast(
            "list[tuple[int, str, str, int, object, int]]",
            db.execute("PRAGMA table_info(agents)").fetchall(),
        )
    }
    if "host_pid" not in columns:
        db.execute("ALTER TABLE agents ADD COLUMN host_pid INTEGER")
    if "host_started" not in columns:
        db.execute("ALTER TABLE agents ADD COLUMN host_started TEXT")
    db.execute(
        "CREATE TABLE IF NOT EXISTS resource_lock_releases (id INTEGER PRIMARY KEY, "
        "scope TEXT NOT NULL, resource TEXT NOT NULL, holder TEXT NOT NULL, "
        "cause TEXT NOT NULL CHECK(cause IN ('holder_exited','forced')), actor TEXT, "
        "reason TEXT, granted_to TEXT, "
        "released_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')))"
    )


def message_columns(db: Connection) -> set[str]:
    return {
        row[1]
        for row in cast(
            "list[tuple[int, str, str, int, object, int]]",
            db.execute("PRAGMA table_info(messages)").fetchall(),
        )
    }
