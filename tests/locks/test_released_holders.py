"""Locks taken from holders whose process exited, or by a forced release."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

from llm_bus import main
from llm_bus.errors import BusError
from llm_bus.hosts import processes
from llm_bus.hosts.processes import HostProcess, exited, host_process
from llm_bus.locks.models import Request
from llm_bus.locks.service import Locks
from llm_bus.mailbox.store import Store
from tests.coordination.test_enrollment import create, invoke

from .support import bodies, record_wakes

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

HOLDER_PROCESS = HostProcess(1234, "Thu Nov 12 01:00:00 2026")
WAITER_PROCESS = HostProcess(5678, "Thu Nov 12 01:05:00 2026")
TAKE = Request(check="1h", describe="test resource")


@pytest.fixture(autouse=True)
def recorded_processes_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake PIDs above must not be checked against this machine's process table."""
    _exited(monkeypatch, set())


def _exited(monkeypatch: pytest.MonkeyPatch, result: set[HostProcess] | None) -> list[object]:
    calls: list[object] = []

    def fake(items: list[HostProcess]) -> set[HostProcess] | None:
        calls.append(sorted(items))
        return result

    monkeypatch.setattr("llm_bus.locks.lifecycle.exited", fake)
    return calls


def _releases(store: Store) -> list[tuple[object, ...]]:
    with store.transaction() as db:
        return db.execute(
            "SELECT resource,holder,cause,actor,reason,granted_to FROM resource_lock_releases"
        ).fetchall()


def _queued(tmp_path: Path) -> tuple[Store, str, str]:
    store = Store(tmp_path / "bus.sqlite3")
    holder = store.register("codex", "holder", "/project", host=HOLDER_PROCESS)
    waiter = store.register("claude", "waiter", "/project", host=WAITER_PROCESS)
    key = Locks(store, holder, "/project").key("db", shared=False)
    Locks(store, holder, "/project").acquire(key, TAKE)
    assert Locks(store, waiter, "/project").acquire(key, TAKE)["status"] == "waiting"
    return store, holder, waiter


def test_exited_holder_passes_lock_to_waiter_and_ends_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record_wakes(monkeypatch)
    store, holder, waiter = _queued(tmp_path)
    observer = store.register("codex", "observer", "/project")
    calls = _exited(monkeypatch, {HOLDER_PROCESS})

    [lock] = cast(
        "list[dict[str, object]]",
        Locks(store, observer, "/project").status(None, every_scope=True)["locks"],
    )

    assert calls == [[HOLDER_PROCESS, WAITER_PROCESS]]
    assert lock["holder"] == waiter
    assert store.is_ended(holder)
    assert _releases(store) == [("db", holder, "holder_exited", None, None, waiter)]
    [grant] = bodies(store, waiter)
    assert f"after {holder} exited without releasing it" in grant


def test_exited_waiter_is_skipped_when_both_are_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record_wakes(monkeypatch)
    store, holder, waiter = _queued(tmp_path)
    observer = store.register("codex", "observer", "/project")
    _exited(monkeypatch, {HOLDER_PROCESS, WAITER_PROCESS})

    assert Locks(store, observer, "/project").status(None, every_scope=True)["locks"] == []
    assert store.is_ended(holder)
    assert store.is_ended(waiter)
    assert _releases(store) == [("db", holder, "holder_exited", None, None, None)]


NOTHING_EXITED: set[HostProcess] = set()


@pytest.mark.parametrize("result", [None, NOTHING_EXITED])
def test_live_or_unreadable_processes_keep_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: set[HostProcess] | None
) -> None:
    store, holder, waiter = _queued(tmp_path)
    _exited(monkeypatch, result)

    [lock] = cast(
        "list[dict[str, object]]",
        Locks(store, waiter, "/project").status(None, every_scope=True)["locks"],
    )

    assert lock["holder"] == holder
    assert not store.is_ended(holder)
    assert _releases(store) == []


def test_sessions_without_a_recorded_process_are_not_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    holder = store.register("codex", "holder", "/project")
    locks = Locks(store, holder, "/project")
    locks.acquire(locks.key("db", shared=False), TAKE)
    calls = _exited(monkeypatch, set())

    locks.status(None, every_scope=True)

    assert calls == []


