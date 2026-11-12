"""Lifecycle support for advisory resource locks."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from llm_bus.hosts.processes import HostProcess, exited
from llm_bus.hosts.sessions import is_subagent
from llm_bus.locks.models import (
    CATALOG_IDLE_DAYS,
    DEFAULT_TTL_MINUTES,
    HOLDER_EXITED,
    NUDGE_INTERVAL_MINUTES,
    Key,
    Lock,
    Notice,
    Request,
    Waiter,
)
from llm_bus.locks.notices import RELEASED_OR_LAPSED, check_body, grant_body, request_body
from llm_bus.mailbox.agents import end_agent, expire_idle_agents

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
    notices = reap_exited(db)
    waiters = cast(
        "list[tuple[str]]",
        db.execute("SELECT DISTINCT waiter FROM resource_lock_waiters").fetchall(),
    )
    for (waiter,) in waiters:
        if is_subagent(waiter):
            db.execute("DELETE FROM resource_lock_waiters WHERE waiter=?", (waiter,))
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


def reap_exited(db: sqlite3.Connection) -> list[Notice]:
    """End lock holders and waiters whose host process exited, and pass their locks on.

    A killed host fires no close hook, so its session would otherwise hold until the lease
    ends. Sessions without a recorded process, or an unreadable process table, are left alone.
    """
    rows = cast(
        "list[tuple[str, int, str]]",
        db.execute(
            "SELECT address,host_pid,host_started FROM agents WHERE ended_at IS NULL "
            "AND host_pid IS NOT NULL AND (address IN (SELECT holder FROM resource_locks) "
            "OR address IN (SELECT waiter FROM resource_lock_waiters))"
        ).fetchall(),
    )
    recorded = {agent_address: HostProcess(pid, started) for agent_address, pid, started in rows}
    gone = exited(list(recorded.values())) if recorded else None
    if not gone:
        return []
    ended = [agent_address for agent_address, host in recorded.items() if host in gone]
    for agent_address in ended:
        end_agent(db, agent_address)
    notices: list[Notice] = []
    for agent_address in ended:
        held = cast(
            "list[tuple[str, str]]",
            db.execute(
                "SELECT scope,resource FROM resource_locks WHERE holder=?", (agent_address,)
            ).fetchall(),
        )
        for scope, resource in held:
            notices.extend(release_from(db, Key(scope, resource), agent_address, HOLDER_EXITED))
    return notices


def release_from(  # noqa: PLR0913 - audit fields
    db: sqlite3.Connection,
    key: Key,
    holder: str,
    cause: str,
    *,
    actor: str | None = None,
    reason: str | None = None,
) -> list[Notice]:
    """Pass a lock on without its holder's consent, and record who, why, and to whom."""
    # pylint: disable=too-many-arguments
    how = (
        "exited without releasing it"
        if cause == HOLDER_EXITED
        else f"had it force-released by {actor}"
    )
    notices = hand_off(db, key, holder, how)
    lock = read_lock(db, key)
    db.execute(
        "INSERT INTO resource_lock_releases(scope,resource,holder,cause,actor,reason,granted_to) "
        "VALUES (?,?,?,?,?,?,?)",
        (*key, holder, cause, actor, reason, lock["holder"] if lock else None),
    )
    return notices


def hand_off(
    db: sqlite3.Connection, key: Key, previous: str, how: str = RELEASED_OR_LAPSED
) -> list[Notice]:
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
    return [Notice(first[0], grant_body(lock, previous, how))]


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
    if not request.notify or is_subagent(holder):
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
