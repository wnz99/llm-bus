"""ownership."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, cast

import pytest

from llm_bus import main
from llm_bus.errors import BusError
from llm_bus.locks.models import Request
from llm_bus.locks.service import Locks

from .support import bodies as _bodies
from .support import holder_store
from .support import make_store as _store
from .support import overdue as _overdue
from .support import record_wakes as _record_wakes

if TYPE_CHECKING:
    from pathlib import Path


def test_free_resource_is_acquired_then_renewed_by_its_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wakes = _record_wakes(monkeypatch)
    store, holder = holder_store(tmp_path)
    locks = Locks(store, holder, "/project")
    key = locks.key("db:orders", shared=False)

    first = locks.acquire(
        key, Request(reason="migrating", ttl_minutes=None, check="1h", describe="test resource")
    )
    assert first["status"] == "acquired"
    lock = cast("dict[str, object]", first["lock"])
    assert lock["holder"] == holder
    assert lock["reason"] == "migrating"
    assert lock["scope"] == key.scope
    assert lock["waiters"] == []
    assert first["notices"] == []

    again = locks.acquire(
        key, Request(reason=None, ttl_minutes=5, check="5m", describe="test resource")
    )
    assert again["status"] == "renewed"
    renewed = cast("dict[str, object]", again["lock"])
    assert renewed["reason"] == "migrating"
    assert cast("str", renewed["expires_at"]) < cast("str", lock["expires_at"])
    assert wakes == []


def test_contended_acquire_queues_waiter_and_wakes_holder_once_per_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wakes = _record_wakes(monkeypatch)
    store, holder = holder_store(tmp_path)
    waiter = store.register("codex", "two", "/project")
    key = Locks(store, holder, "/project").key("db:orders", shared=False)
    Locks(store, holder, "/project").acquire(
        key, Request(reason=None, ttl_minutes=None, check="1h", describe="test resource")
    )

    quiet = Locks(store, waiter, "/project").acquire(
        key, Request(reason="need schema", ttl_minutes=None, check="1h", describe="test resource")
    )
    assert quiet["status"] == "waiting"
    assert quiet["notified"] is False
    assert quiet["notices"] == []
    assert wakes == []

    asked = Locks(store, waiter, "/project").acquire(
        key,
        Request(reason=None, ttl_minutes=None, check="1h", notify=True, describe="test resource"),
    )
    assert asked["status"] == "waiting"
    assert asked["notified"] is True
    lock = cast("dict[str, object]", asked["lock"])
    assert lock["holder"] == holder
    assert [item["waiter"] for item in cast("list[dict[str, object]]", lock["waiters"])] == [waiter]
    notices = cast("list[dict[str, object]]", asked["notices"])
    assert [item["to"] for item in notices] == [holder]
    assert wakes == [holder]
    [body] = _bodies(store, holder)
    assert body.startswith("llm-bus lock request:")
    assert "need schema" in body
    assert "`llm-bus lock release db:orders`" in body

    repeat = Locks(store, waiter, "/project").acquire(
        key,
        Request(reason=None, ttl_minutes=None, check="1h", notify=True, describe="test resource"),
    )
    assert repeat["status"] == "waiting"
    assert repeat["notified"] is False
    assert repeat["notices"] == []
    assert wakes == [holder]
    assert len(_bodies(store, holder)) == 1


def test_release_hands_off_to_first_live_waiter_and_wakes_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wakes = _record_wakes(monkeypatch)
    store, holder = holder_store(tmp_path)
    gone = store.register("codex", "two", "/project")
    later = store.register("claude", "three", "/project")
    key = Locks(store, holder, "/project").key("db:orders", shared=False)
    Locks(store, holder, "/project").acquire(
        key, Request(reason=None, ttl_minutes=None, check="1h", describe="test resource")
    )
    Locks(store, gone, "/project").acquire(
        key,
        Request(reason=None, ttl_minutes=None, check="1h", notify=True, describe="test resource"),
    )
    Locks(store, later, "/project").acquire(
        key,
        Request(
            reason="reports", ttl_minutes=None, check="1h", notify=True, describe="test resource"
        ),
    )
    store.end_session("codex", "two")
    assert wakes == [holder, holder]

    with pytest.raises(BusError, match="held by"):
        Locks(store, later, "/project").release(key)
    released = Locks(store, holder, "/project").release(key)
    assert released["status"] == "released"
    assert released["granted_to"] == later
    lock = cast("dict[str, object]", released["lock"])
    assert lock["holder"] == later
    assert lock["reason"] == "reports"
    assert lock["waiters"] == []
    assert wakes == [holder, holder, later]
    [body] = _bodies(store, later)
    assert body.startswith("llm-bus lock granted:")

    final = Locks(store, later, "/project").release(key)
    assert final["granted_to"] is None
    assert final["lock"] is None
    with pytest.raises(BusError, match="not locked"):
        Locks(store, later, "/project").release(key)


def test_expired_or_ended_holders_lose_the_lock_on_any_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wakes = _record_wakes(monkeypatch)
    store, holder = holder_store(tmp_path)
    waiter = store.register("codex", "two", "/project")
    key = Locks(store, holder, "/project").key("db:orders", shared=False)
    Locks(store, holder, "/project").acquire(
        key, Request(reason=None, ttl_minutes=1, check="1m", describe="test resource")
    )
    Locks(store, waiter, "/project").acquire(
        key, Request(reason=None, ttl_minutes=None, check="1h", describe="test resource")
    )
    with store.transaction() as db:
        db.execute("UPDATE resource_locks SET expires_at='2000-01-01T00:00:00.000Z'")
    assert wakes == []

    status = Locks(store, holder, "/project").status(None, every_scope=False)
    [lock] = cast("list[dict[str, object]]", status["locks"])
    assert lock["holder"] == waiter
    assert [item["to"] for item in cast("list[dict[str, object]]", status["notices"])] == [waiter]
    assert wakes == [waiter]

    store.end_session("codex", "two")
    empty = Locks(store, holder, "/project").status(None, every_scope=False)
    assert empty["locks"] == []


def test_scopes_separate_folders_unless_shared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wakes = _record_wakes(monkeypatch)
    store = _store(tmp_path)
    first = store.register("claude", "one", str(tmp_path / "first"))
    second = store.register("codex", "two", str(tmp_path / "second"))
    one = Locks(store, first, str(tmp_path / "first"))
    two = Locks(store, second, str(tmp_path / "second"))

    assert one.acquire(
        one.key("db", shared=False),
        Request(reason=None, ttl_minutes=None, check="1h", describe="test resource"),
    )["status"] == ("acquired")
    assert two.acquire(
        two.key("db", shared=False),
        Request(reason=None, ttl_minutes=None, check="1h", describe="test resource"),
    )["status"] == ("acquired")
    assert one.acquire(
        one.key("db", shared=True),
        Request(reason=None, ttl_minutes=None, check="1h", describe="test resource"),
    )["status"] == ("acquired")
    waiting = two.acquire(
        two.key("db", shared=True),
        Request(reason=None, ttl_minutes=None, check="1h", notify=True, describe="test resource"),
    )
    assert waiting["status"] == "waiting"
    assert wakes == [first]
    assert "--shared" in _bodies(store, first)[0]

    visible = cast("list[dict[str, object]]", two.status(None, every_scope=False)["locks"])
    assert sorted((lock["scope"], lock["holder"]) for lock in visible) == sorted(
        [(one.key("db", shared=True).scope, first), (two.key("db", shared=False).scope, second)]
    )
    everything = cast("list[dict[str, object]]", two.status(None, every_scope=True)["locks"])
    assert len(everything) == 3


def test_cli_lock_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wakes = _record_wakes(monkeypatch)
    monkeypatch.setenv("LLM_BUS_DB", str(tmp_path / "bus.sqlite3"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "one")
    assert (
        main(
            [
                "lock",
                "acquire",
                "db:orders",
                "--reason",
                "migrating",
                "--ttl",
                "10",
                "--check",
                "5m",
                "--describe",
                "orders database",
            ]
        )
        == 0
    )
    acquired = cast("dict[str, object]", json.loads(capsys.readouterr().out))
    assert acquired["status"] == "acquired"

    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    monkeypatch.setenv("CODEX_THREAD_ID", "two")
    assert main(["lock", "acquire", "db:orders"]) == 0
    waiting = cast("dict[str, object]", json.loads(capsys.readouterr().out))
    assert waiting["status"] == "waiting"
    assert wakes == []
    assert main(["lock", "acquire", "db:orders", "--notify"]) == 0
    asked = cast("dict[str, object]", json.loads(capsys.readouterr().out))
    assert asked["notified"] is True
    assert wakes == ["claude:one"]
    assert main(["lock", "release", "db:orders"]) == 1
    assert "held by claude:one" in capsys.readouterr().err

    monkeypatch.delenv("CODEX_THREAD_ID")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "one")
    assert main(["lock", "release", "db:orders"]) == 0
    released = cast("dict[str, object]", json.loads(capsys.readouterr().out))
    assert released["granted_to"] == "codex:two"
    assert main(["lock", "status", "--all"]) == 0
    status = cast("dict[str, object]", json.loads(capsys.readouterr().out))
    [lock] = cast("list[dict[str, object]]", status["locks"])
    assert lock["holder"] == "codex:two"
    assert main(["lock", "status", "db:orders", "--shared"]) == 0
    assert json.loads(capsys.readouterr().out)["locks"] == []


@pytest.mark.parametrize("handoff", [False, True])
def test_notice_persistence_failure_rolls_back_lock_state(tmp_path: Path, *, handoff: bool) -> None:
    store, holder = holder_store(tmp_path)
    waiter = store.register("codex", "two", "/project")
    mine = Locks(store, holder, "/project")
    key = mine.key("db", shared=False)
    mine.acquire(key, Request(check="1h", describe="database"))
    Locks(store, waiter, "/project").acquire(key, Request())
    if not handoff:
        _overdue(store)
    with store.transaction() as db:
        db.execute(
            "CREATE TRIGGER reject_notice BEFORE INSERT ON messages "
            "BEGIN SELECT RAISE(ABORT, 'storage failure'); END"
        )
    if handoff:
        with pytest.raises(sqlite3.IntegrityError, match="storage failure"):
            mine.release(key)
    else:
        with pytest.raises(sqlite3.IntegrityError, match="storage failure"):
            mine.status(None, every_scope=False)
    with store.transaction() as db:
        assert db.execute("SELECT holder,checked_at FROM resource_locks").fetchall() == [
            (holder, None)
        ]
        assert db.execute("SELECT waiter FROM resource_lock_waiters").fetchall() == [(waiter,)]
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone() == (0,)
