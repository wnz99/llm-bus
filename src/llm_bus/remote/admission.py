"""Admission ownership."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from llm_bus.errors import BusError
from llm_bus.policy.inbound import receive_mode
from llm_bus.remote import outbox
from llm_bus.remote.models import Delivery, validate_reply_uuid

if TYPE_CHECKING:
    from sqlite3 import Connection


if TYPE_CHECKING:
    from llm_bus.remote.store import RemoteStore


def incoming_parent(self: RemoteStore, db: Connection, item: Delivery, scope: str) -> int | None:
    parent_uuid = item.get("reply_to")
    if parent_uuid is None:
        return None
    validate_reply_uuid(parent_uuid)
    parent = cast(
        "tuple[int, str] | None",
        db.execute(
            "SELECT local_id,envelope FROM remote_imports WHERE id=? UNION ALL "
            "SELECT local_id,envelope FROM remote_outbox WHERE id=?",
            (parent_uuid, parent_uuid),
        ).fetchone(),
    )
    if parent is None:
        return None
    native = item["recipient"].split("/", 2)[2]
    outbox.validate_parent_envelope(self, parent[1], item["sender"], native, item["repo_id"])
    self.store.validate_reply(
        db, parent[0], (item["sender"], f"remote:{item['repo_id']}"), (native, scope)
    )
    return parent[0]


def admit_message(
    self: RemoteStore, item: Delivery, scope: str
) -> tuple[str, int | None, bool] | None:
    """Commit exactly one delivery class under the current machine policy."""
    native = item["recipient"].split("/", 2)[2]
    payload = json.dumps(item, sort_keys=True)
    with self.store.transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        recipient = cast(
            "tuple[str, str] | None",
            db.execute(
                "SELECT project,scope FROM agents WHERE address=? AND address NOT IN "
                "(SELECT address FROM remote_proxies)",
                (native,),
            ).fetchone(),
        )
        if recipient is None:
            return None
        mode = receive_mode(item, scope, recipient[1], self.connection)
        if mode == "deny":
            return None
        prior = cast(
            "tuple[str, int | None, str] | None",
            db.execute(
                "SELECT 'inbox',local_id,envelope FROM remote_imports WHERE id=? "
                "UNION ALL SELECT 'monitor',NULL,envelope FROM remote_monitor WHERE id=? "
                "UNION ALL SELECT 'outbox',local_id,envelope FROM remote_outbox WHERE id=?",
                (item["id"], item["id"], item["id"]),
            ).fetchone(),
        )
        if prior is not None:
            prior_mode, prior_id, prior_payload = prior
            if prior_mode == "outbox" or prior_payload != payload:
                raise BusError("Remote message ID was reused with different content")
            return prior_mode, prior_id, False
        parent_id = incoming_parent(self, db, item, scope)
        if "reply_to" in item and parent_id is None:
            return None
        if mode == "monitor":
            db.execute(
                "INSERT INTO remote_monitor(id,envelope,scope,connection) VALUES (?,?,?,?)",
                (item["id"], payload, scope, self.connection),
            )
            return "monitor", None, True
        outbox.proxy(db, item["sender"], item["repo_id"])
        cursor = db.execute(
            "INSERT INTO messages(sender,recipient,body,recipient_cwd,"
            "sender_scope,recipient_scope,sent_at,reply_to) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                item["sender"],
                native,
                item["body"],
                recipient[0],
                f"remote:{item['repo_id']}",
                scope,
                item["sent_at"],
                parent_id,
            ),
        )
        local_id = cursor.lastrowid
        if local_id is None:
            raise RuntimeError("SQLite did not return an imported message ID")
        db.execute(
            "INSERT INTO remote_imports(id,local_id,envelope) VALUES (?,?,?)",
            (item["id"], local_id, payload),
        )
        self.store.request_dispatch(db, native)
        return "inbox", local_id, True


def acknowledgements(self: RemoteStore) -> list[tuple[str, str]]:
    with self.store.transaction() as db:
        rows = cast(
            "list[tuple[str, str]]",
            db.execute(
                "SELECT r.id,r.envelope FROM remote_imports r "
                "JOIN messages m ON m.id=r.local_id "
                "WHERE r.ack_synced=0 AND m.acknowledged_at IS NOT NULL"
            ).fetchall(),
        )
    return [
        (remote_id, cast("Delivery", json.loads(payload))["repo_id"]) for remote_id, payload in rows
    ]


def permits_ack(self: RemoteStore, remote_id: str) -> bool:
    with self.store.transaction() as db:
        row = cast(
            "tuple[int] | None",
            db.execute("SELECT local_id FROM remote_imports WHERE id=?", (remote_id,)).fetchone(),
        )
    return row is not None and self.store.pending_allowed(row[0])


def mark_acknowledged(self: RemoteStore, remote_id: str) -> None:
    with self.store.transaction() as db:
        db.execute("UPDATE remote_imports SET ack_synced=1 WHERE id=?", (remote_id,))
