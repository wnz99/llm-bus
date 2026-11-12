"""Recipient-recorded work state for local coordination directions."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from llm_bus.errors import BusError
from llm_bus.reports.lifecycle import TRANSITION_CONFLICT, transition_conflicts
from llm_bus.reports.protocol import MAX_REVISION, parse_report, validate_status

if TYPE_CHECKING:
    from sqlite3 import Connection

WORK_OPERATIONS = ("assign", "review", "reprioritize")
NOT_RECIPIENT = "Report requires a received local coordination direction or an imported message"
UNACKNOWLEDGED = "Acknowledge the direction before reporting its state"
TABLE = "coordination_work_reports"


def create_tables(db: Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS coordination_work_reports ("
        "message_id INTEGER PRIMARY KEY REFERENCES messages(id), "
        "revision INTEGER NOT NULL CHECK(revision>0), status TEXT NOT NULL, "
        "reason TEXT, reported_at TEXT NOT NULL)"
    )


def imported(db: Connection, message_id: int) -> bool:
    return (
        db.execute("SELECT 1 FROM remote_imports WHERE local_id=?", (message_id,)).fetchone()
        is not None
    )


def snapshot(db: Connection, message_id: int) -> dict[str, object] | None:
    row = cast(
        "tuple[int, str, str | None, str] | None",
        db.execute(
            "SELECT revision,status,reason,reported_at FROM coordination_work_reports "
            "WHERE message_id=?",
            (message_id,),
        ).fetchone(),
    )
    if row is None:
        return None
    status, reason = validate_status(row[1], row[2])
    return {
        "message_id": message_id,
        "revision": row[0],
        "status": status,
        "reason": reason,
        "reported_at": row[3],
    }


def record(
    db: Connection, message_id: int, recipient: str, status: object, reason: object
) -> dict[str, object]:
    """Record state; the caller already holds a write transaction."""
    selected, why = validate_status(status, reason)
    row = cast(
        "tuple[str, str | None] | None",
        db.execute(
            "SELECT m.recipient,m.acknowledged_at FROM coordination_messages c "
            "JOIN messages m ON m.id=c.message_id WHERE c.message_id=? "
            "AND NOT EXISTS (SELECT 1 FROM remote_imports WHERE local_id=m.id) "
            "AND NOT EXISTS (SELECT 1 FROM remote_outbox WHERE local_id=m.id)",
            (message_id,),
        ).fetchone(),
    )
    if row is None or row[0] != recipient:
        raise BusError(NOT_RECIPIENT)
    if row[1] is None:
        raise BusError(UNACKNOWLEDGED)
    prior = snapshot(db, message_id)
    if prior is not None and prior["status"] == selected and prior["reason"] == why:
        return prior
    if transition_conflicts(cast("str | None", prior["status"]) if prior else None, selected):
        raise BusError(TRANSITION_CONFLICT)
    revision = cast("int", prior["revision"]) + 1 if prior else 1
    if revision > MAX_REVISION:
        raise BusError("Work report revision limit reached")
    db.execute(
        "INSERT INTO coordination_work_reports VALUES (?,?,?,?,"
        "strftime('%Y-%m-%dT%H:%M:%fZ','now')) ON CONFLICT(message_id) DO UPDATE SET "
        "revision=excluded.revision,status=excluded.status,reason=excluded.reason,"
        "reported_at=excluded.reported_at",
        (message_id, revision, selected, why),
    )
    return cast("dict[str, object]", snapshot(db, message_id))


def work_report(
    db: Connection, tables: set[str], message_id: int, *, remote_id: str | None, imported: bool
) -> object:
    """Read-only work state; tolerates tables missing from older schemas."""
    # pylint: disable=redefined-outer-name  # keyword name is the planned interface
    if remote_id is not None and not imported:
        row = (
            cast(
                "tuple[str] | None",
                db.execute(
                    "SELECT payload FROM remote_reports WHERE id=?", (remote_id,)
                ).fetchone(),
            )
            if "remote_reports" in tables
            else None
        )
        return parse_report(cast("object", json.loads(row[0])), remote_id) if row else None
    if imported or TABLE not in tables:
        return None
    return snapshot(db, message_id)
