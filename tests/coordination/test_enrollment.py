"""Durable explicit owner enrollment, replacement and expiry."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from llm_bus.cli import app
from llm_bus.cli.parser import build_parser
from llm_bus.coordination.authority import authorize
from llm_bus.coordination.cli import run
from llm_bus.coordination.models import Direction
from llm_bus.errors import BusError
from llm_bus.mailbox.store import Store
from llm_bus.routing import routing_scope
from tests.local.support import linked_worktree


def invoke(store: Store, cwd: Path, *argv: str, address: str = "codex:owner") -> dict[str, object]:
    return run(store, vars(build_parser().parse_args(argv)), address, str(cwd))


def create(store: Store, cwd: Path) -> dict[str, object]:
    (cwd / ".beads").mkdir(exist_ok=True)
    return invoke(
        store,
        cwd,
        "coordinate",
        "demo",
        "--objective",
        "Build safely",
        "--beads-workspace",
        str(cwd),
        "--memory-ticket",
        "bus-1",
    )


def test_durable_exact_session_enrollment_and_regrant(tmp_path: Path) -> None:
    mailbox = Store(tmp_path / "bus.sqlite3")
    record = create(mailbox, tmp_path)
    with pytest.raises(BusError, match="exact owner grant"):
        invoke(mailbox, tmp_path, "join", "demo", address="claude:worker")
    invoke(
        mailbox,
        tmp_path,
        "coordination",
        "grant",
        "demo",
        "--recipient",
        "claude:worker",
        "--until-revoked",
    )
    invoke(mailbox, tmp_path, "join", "demo", address="claude:worker")
    mailbox = Store(mailbox.path)
    with mailbox.transaction() as db:
        grant_id = cast(
            "tuple[str]", db.execute("SELECT grant_id FROM coordination_grants").fetchone()
        )[0]
        direction = Direction(
            1, str(record["project_id"]), str(record["generation"]), grant_id, "assign"
        )
        assert authorize(
            db,
            sender="codex:owner",
            recipient="claude:worker",
            scope=routing_scope(str(tmp_path)),
            direction=direction,
            now=datetime.now(UTC),
        ).verified
        assert not authorize(
            db,
            sender="codex:owner",
            recipient="claude:other",
            scope=routing_scope(str(tmp_path)),
            direction=direction,
            now=datetime.now(UTC),
        ).verified
    invoke(mailbox, tmp_path, "coordination", "revoke", "demo", "--recipient", "claude:worker")
    invoke(
        mailbox,
        tmp_path,
        "coordination",
        "grant",
        "demo",
        "--recipient",
        "claude:worker",
        "--until-revoked",
    )
    invoke(mailbox, tmp_path, "join", "demo", address="claude:worker")
    with mailbox.transaction() as db:
        assert not authorize(
            db,
            sender="codex:owner",
            recipient="claude:worker",
            scope=routing_scope(str(tmp_path)),
            direction=direction,
            now=datetime.now(UTC),
        ).verified
        assert db.execute("SELECT count(*) FROM coordination_grants").fetchone()[0] == 2


def test_replace_invalidates_and_never_reuses_generation(tmp_path: Path) -> None:
    mailbox = Store(tmp_path / "bus.sqlite3")
    record = create(mailbox, tmp_path)
    generation = str(uuid4())
    invoke(
        mailbox,
        tmp_path,
        "coordination",
        "replace",
        "demo",
        "--coordinator",
        "codex:new",
        "--generation",
        generation,
    )
    with mailbox.transaction() as db:
        assert db.execute("SELECT count(*) FROM coordination_enrollments").fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        invoke(
            mailbox,
            tmp_path,
            "coordination",
            "replace",
            "demo",
            "--coordinator",
            "codex:owner",
            "--generation",
            str(record["generation"]),
        )
    assert invoke(mailbox, tmp_path, "coordination", "show", "demo")["generation"] == generation
    with pytest.raises(BusError, match="Different coordinator"):
        invoke(mailbox, tmp_path, "coordinate", "demo")


def test_expiry_and_missing_prerequisites(tmp_path: Path) -> None:
    mailbox = Store(tmp_path / "bus.sqlite3")
    with pytest.raises(BusError):
        invoke(mailbox, tmp_path, "coordinate", "demo")
    create(mailbox, tmp_path)
    for expiry in ("2020-01-01T00:00:00Z", "2030-01-01", "bad"):
        with pytest.raises(BusError):
            invoke(
                mailbox,
                tmp_path,
                "coordination",
                "grant",
                "demo",
                "--recipient",
                "claude:w",
                "--expires-at",
                expiry,
            )
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    invoke(
        mailbox,
        tmp_path,
        "coordination",
        "grant",
        "demo",
        "--recipient",
        "claude:w",
        "--expires-at",
        future,
    )
    with mailbox.transaction() as db:
        db.execute("UPDATE coordination_grants SET expires_at='2020-01-01T00:00:00Z'")
    with pytest.raises(BusError, match="expired"):
        invoke(mailbox, tmp_path, "join", "demo", address="claude:w")
    with mailbox.transaction() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM coordination_enrollments WHERE address='claude:w'"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(
    "extra", [{"unknown": 1}, {"version": True}, {"version": 2}, {"operation": "grant"}]
)
def test_direction_unknown_fields_and_versions(extra: dict[str, object]) -> None:
    fields: dict[str, object] = {
        "version": 1,
        "project_id": str(uuid4()),
        "generation": str(uuid4()),
        "grant_id": str(uuid4()),
        "operation": "assign",
    }
    with pytest.raises(BusError):
        Direction.parse({**fields, **extra})


def test_one_project_per_session_and_leave(tmp_path: Path) -> None:
    mailbox = Store(tmp_path / "bus.sqlite3")
    create(mailbox, tmp_path)
    with pytest.raises(BusError, match="Leave current"):
        invoke(
            mailbox,
            tmp_path,
            "coordinate",
            "other",
            "--objective",
            "test",
            "--beads-workspace",
            str(tmp_path),
            "--memory-ticket",
            "bus-2",
        )
    invoke(mailbox, tmp_path, "coordination", "leave", "demo")
    invoke(
        mailbox,
        tmp_path,
        "coordinate",
        "other",
        "--objective",
        "test",
        "--beads-workspace",
        str(tmp_path),
        "--memory-ticket",
        "bus-2",
    )


def test_commands_reuse_native_main_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BUS_DB", str(tmp_path / "bus.sqlite3"))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "worker")

    def reject(_address: str) -> None:
        raise BusError("Main agents only")

    monkeypatch.setattr(app, "require_main_agent", reject)
    assert app.main(["join", "demo"]) == 1
    monkeypatch.delenv("CODEX_THREAD_ID")
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    assert app.main(["coordinate", "demo"]) == 1


def test_enrollment_uses_linked_worktree_scope(tmp_path: Path) -> None:
    repo, worktree, _git = linked_worktree(tmp_path)
    mailbox = Store(tmp_path / "bus.sqlite3")
    record = create(mailbox, repo)
    assert (
        invoke(mailbox, worktree, "coordination", "show", "demo")["project_id"]
        == record["project_id"]
    )


def test_native_child_rejected_before_enrollment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native_home = tmp_path / "codex"
    native_home.mkdir()
    with sqlite3.connect(native_home / "state_5.sqlite") as db:
        db.execute("CREATE TABLE thread_spawn_edges(parent_thread_id,child_thread_id,status)")
        db.execute("INSERT INTO thread_spawn_edges VALUES ('parent','child','open')")
    monkeypatch.setenv("CODEX_HOME", str(native_home))
    monkeypatch.setenv("CODEX_THREAD_ID", "child")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setenv("LLM_BUS_DB", str(tmp_path / "bus.sqlite3"))
    assert app.main(["join", "demo"]) == 1
