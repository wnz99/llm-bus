"""Collaboration message store ownership."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import cast

from llm_bus.collab.models import (
    MAX_BODY_BYTES,
    MESSAGE_COLUMNS,
    CollabError,
    Message,
    MessageRow,
    NewMessage,
)
from llm_bus.collab.policy import now, stamp


def _message(row: MessageRow) -> Message:
    return Message(
        id=row[0],
        peer_id=row[1],
        direction=row[2],
        kind=row[3],
        uid=row[4],
        in_reply_to=row[5],
        body=row[6],
        state=row[7],
        detail=row[8],
        created_at=row[9],
    )


def add_message(db: sqlite3.Connection, peer_id: int, new: NewMessage) -> int | None:
    """Insert a message ID; return None for a duplicate UID from the same peer."""
    if not new.body.strip():
        raise CollabError("Message body cannot be empty")
    if len(new.body.encode("utf-8")) > MAX_BODY_BYTES:
        raise CollabError("Message body exceeds 64 KiB")
    try:
        cursor = db.execute(
            "INSERT INTO messages(peer_id, direction, kind, uid, in_reply_to, body, state, "
            "detail, created_at, origin_session) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                peer_id,
                new.direction,
                new.kind,
                new.uid,
                new.in_reply_to,
                new.body,
                new.state,
                new.detail,
                stamp(now()),
                new.origin_session,
            ),
        )
    except sqlite3.IntegrityError:
        return None
    if cursor.lastrowid is None:
        raise RuntimeError("SQLite did not return a message ID")
    return cursor.lastrowid


def question_session(db: sqlite3.Connection, peer_id: int, uid: str) -> str | None:
    """Immutable local owner of an outgoing question; legacy questions have none."""
    row = cast(
        "tuple[str | None] | None",
        db.execute(
            "SELECT origin_session FROM messages WHERE peer_id=? AND uid=? "
            "AND direction='out' AND kind='request'",
            (peer_id, uid),
        ).fetchone(),
    )
    return row[0] if row else None


def message(db: sqlite3.Connection, message_id: int) -> Message:
    row = cast(
        "MessageRow | None",
        db.execute(
            f"SELECT {MESSAGE_COLUMNS} FROM messages WHERE id = ?",  # noqa: S608
            (message_id,),
        ).fetchone(),
    )
    if row is None:
        raise CollabError(f"Unknown collaboration message {message_id}")
    return _message(row)


def messages(
    db: sqlite3.Connection, *, state: str | None = None, limit: int = 200
) -> list[Message]:
    query = f"SELECT {MESSAGE_COLUMNS} FROM messages"  # noqa: S608
    parameters: tuple[object, ...] = ()
    if state is not None:
        query += " WHERE state = ?"
        parameters = (state,)
    rows = cast(
        "list[MessageRow]",
        db.execute(f"{query} ORDER BY id DESC LIMIT ?", (*parameters, limit)).fetchall(),
    )
    return [_message(row) for row in rows]


def set_state(
    db: sqlite3.Connection, message_id: int, state: str, detail: str | None = None
) -> None:
    db.execute(
        "UPDATE messages SET state = ?, detail = ? WHERE id = ?",
        (state, detail, message_id),
    )


def transition(db: sqlite3.Connection, message_id: int, expected: str, state: str) -> bool:
    """Change state only from an expected state, so one human action wins."""
    cursor = db.execute(
        "UPDATE messages SET state = ? WHERE id = ? AND state = ?",
        (state, message_id, expected),
    )
    return cursor.rowcount == 1


def first_answer(db: sqlite3.Connection, peer_id: int, answer: Message) -> bool:
    """True when an incoming reply is the first one to a question this side sent."""
    asked = cast(
        "tuple[int] | None",
        db.execute(
            "SELECT 1 FROM messages WHERE peer_id = ? AND direction = 'out' "
            "AND kind = 'request' AND uid = ?",
            (peer_id, answer["in_reply_to"]),
        ).fetchone(),
    )
    earlier = cast(
        "tuple[int] | None",
        db.execute(
            "SELECT 1 FROM messages WHERE peer_id = ? AND direction = 'in' "
            "AND kind = 'reply' AND in_reply_to = ? AND id < ?",
            (peer_id, answer["in_reply_to"], answer["id"]),
        ).fetchone(),
    )
    return asked is not None and earlier is None


def recent_auto_replies(db: sqlite3.Connection, peer_id: int) -> int:
    since = stamp(now() - timedelta(hours=1))
    row = cast(
        "tuple[int]",
        db.execute(
            "SELECT COUNT(*) FROM messages WHERE peer_id = ? AND direction = 'in' "
            "AND kind = 'request' AND state IN ('responding', 'answered', 'held_reply') "
            "AND created_at >= ?",
            (peer_id, since),
        ).fetchone(),
    )
    return row[0]
