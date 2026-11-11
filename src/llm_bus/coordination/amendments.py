"""Append-only objective amendments for the current coordinator generation."""

import hashlib
from datetime import UTC, datetime
from sqlite3 import Connection
from typing import cast
from uuid import uuid4

COLUMNS = "amendment_id,text,sha256,decision_id,recorded_by,recorded_at"


def append(
    db: Connection,
    record: dict[str, object],
    text: str,
    by: str,
    decision_id: str | None = None,
) -> dict[str, object]:
    """Store one amendment; callers validate the text and hold the transaction."""
    amendment: dict[str, object] = {
        "amendment_id": uuid4().hex,
        "text": text,
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "decision_id": decision_id,
        "recorded_by": by,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    db.execute(
        "INSERT INTO coordination_amendments(amendment_id,scope,project_id,generation,text,"
        "sha256,decision_id,recorded_by,recorded_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            amendment["amendment_id"],
            record["scope"],
            record["project_id"],
            record["generation"],
            text,
            amendment["sha256"],
            decision_id,
            by,
            amendment["recorded_at"],
        ),
    )
    return amendment


def history(db: Connection, record: dict[str, object]) -> list[dict[str, object]]:
    """Amendments of the current generation, oldest first."""
    cursor = db.execute(
        f"SELECT {COLUMNS} FROM coordination_amendments "  # noqa: S608 - fixed column list
        "WHERE scope=? AND project_id=? AND generation=? ORDER BY rowid",
        (record["scope"], record["project_id"], record["generation"]),
    )
    return [
        dict(zip((c[0] for c in cursor.description), row, strict=True))
        for row in cast("list[tuple[object, ...]]", cursor.fetchall())
    ]


def effective_objective(db: Connection, record: dict[str, object]) -> str:
    """Latest amendment text, else the objective stored when the project was created."""
    latest = history(db, record)
    return str(latest[-1]["text"]) if latest else str(record["objective"])
