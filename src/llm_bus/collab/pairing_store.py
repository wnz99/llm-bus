"""Collaboration pairing store ownership."""

from __future__ import annotations

import base64
import os
from datetime import timedelta
from typing import TYPE_CHECKING, cast

from llm_bus.collab.invites import decode_invite
from llm_bus.collab.models import (
    INVITE_MINUTES,
    ApprovedJoin,
    CollabError,
    Grant,
)
from llm_bus.collab.policy import github_user, grant_values, now, parse_stamp, stamp

if TYPE_CHECKING:
    import sqlite3

    from llm_bus.collab.models import InviteRow


def create_invite(
    db: sqlite3.Connection, peer_user: str, choice: Grant, repository: str, session: str | None
) -> tuple[str, str]:
    """Record a one-time invite bound to one GitHub user; return (nonce, expires_at)."""
    nonce = base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("=")
    created = now()
    expires = stamp(created + timedelta(minutes=INVITE_MINUTES))
    db.execute(
        "INSERT INTO invites(nonce, peer_user, mode, full_until, review_replies, "
        "agent_replies, wake_on_answer, repository, session, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            nonce,
            github_user(peer_user),
            *grant_values(choice),
            repository,
            session,
            stamp(created),
            expires,
        ),
    )
    return nonce, expires


def claim_invite(
    db: sqlite3.Connection, nonce: str, peer_user: str
) -> tuple[Grant, str, str | None]:
    """Consume an unexpired invite for exactly its GitHub user; single use."""
    row = cast(
        "InviteRow | None",
        db.execute(
            "SELECT peer_user, mode, full_until, review_replies, repository, session, "
            "expires_at, used_at, agent_replies, wake_on_answer FROM invites "
            "WHERE nonce = ?",
            (nonce,),
        ).fetchone(),
    )
    if row is None:
        raise CollabError("Invite is unknown, already used, or for another user")
    invited, mode, full_until, review, repository, session, expires_at, used_at = row[:8]
    if used_at is not None or invited != peer_user:
        raise CollabError("Invite is unknown, already used, or for another user")
    if parse_stamp(expires_at) <= now():
        raise CollabError("Invite has expired")
    db.execute("UPDATE invites SET used_at = ? WHERE nonce = ?", (stamp(now()), nonce))
    choice = Grant(mode, full_until, bool(review), bool(row[8]), bool(row[9]))
    return choice, repository, session


def invite_is_open(db: sqlite3.Connection, nonce: str, peer_user: str) -> bool:
    row = cast(
        "tuple[str, str] | None",
        db.execute(
            "SELECT peer_user, expires_at FROM invites WHERE nonce = ? AND used_at IS NULL",
            (nonce,),
        ).fetchone(),
    )
    return row is not None and row[0] == peer_user and parse_stamp(row[1]) > now()


def add_join(db: sqlite3.Connection, invite: str, repository: str, session: str | None) -> int:
    decode_invite(invite)
    cursor = db.execute(
        "INSERT INTO joins(invite, repository, session, status, created_at) "
        "VALUES (?, ?, ?, 'awaiting_owner', ?)",
        (invite.strip(), repository, session, stamp(now())),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("SQLite did not return a join ID")
    return cursor.lastrowid


def join(db: sqlite3.Connection, join_id: int) -> tuple[str, str, str | None, str, str | None]:
    """Return (invite, repository, session, status, detail)."""
    row = cast(
        "tuple[str, str, str | None, str, str | None] | None",
        db.execute(
            "SELECT invite, repository, session, status, detail FROM joins WHERE id = ?",
            (join_id,),
        ).fetchone(),
    )
    if row is None:
        raise CollabError("Unknown join request")
    return row


def approve_join(db: sqlite3.Connection, join_id: int, choice: Grant) -> None:
    cursor = db.execute(
        "UPDATE joins SET status = 'approved', mode = ?, full_until = ?, "
        "review_replies = ?, agent_replies = ?, wake_on_answer = ? "
        "WHERE id = ? AND status = 'awaiting_owner'",
        (
            *grant_values(choice),
            join_id,
        ),
    )
    if cursor.rowcount != 1:
        raise CollabError("Join request is not awaiting approval")


def approved_joins(db: sqlite3.Connection) -> list[ApprovedJoin]:
    rows = cast(
        "list[tuple[int, str, str, str | None, str, str | None, int, int, int]]",
        db.execute(
            "SELECT id, invite, repository, session, mode, full_until, review_replies, "
            "agent_replies, wake_on_answer FROM joins WHERE status = 'approved' "
            "ORDER BY id"
        ).fetchall(),
    )
    return [
        ApprovedJoin(r[0], r[1], r[2], r[3], Grant(r[4], r[5], bool(r[6]), bool(r[7]), bool(r[8])))
        for r in rows
    ]


def finish_join(db: sqlite3.Connection, join_id: int, status: str, detail: str) -> None:
    db.execute("UPDATE joins SET status = ?, detail = ? WHERE id = ?", (status, detail, join_id))
