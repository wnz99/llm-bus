"""Arrivals use grant identity and share the atomic owner join transaction."""

# Each regression states its security-sensitive command and expected rows explicitly.
# pylint: disable=duplicate-code

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from llm_bus.coordination import cli
from llm_bus.mailbox.models import SenderContext
from llm_bus.mailbox.schema import SCHEMA_VERSION
from llm_bus.mailbox.store import Store
from tests.coordination.test_enrollment import create, invoke


def test_explicit_grant_dedup_survives_ack_leave_and_reopen(tmp_path: Path) -> None:
    mailbox = Store(tmp_path / "bus.db")
    create(mailbox, tmp_path)
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
    mailbox.send(
        SenderContext("claude", "worker", str(tmp_path)),
        "codex:owner",
        "Worker claude:worker joined coordination project demo.",
    )
    first = invoke(mailbox, tmp_path, "join", "demo", address="claude:worker")
    with mailbox.transaction() as db:
        db.execute("UPDATE messages SET acknowledged_at=sent_at")
        assert db.execute("SELECT count(*) FROM coordination_session_consents").fetchone()[0] == 0
    invoke(mailbox, tmp_path, "coordination", "leave", "demo", address="claude:worker")
    mailbox = Store(mailbox.path)
    second = invoke(mailbox, tmp_path, "join", "demo", address="claude:worker")
    assert first["grants"] == second["grants"]
    mailbox.end_session("claude", "worker")
    with mailbox.transaction() as db:
        assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 2
        assert db.execute("SELECT revoked_at FROM coordination_grants").fetchone()[0] is None


def test_rollback_before_dedup_never_wakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mailbox = Store(tmp_path / "bus.db")
    create(mailbox, tmp_path)
    with mailbox.transaction() as db:
        db.execute(
            "CREATE TRIGGER reject_arrival BEFORE INSERT ON coordination_local_availability BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
    wakes: list[object] = []

    def record_wake(*args: object, **_kwargs: object) -> None:
        wakes.append(args)

    monkeypatch.setattr(cli, "dispatch_pending", record_wake)
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        invoke(mailbox, tmp_path, "join", "demo", address="claude:worker")
    with mailbox.transaction() as db:
        for table in (
            "coordination_grants",
            "coordination_session_consents",
            "coordination_targets",
            "messages",
            "coordination_local_availability",
            "wake_dispatch",
        ):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0  # noqa: S608
        assert (
            db.execute(
                "SELECT count(*) FROM coordination_enrollments WHERE role='worker'"
            ).fetchone()[0]
            == 0
        )
    assert wakes == []


def test_concurrent_joins_queue_one_arrival_without_live_coordinator(tmp_path: Path) -> None:
    mailbox = Store(tmp_path / "bus.db")
    create(mailbox, tmp_path)
    mailbox.end_session("codex", "owner")

    def join(_index: int) -> None:
        invoke(Store(mailbox.path), tmp_path, "join", "demo", address="claude:worker")

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(join, range(2)))
    with mailbox.transaction() as db:
        assert db.execute("SELECT count(*) FROM coordination_local_availability").fetchone()[0] == 1
        assert db.execute("SELECT requested_generation FROM wake_dispatch").fetchone()[0] == 1


@pytest.mark.parametrize("version", [8, 9, 10, 11])
def test_additive_schema_upgrade(tmp_path: Path, version: int) -> None:
    mailbox = Store(tmp_path / "bus.db")
    create(mailbox, tmp_path)
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
    with mailbox.transaction() as db:
        db.execute("DROP TABLE coordination_local_availability")
        db.execute("DROP TABLE coordination_session_consents")
        db.execute("DROP TABLE IF EXISTS coordination_availability_events")
        db.execute(f"PRAGMA user_version={version}")
    mailbox = Store(mailbox.path)
    with mailbox.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert (
            db.execute("SELECT count(*) FROM coordination_availability_events").fetchone()[0] == 0
        )
        assert db.execute("SELECT count(*) FROM coordination_grants").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM coordination_session_consents").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM coordination_local_availability").fetchone()[0] == 0


def test_wake_observes_committed_arrival_and_failure_preserves_join(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox = Store(tmp_path / "bus.db")
    create(mailbox, tmp_path)
    observed: list[int] = []

    def failed_wake(_mailbox: object, _recipient: object, *, trigger_id: int) -> dict[str, str]:
        with sqlite3.connect(mailbox.path) as db:
            assert db.execute(
                "SELECT message_id FROM coordination_local_availability"
            ).fetchone() == (trigger_id,)
            assert db.execute("SELECT count(*) FROM coordination_targets").fetchone()[0] == 1
        observed.append(trigger_id)
        return {"status": "failed"}

    monkeypatch.setattr(cli, "dispatch_pending", failed_wake)
    result = invoke(mailbox, tmp_path, "join", "demo", address="claude:worker")
    assert result["availability_wake"] == {"status": "failed"}
    invoke(mailbox, tmp_path, "join", "demo", address="claude:worker")
    assert len(observed) == 1
