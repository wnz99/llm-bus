"""checks."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from llm_bus.errors import BusError
from llm_bus.locks.models import Request
from llm_bus.locks.service import Locks
from llm_bus.locks.timer import TIMER_POLL_SECONDS, run_timer

from .support import make_store as _store
from .support import overdue as _overdue
from .support import record_wakes as _record_wakes

if TYPE_CHECKING:
    from pathlib import Path

    from llm_bus.mailbox.store import Store


def test_due_batch_survives_one_wake_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    holders = [store.register("codex", name, "/project") for name in ("one", "two")]
    for holder, resource in zip(holders, ("db", "deploy"), strict=True):
        locks = Locks(store, holder, "/project")
        locks.acquire(locks.key(resource, shared=False), Request(check="1h", describe=resource))
    _overdue(store)
    attempted: list[str] = []

    def dispatch(_store: Store, recipient: str, *, trigger_id: int) -> dict[str, str]:
        assert trigger_id > 0
        attempted.append(recipient)
        assert all(len(store.inbox(holder)) == 1 for holder in holders)
        if len(attempted) == 1:
            raise OSError("host unavailable")
        return {"status": "requested", "via": "test"}

    monkeypatch.setattr("llm_bus.locks.notices.dispatch_pending", dispatch)
    locks = Locks(store, holders[0], "/project")
    result = locks.status(None, every_scope=False)
    deliveries = cast("list[dict[str, object]]", result["notices"])
    assert cast("dict[str, str]", deliveries[0]["wake"])["status"] == "unknown"
    assert set(attempted) == set(holders)
    assert locks.status(None, every_scope=False)["notices"] == []


def test_holder_must_estimate_release_within_its_lease(tmp_path: Path) -> None:
    store = _store(tmp_path)
    holder = store.register("claude", "one", "/project")
    locks = Locks(store, holder, "/project")
    key = locks.key("db", shared=False)
    with pytest.raises(BusError, match="--check"):
        locks.acquire(
            key, Request(check=None, reason=None, ttl_minutes=None, describe="test resource")
        )
    for bad in ("soon", "0m", "2w", "-5m"):
        with pytest.raises(BusError, match="--check"):
            locks.acquire(
                key, Request(check=bad, reason=None, ttl_minutes=None, describe="test resource")
            )
    with pytest.raises(BusError, match="beyond the lease"):
        locks.acquire(
            key, Request(check="2h", reason=None, ttl_minutes=60, describe="test resource")
        )
    acquired = locks.acquire(
        key, Request(check="90", reason=None, ttl_minutes=None, describe="test resource")
    )
    lock = cast("dict[str, object]", acquired["lock"])
    assert cast("str", lock["acquired_at"]) < cast("str", lock["check_at"])
    assert lock["checked_at"] is None


def test_due_check_asks_holder_once_until_it_answers_with_a_new_estimate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wakes = _record_wakes(monkeypatch)
    store = _store(tmp_path)
    holder = store.register("claude", "one", "/project")
    waiter = store.register("codex", "two", "/project")
    mine = Locks(store, holder, "/project")
    key = mine.key("db:orders", shared=False)
    mine.acquire(key, Request(check="1h", reason=None, ttl_minutes=None, describe="test resource"))
    Locks(store, waiter, "/project").acquire(
        key, Request(check=None, reason=None, ttl_minutes=None, describe="test resource")
    )
    assert mine.status(None, every_scope=False)["notices"] == []
    _overdue(store)

    first = Locks(store, waiter, "/project").status(None, every_scope=False)
    assert [item["to"] for item in cast("list[dict[str, object]]", first["notices"])] == [holder]
    assert wakes == [holder]
    messages = store.inbox(holder)
    assert len(messages) == 1
    message = messages[0]
    assert message["sender"] == holder
    assert message["body"].startswith("llm-bus lock check: you estimated resource 'db:orders'")
    assert "1 session(s) are waiting" in message["body"]
    assert Locks(store, waiter, "/project").status(None, every_scope=False)["notices"] == []
    assert "is unanswered" in mine.summary()

    renewed = mine.acquire(
        key, Request(check="30m", reason=None, ttl_minutes=None, describe="test resource")
    )
    assert renewed["status"] == "renewed"
    assert cast("dict[str, object]", renewed["lock"])["checked_at"] is None
    _overdue(store)
    again = mine.status(None, every_scope=False)
    assert [item["to"] for item in cast("list[dict[str, object]]", again["notices"])] == [holder]
    assert wakes == [holder, holder]


def test_granted_holder_is_asked_for_an_estimate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record_wakes(monkeypatch)
    store = _store(tmp_path)
    holder = store.register("claude", "one", "/project")
    waiter = store.register("codex", "two", "/project")
    key = Locks(store, holder, "/project").key("db", shared=False)
    Locks(store, holder, "/project").acquire(
        key, Request(check="1h", reason=None, ttl_minutes=None, describe="test resource")
    )
    Locks(store, waiter, "/project").acquire(
        key, Request(check=None, reason=None, ttl_minutes=None, describe="test resource")
    )
    Locks(store, holder, "/project").release(key)
    messages = store.inbox(waiter)
    assert len(messages) == 1
    grant = messages[0]
    assert "--check DURATION" in grant["body"]
    assert "is unanswered" in Locks(store, waiter, "/project").summary()
    assert Locks(store, waiter, "/project").status(key, every_scope=False)["notices"] == []


def test_timer_sleeps_until_estimate_then_raises_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wakes = _record_wakes(monkeypatch)
    store = _store(tmp_path)
    holder = store.register("claude", "one", "/project")
    locks = Locks(store, holder, "/project")
    key = locks.key("db", shared=False)
    locks.acquire(key, Request(check="2h", reason=None, ttl_minutes=None, describe="test resource"))
    with store.transaction() as db:
        db.execute(
            "UPDATE agents SET last_seen=strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-1 hour') "
            "WHERE address=?",
            (holder,),
        )
        last_seen = cast(
            "tuple[str]",
            db.execute("SELECT last_seen FROM agents WHERE address=?", (holder,)).fetchone(),
        )
    slept: list[float] = []

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        _overdue(store)

    run_timer(store, key, sleep=fake_sleep)
    assert len(slept) == 1
    assert 0 < slept[0] <= TIMER_POLL_SECONDS
    assert wakes == [holder]
    with store.transaction() as db:
        assert db.execute("SELECT last_seen FROM agents WHERE address=?", (holder,)).fetchone() == (
            last_seen
        )
    run_timer(store, key, sleep=fake_sleep)
    assert wakes == [holder]
    assert len(slept) == 1
    locks.release(key)
    run_timer(store, key, sleep=fake_sleep)
    assert len(slept) == 1


def test_timer_handoff_does_not_reactivate_ended_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wakes = _record_wakes(monkeypatch)
    store = _store(tmp_path)
    holder = store.register("claude", "one", "/project")
    waiter = store.register("codex", "two", "/project")
    locks = Locks(store, holder, "/project")
    key = locks.key("db", shared=False)
    locks.acquire(key, Request(check="1h", describe="database"))
    Locks(store, waiter, "/project").acquire(key, Request())

    def end_while_sleeping(_seconds: float) -> None:
        store.end_session("claude", "one")
        with store.transaction() as db:
            db.execute(
                "UPDATE agents SET ended_at='2000-01-01T00:00:00.000Z' WHERE address=?",
                (holder,),
            )
        _overdue(store)

    run_timer(store, key, sleep=end_while_sleeping)
    with store.transaction() as db:
        assert db.execute("SELECT ended_at FROM agents WHERE address=?", (holder,)).fetchone() == (
            "2000-01-01T00:00:00.000Z",
        )
        assert db.execute("SELECT holder FROM resource_locks").fetchall() == [(waiter,)]
    assert wakes == [waiter]
    messages = store.inbox(waiter)
    assert len(messages) == 1
    message = messages[0]
    assert message["sender"] == holder
    assert message["body"].startswith("llm-bus lock granted:")
