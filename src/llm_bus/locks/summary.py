"""Hook guidance for held, awaited and newly catalogued locks."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from llm_bus.locks.catalog import catalog_changed, catalog_rows
from llm_bus.locks.lifecycle import reap
from llm_bus.locks.models import SHARED_SCOPE
from llm_bus.locks.notices import deliver, held_phrase, known_phrase, persist, scope_name
from llm_bus.routing import routing_scope

if TYPE_CHECKING:
    from llm_bus.mailbox.store import Store


def summary(store: Store, agent_address: str, project: str, *, full: bool = False) -> str:
    """Hook text for this session's own locks, plus lock names only when they are news.

    Known names appear in full guidance or when names or descriptions changed since this
    session last saw them; holder churn alone is not news.
    """
    with store.transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        notices = reap(db)
        held = cast(
            "list[tuple[str, str, str, str, str | None]]",
            db.execute(
                "SELECT scope,resource,expires_at,check_at,checked_at FROM resource_locks "
                "WHERE holder=? ORDER BY check_at",
                (agent_address,),
            ).fetchall(),
        )
        waiting = cast(
            "list[tuple[str, str, str, str]]",
            db.execute(
                "SELECT w.scope,w.resource,l.holder,l.check_at FROM resource_lock_waiters w "
                "JOIN resource_locks l ON l.scope=w.scope AND l.resource=w.resource "
                "WHERE w.waiter=? ORDER BY w.requested_at",
                (agent_address,),
            ).fetchall(),
        )
        known = catalog_rows(db, (routing_scope(project), SHARED_SCOPE))
        changed = catalog_changed(db, agent_address, known)
        sent = persist(store, agent_address, db, notices)
    deliver(store, sent)
    parts: list[str] = []
    if known and (full or changed):
        parts.append(known_phrase(known))
    if held:
        names = ", ".join(held_phrase(*item) for item in held)
        parts.append(
            f"Locks you hold: {names}. Run `llm-bus lock release RESOURCE` when done, "
            "or `llm-bus lock acquire RESOURCE --check 30m` with a new estimate. "
        )
    if waiting:
        names = ", ".join(
            f"{scope_name(scope, resource)} (held by {holder}, check due {check})"
            for scope, resource, holder, check in waiting
        )
        parts.append(
            f"Locks you await: {names}; you are woken when one passes to you. "
            "`llm-bus lock acquire RESOURCE --notify` asks the holder now. "
        )
    if any(item[0] == SHARED_SCOPE for item in held + waiting):
        parts.append(
            "For machine-wide locks marked --shared, include --shared in every release, "
            "acquire --check, and acquire --notify command: "
            "`llm-bus lock release RESOURCE --shared`, "
            "`llm-bus lock acquire RESOURCE --shared --check 30m`, or "
            "`llm-bus lock acquire RESOURCE --shared --notify`. "
        )
    return "".join(parts)
