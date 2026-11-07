"""Discovery store ownership."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from llm_bus.remote.models import Peer
    from llm_bus.remote.store import RemoteStore


def sessions(self: RemoteStore) -> list[dict[str, str | int | None]]:
    self.store.all_agents()  # Apply the native inactivity rule before exporting activity.
    with self.store.transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = cast(
            "list[tuple[str, str, str, str | None, str, str | None]]",
            db.execute(
                "SELECT a.address, a.project, a.last_seen, a.ended_at, a.scope, l.label "
                "FROM agents a LEFT JOIN agent_labels l ON l.address=a.address "
                "WHERE a.address NOT IN (SELECT address FROM remote_proxies)"
            ).fetchall(),
        )
        result: list[dict[str, str | int | None]] = []
        for row in rows:
            address, project, last_seen, ended_at, scope, label = row
            session: dict[str, str | int | None] = {
                "native_address": address,
                "project": project,
                "last_seen": last_seen,
                "ended_at": ended_at,
                "scope": scope,
                "label": label if ended_at is None else None,
            }
            payload = json.dumps(session, sort_keys=True)
            previous = cast(
                "tuple[int, str] | None",
                db.execute(
                    "SELECT revision,payload FROM session_publications WHERE address=?", (address,)
                ).fetchone(),
            )
            revision = 1 if previous is None else previous[0] + int(previous[1] != payload)
            db.execute(
                "INSERT INTO session_publications(address,revision,payload) VALUES (?,?,?) "
                "ON CONFLICT(address) DO UPDATE SET "
                "revision=excluded.revision,payload=excluded.payload",
                (address, revision, payload),
            )
            session["revision"] = revision
            result.append(session)
    return result


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
