"""Collaboration peer store ownership."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from llm_bus.collab.models import (
    MAX_STATUS_SUMMARY,
    PEER_COLUMNS,
    SESSION,
    CollabError,
    Grant,
    NewPeer,
    Peer,
    PeerRow,
)
from llm_bus.collab.policy import github_user, grant_values, now, stamp

if TYPE_CHECKING:
    import sqlite3


def _peer(row: PeerRow) -> Peer:
    return Peer(
        responder_context=row[16],
        status_summary=row[17],
        id=row[0],
        pair=row[1],
        github_user=row[2],
        repository=row[3],
        endpoint_id=row[4],
        ticket=row[5],
        mode=row[6],
        full_until=row[7],
        review_replies=bool(row[8]),
        their_grant=row[9],
        session=row[10],
        confirm_code=row[11],
        created_at=row[12],
        revoked_at=row[13],
        agent_replies=bool(row[14]),
        wake_on_answer=bool(row[15]),
    )


def add_peer(db: sqlite3.Connection, facts: NewPeer, choice: Grant) -> None:
    db.execute(
        "INSERT INTO peers(pair, github_user, repository, endpoint_id, ticket, "
        "key_fingerprint, mode, full_until, review_replies, agent_replies, wake_on_answer, "
        "their_grant, session, confirm_code, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            facts.pair,
            github_user(facts.github_user),
            facts.repository,
            facts.endpoint_id,
            facts.ticket,
            facts.key_fingerprint,
            *grant_values(choice),
            facts.their_grant,
            facts.session,
            facts.confirm_code,
            stamp(now()),
        ),
    )


def peers(db: sqlite3.Connection) -> list[Peer]:
    rows = cast(
        "list[PeerRow]",
        db.execute(
            f"SELECT {PEER_COLUMNS} FROM peers ORDER BY id"  # noqa: S608 - fixed columns
        ).fetchall(),
    )
    return [_peer(row) for row in rows]


def peer(db: sqlite3.Connection, peer_id: int) -> Peer | None:
    return next((p for p in peers(db) if p["id"] == peer_id), None)


def peer_by_pair(db: sqlite3.Connection, pair: str) -> Peer | None:
    return next((p for p in peers(db) if p["pair"] == pair), None)


def set_mode(db: sqlite3.Connection, peer_id: int, choice: Grant) -> None:
    db.execute(
        "UPDATE peers SET mode = ?, full_until = ?, review_replies = ?, agent_replies = ?, "
        "wake_on_answer = ? WHERE id = ?",
        (
            *grant_values(choice),
            peer_id,
        ),
    )


def set_responder_context(db: sqlite3.Connection, peer_id: int, context: str, summary: str) -> None:
    """Owner-selected context, separate from tool permissions and remote grants."""
    if context not in {"fork", "summary"}:
        raise CollabError("Responder context must be fork or summary")
    if len(summary) > MAX_STATUS_SUMMARY:
        raise CollabError(f"Status summary must be at most {MAX_STATUS_SUMMARY} characters")
    db.execute(
        "UPDATE peers SET responder_context = ?, status_summary = ? WHERE id = ?",
        (context, summary, peer_id),
    )


def set_session(db: sqlite3.Connection, peer_id: int, session: str) -> None:
    if not SESSION.fullmatch(session):
        raise CollabError("Invalid session ID")
    db.execute("UPDATE peers SET session = ? WHERE id = ?", (session, peer_id))


def set_ticket(db: sqlite3.Connection, peer_id: int, ticket: str) -> None:
    db.execute("UPDATE peers SET ticket = ? WHERE id = ?", (ticket, peer_id))


def revoke(db: sqlite3.Connection, peer_id: int) -> None:
    """Stop future delivery; stored history is retained, exposed content is not recalled."""
    db.execute(
        "UPDATE peers SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
        (stamp(now()), peer_id),
    )
    db.execute(
        "UPDATE messages SET state = 'cancelled' WHERE peer_id = ? AND direction = 'out' "
        "AND state IN ('queued', 'held')",
        (peer_id,),
    )
