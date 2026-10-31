"""guidance."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING, cast

import pytest

from llm_bus import main
from llm_bus.errors import BusError
from llm_bus.locks.models import Request
from llm_bus.locks.service import Locks
from llm_bus.mailbox.store import Store

from .support import holder_store
from .support import make_store as _store
from .support import record_wakes as _record_wakes

if TYPE_CHECKING:
    from pathlib import Path


def test_invalid_names_reasons_and_ttls_are_rejected(tmp_path: Path) -> None:
    store, holder = holder_store(tmp_path)
    locks = Locks(store, holder, "/project")
    for name in ("", "   ", "a" * 201, "bad\nname"):
        with pytest.raises(BusError, match="Resource name"):
            locks.key(name, shared=False)
    key = locks.key("  db  ", shared=False)
    assert key.resource == "db"
    for ttl in (0, -1, 10081):
        with pytest.raises(BusError, match="TTL"):
            locks.acquire(
                key, Request(reason=None, ttl_minutes=ttl, check="1m", describe="test resource")
            )
    with pytest.raises(BusError, match="Reason"):
        locks.acquire(
            key, Request(reason="x" * 501, ttl_minutes=None, check="1h", describe="test resource")
        )


def test_hook_guidance_names_held_and_awaited_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wakes = _record_wakes(monkeypatch)
    store, holder = holder_store(tmp_path)
    waiter = store.register("codex", "two", "/project")
    key = Locks(store, holder, "/project").key("db:orders", shared=False)
    assert Locks(store, holder, "/project").summary() == ""
    Locks(store, holder, "/project").acquire(
        key, Request(reason=None, ttl_minutes=None, check="1h", describe="test resource")
    )
    Locks(store, waiter, "/project").acquire(
        key, Request(reason=None, ttl_minutes=None, check="1h", describe="test resource")
    )
    assert wakes == []

    held = Locks(store, holder, "/project").summary()
    assert held.startswith("Known lock names: db:orders = test resource (held by claude:one). ")
    assert "Locks you hold: db:orders (check due " in held
    assert "`llm-bus lock release RESOURCE`" in held
    assert "--check 30m" in held
    awaiting = Locks(store, waiter, "/project").summary()
    [lock] = cast(
        "list[dict[str, str]]",
        Locks(store, holder, "/project").status(key, every_scope=False)["locks"],
    )
    check = lock["check_at"]
    assert awaiting == (
        f"Known lock names: db:orders = test resource (held by {holder}). "
        "Reuse these names for the same resource; `llm-bus lock list` shows all. "
        f"Locks you await: db:orders (held by {holder}, check due {check}); "
        "you are woken when one passes to you. "
        "`llm-bus lock acquire RESOURCE --notify` asks the holder now. "
    )


def test_summary_preserves_shared_scope_after_catalog_is_seen(tmp_path: Path) -> None:
    store, holder = holder_store(tmp_path)
    waiter = store.register("codex", "two", "/project")
    mine = Locks(store, holder, "/project")
    theirs = Locks(store, waiter, "/project")
    for shared in (False, True):
        key = mine.key("db", shared=shared)
        mine.acquire(key, Request(check="1h", describe="database"))
        theirs.acquire(key, Request())
    mine.summary()
    theirs.summary()
    for summary in (mine.summary(), theirs.summary()):
        assert "Known lock names" not in summary
        assert "db (" in summary
        assert "db --shared (machine-wide)" in summary
        assert "include --shared in every release, acquire --check, and acquire --notify" in summary
        assert "`llm-bus lock release RESOURCE --shared`" in summary
        assert "`llm-bus lock acquire RESOURCE --shared --check 30m`" in summary
        assert "`llm-bus lock acquire RESOURCE --shared --notify`" in summary


def test_full_guidance_teaches_locks_and_reaches_running_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLM_BUS_DB", str(tmp_path / "bus.sqlite3"))
    store = Store(tmp_path / "bus.sqlite3")
    address = store.register("claude", "one", str(tmp_path))
    store.guidance_needed(address, 1, reset=False)
    hook = json.dumps(
        {"session_id": "one", "hook_event_name": "UserPromptSubmit", "cwd": str(tmp_path)}
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(hook))
    assert main(["hook", "claude"]) == 0
    assert "llm-bus lock acquire NAME --check 45m" in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", io.StringIO(hook))
    assert main(["hook", "claude"]) == 0
    assert "llm-bus lock acquire NAME" not in capsys.readouterr().out


def test_new_names_need_a_purpose_and_look_alikes_need_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record_wakes(monkeypatch)
    store = _store(tmp_path)
    first = store.register("claude", "one", "/project")
    second = store.register("codex", "two", "/project")
    one, two = Locks(store, first, "/project"), Locks(store, second, "/project")
    with pytest.raises(BusError, match="new lock name; add --describe"):
        one.acquire(one.key("db:orders", shared=False), Request(check="1h"))
    one.acquire(
        one.key("db:orders", shared=False),
        Request(check="1h", describe="orders Postgres database"),
    )
    one.release(one.key("db:orders", shared=False))

    for alike in ("orders-db", "DB_Orders", "orders", "db:order"):
        with pytest.raises(
            BusError, match=r"looks like existing lock name\(s\): db:orders \(orders"
        ):
            two.acquire(two.key(alike, shared=False), Request(check="1h", describe="dup"))
    distinct = two.acquire(
        two.key("orders-db", shared=False),
        Request(check="1h", describe="orders read replica", new=True),
    )
    assert distinct["status"] == "acquired"
    unrelated = two.acquire(
        two.key("deploy:staging", shared=False), Request(check="1h", describe="staging deploy")
    )
    assert unrelated["status"] == "acquired"

    reused = two.acquire(two.key("db:orders", shared=False), Request(check="1h"))
    assert reused["status"] == "acquired"
    listed = cast("list[dict[str, object]]", one.catalog(every_scope=False)["resources"])
    assert {item["resource"]: (item["description"], item["held_by"]) for item in listed} == {
        "db:orders": ("orders Postgres database", second),
        "orders-db": ("orders read replica", second),
        "deploy:staging": ("staging deploy", second),
    }
    two.release(two.key("deploy:staging", shared=False))
    free = [
        item
        for item in cast("list[dict[str, object]]", one.catalog(every_scope=False)["resources"])
        if item["resource"] == "deploy:staging"
    ]
    assert free[0]["held_by"] is None
    assert "deploy:staging = staging deploy (free)" in one.summary()


def test_idle_names_are_forgotten_after_thirty_days(tmp_path: Path) -> None:
    store, holder = holder_store(tmp_path)
    locks = Locks(store, holder, "/project")
    key = locks.key("db", shared=False)
    locks.acquire(key, Request(check="1h", describe="main database"))
    locks.release(key)
    with store.transaction() as db:
        db.execute("UPDATE resource_catalog SET last_used_at='2000-01-01T00:00:00.000Z'")
    assert locks.catalog(every_scope=True)["resources"] == []


def test_lock_names_reach_the_hook_only_when_they_are_news(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _record_wakes(monkeypatch)
    store = _store(tmp_path)
    first = store.register("claude", "one", "/project")
    other = store.register("codex", "two", "/project")
    mine, theirs = Locks(store, first, "/project"), Locks(store, other, "/project")
    assert mine.summary() == ""
    theirs.acquire(theirs.key("db", shared=False), Request(check="1h", describe="main database"))
    assert mine.summary().startswith("Known lock names: db = main database (held by codex:two)")
    assert mine.summary() == ""
    theirs.release(theirs.key("db", shared=False))
    assert mine.summary() == ""
    assert "Known lock names: db = main database (free)" in mine.summary(full=True)
    theirs.acquire(
        theirs.key("deploy", shared=False), Request(check="1h", describe="staging deploy")
    )
    news = mine.summary()
    assert "deploy = staging deploy" in news
    assert "db = main database" in news
    assert mine.summary() == ""