def test_registration_keeps_the_last_known_process(tmp_path: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    store.register("codex", "holder", "/project", host=HOLDER_PROCESS)
    store.register("codex", "holder", "/project")
    with store.transaction() as db:
        row = cast(
            "tuple[int, str]", db.execute("SELECT host_pid,host_started FROM agents").fetchone()
        )
    assert row == HOLDER_PROCESS


def _ps(monkeypatch: pytest.MonkeyPatch, output: str | None) -> None:
    def fake(_arguments: list[str]) -> str | None:
        return output

    monkeypatch.setattr(processes, "_ps", fake)


def _pid(value: int) -> Callable[[], int]:
    def fake() -> int:
        return value

    return fake


def test_host_process_walks_to_the_nearest_host_ancestor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.getppid", _pid(30))
    _ps(
        monkeypatch,
        "   30    20 Thu Nov 12 01:00:03 2026     /bin/zsh\n"
        "   20    10 Thu Nov 12 01:00:02 2026     /Users/me/.local/bin/codex\n"
        "   10     1 Thu Nov 12 01:00:01 2026     /Applications/Some App/codex\n",
    )
    assert host_process("codex") == HostProcess(20, "Thu Nov 12 01:00:02 2026")
    assert host_process("claude") is None
    _ps(monkeypatch, None)
    assert host_process("codex") is None


def test_exited_compares_start_times_and_distrusts_a_partial_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("os.getpid", _pid(99))
    _ps(
        monkeypatch,
        "   99 Thu Nov 12 02:00:00 2026\n 1234 Thu Nov 12 01:00:00 2026\n"
        " 5678 Thu Nov 12 03:00:00 2026\n",
    )
    reused = HostProcess(5678, "Thu Nov 12 01:05:00 2026")
    gone = HostProcess(4321, "Thu Nov 12 01:00:00 2026")
    assert exited([HOLDER_PROCESS, reused, gone]) == {reused, gone}
    _ps(monkeypatch, " 1234 Thu Nov 12 01:00:00 2026\n")
    assert exited([HOLDER_PROCESS, gone]) is None
    _ps(monkeypatch, None)
    assert exited([gone]) is None


def _project_lock(tmp_path: Path, *, shared: bool = False) -> tuple[Store, str]:
    store = Store(tmp_path / "bus.sqlite3")
    create(store, tmp_path)
    worker = "claude:worker"
    invoke(store, tmp_path, "join", "demo", address=worker)
    store.register("codex", "waiter", str(tmp_path))
    locks = Locks(store, worker, str(tmp_path))
    key = locks.key("db", shared=shared)
    locks.acquire(key, TAKE)
    Locks(store, "codex:waiter", str(tmp_path)).acquire(key, TAKE)
    return store, worker


@pytest.mark.parametrize("shared", [False, True])
def test_coordinator_forces_a_worker_lock_to_the_next_waiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, shared: bool
) -> None:
    record_wakes(monkeypatch)
    store, worker = _project_lock(tmp_path, shared=shared)
    locks = Locks(store, "codex:owner", str(tmp_path))

    result = locks.force_release(locks.key("db", shared=shared), "  worker stuck  ")

    assert result["status"] == "forced"
    assert result["previous_holder"] == worker
    assert result["granted_to"] == "codex:waiter"
    assert _releases(store) == [
        ("db", worker, "forced", "codex:owner", "worker stuck", "codex:waiter")
    ]
    assert any(
        "codex:owner took resource 'db' from you. Their reason: worker stuck." in body
        for body in bodies(store, worker)
    )
    assert any(
        "after claude:worker had it force-released by codex:owner" in body
        for body in bodies(store, "codex:waiter")
    )


def test_force_is_refused_without_coordination_reason_or_another_holder(tmp_path: Path) -> None:
    store, worker = _project_lock(tmp_path)
    outsider = store.register("codex", "outsider", str(tmp_path))
    key = Locks(store, outsider, str(tmp_path)).key("db", shared=False)

    with pytest.raises(BusError, match="can force a release"):
        Locks(store, outsider, str(tmp_path)).force_release(key, "mine now")
    with pytest.raises(BusError, match="needs --reason"):
        Locks(store, "codex:owner", str(tmp_path)).force_release(key, "  ")
    with pytest.raises(BusError, match="You hold 'db'"):
        Locks(store, worker, str(tmp_path)).force_release(key, "mine")
    with pytest.raises(BusError, match="is not locked"):
        Locks(store, "codex:owner", str(tmp_path)).force_release(
            Locks(store, worker, str(tmp_path)).key("other", shared=False), "x"
        )
    assert _releases(store) == []


def test_coordinator_cannot_force_a_shared_lock_of_a_non_worker(tmp_path: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    create(store, tmp_path)
    stranger = store.register("codex", "stranger", str(tmp_path))
    locks = Locks(store, stranger, str(tmp_path))
    key = locks.key("db", shared=True)
    locks.acquire(key, TAKE)

    with pytest.raises(BusError, match="can force a release"):
        Locks(store, "codex:owner", str(tmp_path)).force_release(key, "stuck")


def _cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    record_wakes(monkeypatch)
    monkeypatch.setenv("LLM_BUS_DB", str(tmp_path / "bus.sqlite3"))
    monkeypatch.chdir(tmp_path)
    store = Store(tmp_path / "bus.sqlite3")
    holder = store.register("codex", "holder", str(tmp_path))
    locks = Locks(store, holder, str(tmp_path))
    locks.acquire(locks.key("db", shared=False), TAKE)
    return store


def test_owner_terminal_forces_without_a_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _cli(tmp_path, monkeypatch)
    monkeypatch.setattr("llm_bus.cli.commands.interactive_terminal", lambda: True)

    assert main(["lock", "release", "db", "--force", "--reason", "session killed"]) == 0

    result = cast("dict[str, object]", json.loads(capsys.readouterr().out))
    assert result["status"] == "forced"
    assert result["granted_to"] is None
    assert _releases(store) == [
        ("db", "codex:holder", "forced", "owner terminal", "session killed", None)
    ]
    notices = store.inbox("codex:holder")
    assert [notice["sender"] for notice in notices] == ["codex:holder"]
    assert "owner terminal took resource 'db' from you" in notices[0]["body"]


def test_owner_path_needs_an_interactive_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _cli(tmp_path, monkeypatch)

    assert main(["lock", "release", "db", "--force", "--reason", "x"]) == 1
    assert "can run `llm-bus lock release --force`" in capsys.readouterr().err
    monkeypatch.setattr("llm_bus.cli.commands.interactive_terminal", lambda: True)
    assert main(["--format", "agent", "lock", "release", "db", "--force", "--reason", "x"]) == 1
    capsys.readouterr()

    monkeypatch.setenv("CODEX_THREAD_ID", "other")
    assert main(["lock", "release", "db", "--force", "--reason", "x"]) == 1
    assert "can force a release" in capsys.readouterr().err
    assert main(["lock", "release", "db", "--reason", "x"]) == 1
    assert "--reason applies only to a forced release" in capsys.readouterr().err
    assert _releases(store) == []
