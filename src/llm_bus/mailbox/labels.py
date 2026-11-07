"""Repository-scoped aliases for native session identities."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

from llm_bus.errors import BusError

if TYPE_CHECKING:
    import sqlite3


def validate_label(value: str) -> tuple[str, str]:
    """Validate bounded ASCII spelling and return display/key."""
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value) is None:
        raise BusError("Label must contain 1-64 ASCII letters, digits, underscores or hyphens")
    return value, value.lower()


def claim(db: sqlite3.Connection, scope: str, agent_address: str, label: str) -> None:
    """Claim inside the caller's immediate transaction."""
    display, key = validate_label(label)
    row = cast(
        "tuple[str] | None",
        db.execute(
            "SELECT address FROM agent_labels WHERE scope=? AND label_key=?", (scope, key)
        ).fetchone(),
    )
    if row is not None:
        if row[0] == agent_address:
            return
        raise BusError(f"Label {display!r} is claimed by {row[0]}")
    db.execute("DELETE FROM agent_labels WHERE address=?", (agent_address,))
    db.execute(
        "INSERT INTO agent_labels(scope,label_key,label,address) VALUES (?,?,?,?)",
        (scope, key, display, agent_address),
    )


def lookup(db: sqlite3.Connection, scope: str, label: str) -> str:
    """Resolve a current claim within its selected scope."""
    _display, key = validate_label(label)
    row = cast(
        "tuple[str] | None",
        db.execute(
            "SELECT address FROM agent_labels WHERE scope=? AND label_key=?", (scope, key)
        ).fetchone(),
    )
    if row is None:
        raise BusError(f"Unknown label {label!r}")
    return str(row[0])
