"""Lifecycle support for advisory resource locks."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from llm_bus.locks.models import (
    CATALOG_IDLE_DAYS,
    DEFAULT_TTL_MINUTES,
    NUDGE_INTERVAL_MINUTES,
    Key,
    Lock,
    Notice,
    Request,
    Waiter,
)
from llm_bus.locks.notices import check_body, grant_body, request_body
from llm_bus.mailbox.agents import expire_idle_agents

if TYPE_CHECKING:
    import sqlite3


def read_lock(db: sqlite3.Connection, key: Key) -> Lock | None:
    row = cast(
        "tuple[str, str | None, str, str, str, str | None] | None",
        db.execute(
            "SELECT holder,reason,acquired_at,expires_at,check_at,checked_at FROM resource_locks "
            "WHERE scope=? AND resource=?",
            key,
        ).fetchone(),
    )
    if row is None:
        return None
    waiters = [
        Waiter(waiter=item[0], reason=item[1], requested_at=item[2], nudged_at=item[3])
        for item in cast(
            "list[tuple[str, str | None, str, str | None]]",
            db.execute(
                "SELECT waiter,reason,requested_at,nudged_at FROM resource_lock_waiters "
                "WHERE scope=? AND resource=? ORDER BY requested_at,rowid",
                key,
            ).fetchall(),
        )
    ]
    return Lock(
        resource=key.resource,
        scope=key.scope,
        holder=row[0],
        reason=row[1],
        acquired_at=row[2],
        expires_at=row[3],
        check_at=row[4],
        checked_at=row[5],
        waiters=waiters,
    )


def reap(db: sqlite3.Connection) -> list[Notice]:
    """Pass lapsed or abandoned locks to their first waiter, then raise any due checks."""
    expire_idle_agents(db)
    db.execute(
        "DELETE FROM resource_lock_waiters WHERE waiter IN "
        "(SELECT address FROM agents WHERE ended_at IS NOT NULL)"
    )
    stale = [
        (Key(row[0], row[1]), row[2])
        for row in cast(
            "list[tuple[str, str, str]]",
            db.execute(
                "SELECT scope,resource,holder FROM resource_locks "
                "WHERE expires_at <= strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "OR holder IN (SELECT address FROM agents WHERE ended_at IS NOT NULL)"
            ).fetchall(),
        )
    ]
    notices: list[Notice] = []
    for key, holder in stale:
        notices.extend(hand_off(db, key, holder))
    notices.extend(due_checks(db))
    db.execute(
        "DELETE FROM resource_catalog WHERE last_used_at <= "
        "strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?) AND NOT EXISTS (SELECT 1 FROM resource_locks "
        "l WHERE l.scope=resource_catalog.scope AND l.resource=resource_catalog.resource)",
        (f"-{CATALOG_IDLE_DAYS} days",),
    )
    return notices


def due_checks(db: sqlite3.Connection) -> list[Notice]:
    """Ask each holder whose estimate has passed, once per estimate, in its own name."""
    due = [
        Key(row[0], row[1])
        for row in cast(
            "list[tuple[str, str]]",
            db.execute(
                "SELECT scope,resource FROM resource_locks "
                "WHERE check_at <= strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "AND (checked_at IS NULL OR checked_at < check_at)"
            ).fetchall(),
        )
    ]
    notices: list[Notice] = []
    for key in due:
        db.execute(
            "UPDATE resource_locks SET checked_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE scope=? AND resource=?",
            key,
        )
        lock = read_lock(db, key)
        if lock is not None:
            notices.append(Notice(lock["holder"], check_body(lock), sender=lock["holder"]))
    return notices


def hand_off(db: sqlite3.Connection, key: Key, previous: str) -> list[Notice]:
    """Grant the resource to the earliest live waiter, which must then set its own estimate."""
    first = cast(
        "tuple[str, str | None] | None",
        db.execute(
            "SELECT w.waiter,w.reason FROM resource_lock_waiters w JOIN agents a "
            "ON a.address=w.waiter WHERE w.scope=? AND w.resource=? AND a.ended_at IS NULL "
            "ORDER BY w.requested_at,w.rowid LIMIT 1",
            key,
        ).fetchone(),
    )
    if first is None:
        db.execute("DELETE FROM resource_locks WHERE scope=? AND resource=?", key)
        return []
    # Until the new holder sets --check, the grant message itself is the first ask.
    db.execute(
        "UPDATE resource_locks SET holder=?,reason=?,"
        "acquired_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),"
        "expires_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?),"
        "check_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),"
        "checked_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
        "WHERE scope=? AND resource=?",
        (first[0], first[1], f"+{DEFAULT_TTL_MINUTES} minutes", *key),
    )
    db.execute(
        "DELETE FROM resource_lock_waiters WHERE scope=? AND resource=? AND waiter=?",
        (*key, first[0]),
    )
    lock = read_lock(db, key)
    if lock is None:
        raise RuntimeError("Handed-off lock could not be read")
    return [Notice(first[0], grant_body(lock, previous))]


def enqueue(
    agent_address: str,
    db: sqlite3.Connection,
    key: Key,
    holder: str,
    request: Request,
) -> list[Notice]:
    reason = request.reason
    db.execute(
        "INSERT INTO resource_lock_waiters(scope,resource,waiter,reason,requested_at) "
        "VALUES (?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) "
        "ON CONFLICT(scope,resource,waiter) DO UPDATE SET "
        "reason=COALESCE(excluded.reason,reason)",
        (*key, agent_address, reason),
    )
    if not request.notify:
        return []
    due = cast(
        "tuple[int]",
        db.execute(
            "SELECT nudged_at IS NULL OR nudged_at <= "
            "strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?) FROM resource_lock_waiters "
            "WHERE scope=? AND resource=? AND waiter=?",
            (f"-{NUDGE_INTERVAL_MINUTES} minutes", *key, agent_address),
        ).fetchone(),
    )[0]
    if not due:
        return []
    db.execute(
        "UPDATE resource_lock_waiters SET nudged_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
        "WHERE scope=? AND resource=? AND waiter=?",
        (*key, agent_address),
    )
    lock = read_lock(db, key)
    if lock is None:
        raise RuntimeError("Contended lock could not be read")
    stored = next(
        (item["reason"] for item in lock["waiters"] if item["waiter"] == agent_address),
        reason,
    )
    return [Notice(holder, request_body(lock, agent_address, stored))]
