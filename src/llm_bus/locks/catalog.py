"""Catalog support for advisory resource locks."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from typing import TYPE_CHECKING, cast

from llm_bus.errors import BusError
from llm_bus.locks.models import MAX_DESCRIPTION_LENGTH, SIMILAR_RATIO, Key

if TYPE_CHECKING:
    import sqlite3


def clean_description(description: str | None) -> str | None:
    if description is None:
        return None
    text = " ".join(description.split())
    if len(text) > MAX_DESCRIPTION_LENGTH:
        raise BusError("Description must be at most 200 characters")
    return text or None


def tokens(name: str) -> list[str]:
    return sorted(token for token in re.split(r"[^a-z0-9]+", name.lower()) if token)


def similar(first: str, second: str) -> bool:
    """Treat reordered, re-punctuated, nested, or near-spelled names as likely duplicates."""
    left, right = tokens(first), tokens(second)
    if not left or not right:
        return False
    if set(left) <= set(right) or set(right) <= set(left):
        return True
    ratio = difflib.SequenceMatcher(None, " ".join(left), " ".join(right)).ratio()
    return ratio >= SIMILAR_RATIO


def register(
    db: sqlite3.Connection, key: Key, agent: str, description: str | None, *, new: bool
) -> None:
    """Record a name and its purpose on first use; refuse undescribed or look-alike names."""
    known = cast(
        "tuple[str] | None",
        db.execute(
            "SELECT description FROM resource_catalog WHERE scope=? AND resource=?", key
        ).fetchone(),
    )
    if known is not None:
        db.execute(
            "UPDATE resource_catalog SET last_used_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),"
            "description=COALESCE(?,description) WHERE scope=? AND resource=?",
            (description, *key),
        )
        return
    if description is None:
        raise BusError(
            f"{key.resource!r} is a new lock name; add --describe with what it protects. "
            "Run `llm-bus lock list` to reuse an existing name instead"
        )
    if not new:
        others = cast(
            "list[tuple[str, str]]",
            db.execute(
                "SELECT resource,description FROM resource_catalog WHERE scope=? ORDER BY resource",
                (key.scope,),
            ).fetchall(),
        )
        alike = [f"{name} ({about})" for name, about in others if similar(key.resource, name)]
        if alike:
            raise BusError(
                f"{key.resource!r} looks like existing lock name(s): {'; '.join(alike)}. "
                "Acquire one of those if it is the same resource, or add --new if it is not"
            )
    db.execute(
        "INSERT INTO resource_catalog(scope,resource,description,created_by,created_at,"
        "last_used_at) VALUES (?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),"
        "strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
        (*key, description, agent),
    )


def catalog_rows(db: sqlite3.Connection, scopes: tuple[str, str] | None) -> list[dict[str, object]]:
    query = (
        "SELECT c.scope,c.resource,c.description,c.created_by,c.last_used_at,l.holder,"
        "l.check_at,(SELECT COUNT(*) FROM resource_lock_waiters w WHERE w.scope=c.scope "
        "AND w.resource=c.resource) FROM resource_catalog c LEFT JOIN resource_locks l "
        "ON l.scope=c.scope AND l.resource=c.resource"
    )
    parameters: tuple[str, ...] = ()
    if scopes is not None:
        query += " WHERE c.scope IN (?,?)"
        parameters = scopes
    rows = cast(
        "list[tuple[str, str, str, str, str, str | None, str | None, int]]",
        db.execute(query + " ORDER BY c.last_used_at DESC", parameters).fetchall(),
    )
    return [
        {
            "resource": row[1],
            "scope": row[0],
            "description": row[2],
            "created_by": row[3],
            "last_used_at": row[4],
            "held_by": row[5],
            "check_at": row[6],
            "waiters": row[7],
        }
        for row in rows
    ]


def catalog_changed(db: sqlite3.Connection, agent: str, known: list[dict[str, object]]) -> bool:
    """Record what this session has been shown; report whether names or purposes differ."""
    signature = hashlib.sha256(
        json.dumps(
            sorted((item["scope"], item["resource"], item["description"]) for item in known)
        ).encode()
    ).hexdigest()
    seen = cast(
        "tuple[str] | None",
        db.execute("SELECT signature FROM lock_catalog_seen WHERE address=?", (agent,)).fetchone(),
    )
    db.execute(
        "INSERT INTO lock_catalog_seen(address,signature) VALUES (?,?) "
        "ON CONFLICT(address) DO UPDATE SET signature=excluded.signature",
        (agent, signature),
    )
    return seen is None or seen[0] != signature
