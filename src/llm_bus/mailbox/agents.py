from __future__ import annotations

from typing import TYPE_CHECKING, cast

from llm_bus.hosts.sessions import is_subagent
from llm_bus.mailbox.models import (
    Agent,
    address,
)
from llm_bus.routing import routing_scope

if TYPE_CHECKING:
    import sqlite3

SESSION_IDLE_HOURS = 24


def expire_idle_agents(db: sqlite3.Connection) -> None:
    """Close sessions idle for a day so their messages stop waking and their locks lapse."""
    db.execute(
        "UPDATE agents SET ended_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
        "WHERE ended_at IS NULL AND last_seen <= "
        "strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?)",
        (f"-{SESSION_IDLE_HOURS} hours",),
    )


def register(
    db: sqlite3.Connection,
    kind: str,
    session_id: str,
    project: str,
    *,
    messaging_socket: str | None = None,
) -> str:
    """Upsert session address without deleting its pending messages."""
    scope = routing_scope(project)
    agent_address = upsert_agent(db, kind, session_id, project, scope)
    db.execute(
        "UPDATE agents SET messaging_socket = ? WHERE address = ?",
        (messaging_socket if kind == "claude" else None, agent_address),
    )
    return agent_address


def read_messaging_socket(db: sqlite3.Connection, agent_address: str) -> str | None:
    """Read the native endpoint reported by the recipient's own hook."""
    row = cast(
        "tuple[str | None] | None",
        db.execute(
            "SELECT messaging_socket FROM agents WHERE address = ? AND ended_at IS NULL",
            (agent_address,),
        ).fetchone(),
    )
    return row[0] if row is not None else None


def upsert_agent(
    db: sqlite3.Connection, kind: str, session_id: str, project: str, scope: str
) -> str:
    agent_address = address(kind, session_id)
    db.execute(
        "INSERT INTO agents(address, kind, project, scope, last_seen, ended_at) "
        "VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), NULL) "
        "ON CONFLICT(address) DO UPDATE SET project = excluded.project, "
        "scope = excluded.scope, "
        "last_seen = excluded.last_seen, ended_at = NULL",
        (agent_address, kind, project, scope),
    )
    return agent_address


def end_session(db: sqlite3.Connection, kind: str, session_id: str) -> None:
    """Mark a registered session closed without consuming its pending messages."""
    db.execute(
        "UPDATE agents SET ended_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
        "messaging_socket = NULL "
        "WHERE address = ?",
        (address(kind, session_id),),
    )


def agents(db: sqlite3.Connection, project: str | None = None) -> list[Agent]:
    """List sessions active within the last day; presence is not guaranteed."""
    clauses = ["ended_at IS NULL"]
    parameters: list[str] = []
    if project is not None:
        clauses.append("scope = ?")
        parameters.append(routing_scope(project))
    return [
        agent
        for agent in _read_agents(db, clauses, parameters)
        if not is_subagent(agent["address"])
    ]


def all_agents(db: sqlite3.Connection) -> list[Agent]:
    """List all registered sessions, including ended ones."""
    return _read_agents(db, [], [])


def _read_agents(db: sqlite3.Connection, clauses: list[str], parameters: list[str]) -> list[Agent]:
    expire_idle_agents(db)
    query = "SELECT address, kind, project, last_seen, ended_at FROM agents"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    rows = cast(
        "list[tuple[str, str, str, str, str | None]]",
        db.execute(query + " ORDER BY last_seen DESC", parameters).fetchall(),
    )
    return [
        Agent(address=row[0], kind=row[1], project=row[2], last_seen=row[3], ended_at=row[4])
        for row in rows
    ]


def is_ended(db: sqlite3.Connection, agent_address: str) -> bool:
    """Whether a session ended or has been idle for a day."""
    expire_idle_agents(db)
    row = cast(
        "tuple[str | None] | None",
        db.execute("SELECT ended_at FROM agents WHERE address = ?", (agent_address,)).fetchone(),
    )
    return row is not None and row[0] is not None


def guidance_needed(db: sqlite3.Connection, recipient: str, version: int, *, reset: bool) -> bool:
    """Remember only guidance format, never permissions or peer policy."""
    row = cast(
        "tuple[int] | None",
        db.execute("SELECT version FROM hook_guidance WHERE address=?", (recipient,)).fetchone(),
    )
    db.execute(
        "INSERT INTO hook_guidance(address,version) VALUES (?,?) "
        "ON CONFLICT(address) DO UPDATE SET version=excluded.version",
        (recipient, version),
    )
    return reset or row is None or row[0] != version
