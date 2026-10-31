"""Discovery store ownership."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from llm_bus.remote.models import Peer
    from llm_bus.remote.store import RemoteStore


def sessions(self: RemoteStore) -> list[dict[str, str | None]]:
    self.store.all_agents()  # Apply the native inactivity rule before exporting activity.
    with self.store.transaction() as db:
        rows = cast(
            "list[tuple[str, str, str, str | None, str]]",
            db.execute(
                "SELECT address, project, last_seen, ended_at, scope FROM agents "
                "WHERE address NOT IN (SELECT address FROM remote_proxies)"
            ).fetchall(),
        )
    return [
        {
            "native_address": row[0],
            "project": row[1],
            "last_seen": row[2],
            "ended_at": row[3],
            "scope": row[4],
        }
        for row in rows
    ]


def published(self: RemoteStore, key: str, payload: str | None = None) -> bool:
    with self.store.transaction() as db:
        row = cast(
            "tuple[str] | None",
            db.execute(
                "SELECT payload FROM remote_publications WHERE address=?", (key,)
            ).fetchone(),
        )
    return row is not None and (payload is None or row[0] == payload)


def record_publication(self: RemoteStore, key: str, payload: str) -> None:
    with self.store.transaction() as db:
        db.execute(
            "INSERT INTO remote_publications VALUES (?,?) "
            "ON CONFLICT(address) DO UPDATE SET payload=excluded.payload",
            (key, payload),
        )


def cache(self: RemoteStore, repo_id: str, items: list[Peer]) -> None:
    with self.store.transaction() as db:
        db.execute("DELETE FROM remote_peers WHERE repo_id=?", (repo_id,))
        db.executemany(
            "INSERT INTO remote_peers VALUES (?, ?, ?)",
            [(peer["address"], repo_id, json.dumps(peer)) for peer in items],
        )


def peers(self: RemoteStore) -> list[Peer]:
    with self.store.transaction() as db:
        rows = cast("list[tuple[str]]", db.execute("SELECT payload FROM remote_peers").fetchall())
    return [cast("Peer", json.loads(row[0])) for row in rows]
