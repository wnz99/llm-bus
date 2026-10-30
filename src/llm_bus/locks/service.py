"""Transaction orchestration for advisory resource locks."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from llm_bus.errors import BusError
from llm_bus.locks.catalog import catalog_rows, clean_description, register
from llm_bus.locks.lifecycle import enqueue, hand_off, read_lock, reap
from llm_bus.locks.models import MAX_RESOURCE_LENGTH, SHARED_SCOPE, Key, Notice, Request
from llm_bus.locks.notices import deliver, persist
from llm_bus.locks.summary import summary
from llm_bus.locks.timer import schedule_check
from llm_bus.locks.validation import check_minutes, clean_reason, lease_minutes
from llm_bus.routing import routing_scope

if TYPE_CHECKING:
    from llm_bus.mailbox.store import Store


class Locks:
    """Lock commands for one registered session; every call reaps lapsed holders first."""

    def __init__(self, store: Store, agent_address: str, project: str) -> None:
        self.store: Store = store
        self.agent_address: str = agent_address
        self.project: str = project

    def key(self, resource: str, *, shared: bool) -> Key:
        """Validate a resource name and bind it to this folder, repository, or the shared scope."""
        name = resource.strip()
        if not name or len(name) > MAX_RESOURCE_LENGTH or not name.isprintable():
            raise BusError("Resource name must be 1-200 printable characters")
        return Key(SHARED_SCOPE if shared else routing_scope(self.project), name)

    def acquire(self, key: Key, request: Request) -> dict[str, object]:
        """Take a free resource or renew an owned one with a new check time, else queue.

        The check time is the holder's estimate of when it can release; the bus wakes the
        holder then and asks. With notify, a queued caller also wakes the holder now.
        """
        reason = clean_reason(request.reason)
        ttl = lease_minutes(request.ttl_minutes)
        description = clean_description(request.describe)
        with self.store.transaction() as db:
            db.execute("BEGIN IMMEDIATE")
            notices = reap(db)
            asked: list[Notice] = []
            row = cast(
                "tuple[str] | None",
                db.execute(
                    "SELECT holder FROM resource_locks WHERE scope=? AND resource=?", key
                ).fetchone(),
            )
            if row is None:
                minutes = check_minutes(request.check, ttl)
                register(db, key, self.agent_address, description, new=request.new)
                db.execute(
                    "INSERT INTO resource_locks(scope,resource,holder,reason,acquired_at,"
                    "expires_at,check_at) VALUES (?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),"
                    "strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?),"
                    "strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?))",
                    (*key, self.agent_address, reason, f"+{ttl} minutes", f"+{minutes} minutes"),
                )
                db.execute(
                    "DELETE FROM resource_lock_waiters WHERE scope=? AND resource=? AND waiter=?",
                    (*key, self.agent_address),
                )
                status = "acquired"
            elif row[0] == self.agent_address:
                minutes = check_minutes(request.check, ttl)
                register(db, key, self.agent_address, description, new=True)
                db.execute(
                    "UPDATE resource_locks SET "
                    "expires_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?), "
                    "check_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now', ?), checked_at=NULL, "
                    "reason=COALESCE(?,reason) WHERE scope=? AND resource=?",
                    (f"+{ttl} minutes", f"+{minutes} minutes", reason, *key),
                )
                status = "renewed"
            else:
                asked = enqueue(
                    self.agent_address, db, key, row[0], request._replace(reason=reason)
                )
                notices.extend(asked)
                status = "waiting"
            lock = read_lock(db, key)
            sent = persist(self.store, self.agent_address, db, notices)
        result: dict[str, object] = {"status": status, "lock": lock}
        if status == "waiting":
            result["notified"] = bool(asked)
        else:
            schedule_check(key)
        return {**result, "notices": deliver(self.store, sent)}

    def release(self, key: Key) -> dict[str, object]:
        """Give up an owned resource; the first live waiter takes it and is woken."""
        with self.store.transaction() as db:
            db.execute("BEGIN IMMEDIATE")
            notices = reap(db)
            row = cast(
                "tuple[str] | None",
                db.execute(
                    "SELECT holder FROM resource_locks WHERE scope=? AND resource=?", key
                ).fetchone(),
            )
            if row is None:
                raise BusError(f"Resource {key.resource!r} is not locked")
            if row[0] != self.agent_address:
                raise BusError(
                    f"Resource {key.resource!r} is held by {row[0]}; "
                    "`llm-bus lock acquire --notify` queues you and asks them"
                )
            notices.extend(hand_off(db, key, row[0]))
            lock = read_lock(db, key)
            sent = persist(self.store, self.agent_address, db, notices)
        return {
            "status": "released",
            "granted_to": lock["holder"] if lock else None,
            "lock": lock,
            "notices": deliver(self.store, sent),
        }

    def status(self, key: Key | None, *, every_scope: bool) -> dict[str, object]:
        """Read locks visible from this folder, plus shared ones, without changing ownership."""
        with self.store.transaction() as db:
            db.execute("BEGIN IMMEDIATE")
            notices = reap(db)
            clauses: list[str] = []
            parameters: list[str] = []
            if key is not None:
                clauses.append("scope=? AND resource=?")
                parameters.extend(key)
            elif not every_scope:
                clauses.append("scope IN (?,?)")
                parameters.extend((routing_scope(self.project), SHARED_SCOPE))
            query = "SELECT scope,resource FROM resource_locks"
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            keys = [
                Key(*row)
                for row in cast(
                    "list[tuple[str, str]]",
                    db.execute(query + " ORDER BY scope,resource", parameters).fetchall(),
                )
            ]
            locks = [lock for lock in (read_lock(db, item) for item in keys) if lock]
            sent = persist(self.store, self.agent_address, db, notices)
        return {"locks": locks, "notices": deliver(self.store, sent)}

    def catalog(self, *, every_scope: bool) -> dict[str, object]:
        """Known lock names with what they cover and who holds them, free ones included."""
        with self.store.transaction() as db:
            db.execute("BEGIN IMMEDIATE")
            notices = reap(db)
            resources = catalog_rows(
                db, None if every_scope else (routing_scope(self.project), SHARED_SCOPE)
            )
            sent = persist(self.store, self.agent_address, db, notices)
        return {"resources": resources, "notices": deliver(self.store, sent)}

    def summary(self, *, full: bool = False) -> str:
        """Build hook guidance after reaping lapsed holders."""
        return summary(self.store, self.agent_address, self.project, full=full)
