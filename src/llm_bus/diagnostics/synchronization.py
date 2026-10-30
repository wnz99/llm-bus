"""Read-only synchronization scheduling projections and safe error categories."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from llm_bus.errors import BusError

if TYPE_CHECKING:
    import sqlite3

SYNC_READ_SECONDS = 30


SYNC_KINDS = frozenset({"upload", "status", "report_read", "report_submit", "receipt", "ack"})


SYNC_ERRORS = frozenset(
    {"unavailable", "not_allowed", "protocol_error", "rejected", "unsupported_reply_link"}  # pylint: disable=clean-code-business-policy-literal
)


SYNC_COLUMNS = (
    "attempts",
    "next_attempt_at",
    "last_attempt_at",
    "last_success_at",
    "error_code",
    "first_accepted_at",
    "polling_suspended_at",
)


SYNC_SNAPSHOT_QUERY = (
    "SELECT attempts,next_attempt_at,last_attempt_at,last_success_at,error_code,"
    "first_accepted_at,polling_suspended_at FROM remote_sync_work WHERE kind=? AND key=?"
)


def synchronization_snapshot(db: sqlite3.Connection, kind: str, key: str) -> dict[str, object]:
    """Project retained scheduling state without creating it or changing due work."""
    if kind not in SYNC_KINDS:
        raise BusError("Invalid synchronization kind")
    exists = cast(
        "tuple[int] | None",
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='remote_sync_work'"
        ).fetchone(),
    )
    row = cast(
        "tuple[object, ...] | None",
        db.execute(
            SYNC_SNAPSHOT_QUERY,
            (kind, key),
        ).fetchone()
        if exists
        else None,
    )
    result: dict[str, object] = (
        dict(zip(SYNC_COLUMNS, row, strict=True))
        if row
        else dict.fromkeys(SYNC_COLUMNS, cast("object", None))
    )
    error = result["error_code"]
    if error is not None and (not isinstance(error, str) or error not in SYNC_ERRORS):
        result["error_code"] = "protocol_error"
    success = cast("str | None", result["last_success_at"])
    try:
        result["stale"] = (
            success is None
            or result["error_code"] is not None
            or datetime.now(UTC)
            >= datetime.fromisoformat(success) + timedelta(seconds=SYNC_READ_SECONDS)
        )
    except (ValueError, TypeError):
        result["stale"] = True
    return result
