"""Notices support for advisory resource locks."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from llm_bus.hosts.sessions import is_subagent
from llm_bus.locks.models import (
    HOOK_DESCRIPTION_CHARS,
    HOOK_NAME_LIMIT,
    SHARED_SCOPE,
    Delivery,
    Lock,
    Notice,
)
from llm_bus.wake.dispatch import dispatch_pending

if TYPE_CHECKING:
    import sqlite3

    from llm_bus.mailbox.store import Store


if TYPE_CHECKING:
    from llm_bus.mailbox.models import Message
    from llm_bus.wake.adapters import WakeResult


def scope_name(scope: str, resource: str) -> str:
    return f"{resource} --shared (machine-wide)" if scope == SHARED_SCOPE else resource


def held_phrase(scope: str, resource: str, until: str, check: str, checked: str | None) -> str:
    resource = scope_name(scope, resource)
    if checked is not None and checked >= check:
        return f"{resource} (check at {check} is unanswered; lease until {until})"
    return f"{resource} (check due {check}; lease until {until})"


def known_phrase(known: list[dict[str, object]]) -> str:
    shown = known[:HOOK_NAME_LIMIT]
    names = "; ".join(
        f"{item['resource']}{' --shared' if item['scope'] == SHARED_SCOPE else ''} = "
        f"{shorten(cast('str', item['description']))} "
        f"({'held by ' + cast('str', item['held_by']) if item['held_by'] else 'free'})"
        for item in shown
    )
    more = f" and {len(known) - len(shown)} more" if len(known) > len(shown) else ""
    return (
        f"Known lock names: {names}{more}. Reuse these names for the same resource; "
        "`llm-bus lock list` shows all. "
    )


def shorten(text: str) -> str:
    if len(text) <= HOOK_DESCRIPTION_CHARS:
        return text
    return text[: HOOK_DESCRIPTION_CHARS - 3].rstrip() + "..."


def flag(lock: Lock) -> str:
    return " --shared" if lock["scope"] == SHARED_SCOPE else ""


def request_body(lock: Lock, waiter: str, reason: str | None) -> str:
    why = f" Their reason: {reason}." if reason else ""
    return (
        f"llm-bus lock request: {waiter} is waiting for resource {lock['resource']!r} "
        f"that you hold.{why} If you no longer need it, run "
        f"`llm-bus lock release {lock['resource']}{flag(lock)}` and it passes to them. "
        "If you still need it, run "
        f"`llm-bus lock acquire {lock['resource']}{flag(lock)} --check DURATION` with your "
        "best estimate of when you can release, such as 30m; you will be asked again then."
    )


def check_body(lock: Lock) -> str:
    queue = (
        f" {len(lock['waiters'])} session(s) are waiting for it."
        if lock["waiters"]
        else " Nobody is waiting yet."
    )
    return (
        f"llm-bus lock check: you estimated resource {lock['resource']!r} would be releasable "
        f"by {lock['check_at']}.{queue} If your task no longer needs it, run "
        f"`llm-bus lock release {lock['resource']}{flag(lock)}`. If you still need it, run "
        f"`llm-bus lock acquire {lock['resource']}{flag(lock)} --check DURATION` with a new "
        f"estimate, such as 30m; you will be asked again then. Your lease ends at "
        f"{lock['expires_at']}."
    )


def grant_body(lock: Lock, previous: str) -> str:
    return (
        f"llm-bus lock granted: you now hold resource {lock['resource']!r} after {previous} "
        "released it or its lease ended. Set when you expect to release by running "
        f"`llm-bus lock acquire {lock['resource']}{flag(lock)} --check DURATION`, such as "
        "30m; you will be asked then. Run "
        f"`llm-bus lock release {lock['resource']}{flag(lock)}` when done. "
        f"Your lease ends at {lock['expires_at']}."
    )


def persist(
    store: Store, agent_address: str, db: sqlite3.Connection, notices: list[Notice]
) -> list[Message]:
    messages: list[Message] = []
    for notice in notices:
        if is_subagent(notice.recipient):
            continue
        if notice.sender is None and notice.recipient == agent_address:
            continue
        sender = notice.sender or agent_address
        if is_subagent(sender):
            # A legacy child timer may grant a root waiter after the holder's lease expires.
            sender = notice.recipient
        messages.append(
            store.send_in_transaction(
                db,
                sender,
                notice.recipient,
                notice.body,
                cross_folder=True,
            )
        )
    return messages


def deliver(store: Store, messages: list[Message]) -> list[Delivery]:
    deliveries: list[Delivery] = []
    for sent in messages:
        wake: WakeResult
        try:
            wake = dispatch_pending(store, sent["recipient"], trigger_id=sent["id"])
        except Exception as error:  # pylint: disable=broad-exception-caught
            wake = {
                "status": "unknown",
                "via": "wake coordinator",
                "reason": f"wake dispatch raised {type(error).__name__}",
            }
        deliveries.append(Delivery(to=sent["recipient"], message_id=sent["id"], wake=wake))
    return deliveries
