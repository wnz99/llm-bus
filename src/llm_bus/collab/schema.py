"""Collaboration schema ownership."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import sqlite3

SCHEMA = """
    CREATE TABLE IF NOT EXISTS identity (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        github_user TEXT NOT NULL,
        signing_key TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS invites (
        nonce TEXT PRIMARY KEY,
        peer_user TEXT NOT NULL,
        mode TEXT NOT NULL,
        full_until TEXT,
        review_replies INTEGER NOT NULL,
        agent_replies INTEGER NOT NULL DEFAULT 0,
        wake_on_answer INTEGER NOT NULL DEFAULT 0,
        repository TEXT NOT NULL,
        session TEXT,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used_at TEXT
    );
    CREATE TABLE IF NOT EXISTS joins (
        id INTEGER PRIMARY KEY,
        invite TEXT NOT NULL,
        repository TEXT NOT NULL,
        session TEXT,
        mode TEXT,
        full_until TEXT,
        review_replies INTEGER NOT NULL DEFAULT 1,
        agent_replies INTEGER NOT NULL DEFAULT 0,
        wake_on_answer INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL,
        detail TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS peers (
        id INTEGER PRIMARY KEY,
        pair TEXT NOT NULL UNIQUE,
        github_user TEXT NOT NULL,
        repository TEXT NOT NULL,
        endpoint_id TEXT NOT NULL,
        ticket TEXT,
        key_fingerprint TEXT NOT NULL,
        mode TEXT NOT NULL,
        full_until TEXT,
        review_replies INTEGER NOT NULL,
        agent_replies INTEGER NOT NULL DEFAULT 0,
        wake_on_answer INTEGER NOT NULL DEFAULT 0,
        their_grant TEXT,
        session TEXT,
        confirm_code TEXT NOT NULL,
        created_at TEXT NOT NULL,
        revoked_at TEXT
    );
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY,
        peer_id INTEGER NOT NULL REFERENCES peers(id),
        direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
        kind TEXT NOT NULL CHECK (kind IN ('request', 'reply')),
        uid TEXT NOT NULL,
        in_reply_to TEXT,
        body TEXT NOT NULL,
        state TEXT NOT NULL,
        detail TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (peer_id, direction, uid)
    );
"""
# Columns added after the first release; existing databases gain them on open.
ADDED_COLUMNS = [
    (table, f"{name} INTEGER NOT NULL DEFAULT 0")
    for table in ("invites", "joins", "peers")
    for name in ("agent_replies", "wake_on_answer")
] + [
    ("peers", "responder_context TEXT NOT NULL DEFAULT 'fork'"),
    ("peers", "status_summary TEXT NOT NULL DEFAULT ''"),
    ("messages", "origin_session TEXT"),
]


def initialize(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)
    for table, column in ADDED_COLUMNS:
        info = cast("list[tuple[int, str]]", db.execute(f"PRAGMA table_info({table})").fetchall())
        names = {row[1] for row in info}
        if column.split()[0] not in names:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
    db.commit()
