"""Recipients record fixed-code work state for local directions without waking anyone."""

# The shared enrolled fixture is imported, so pytest finds it by its registered name.
# pylint: disable=unused-import,clean-code-commented-out-code
# pyright: reportUnusedImport=false

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

import pytest

from llm_bus.cli.commands import registered_command
from llm_bus.cli.guidance import guidance
from llm_bus.diagnostics.messages import message_status
from llm_bus.errors import BusError
from llm_bus.mailbox.schema import SCHEMA_VERSION
from llm_bus.mailbox.store import Store
from llm_bus.reports.lifecycle import TRANSITION_CONFLICT
from tests.coordination.test_delivery import enrolled_setup  # noqa: F401

if TYPE_CHECKING:
    from pathlib import Path

    from llm_bus.coordination.models import Direction
    from llm_bus.mailbox.models import SenderContext

WORKER = "claude:worker"
OWNER = "codex:owner"
MILLISECOND_Z = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z"


def report(
    store: Store,
    message_id: int,
    status: str,
    reason: str | None = None,
    *,
    address: str = WORKER,
) -> object:
    args: dict[str, object] = {"message_id": message_id, "status": status, "reason": reason}
    return registered_command(store, "report", args, address, "")


def rows(store: Store) -> int:
    with store.transaction() as db:
        return cast(
            "tuple[int]", db.execute("SELECT count(*) FROM coordination_work_reports").fetchone()
        )[0]


def work_report(store: Store, message_id: int) -> object:
    return message_status(store.path, message_id)["work_report"]


def direction_id(store: Store, owner: SenderContext, direction: Direction) -> int:
    sent = store.send(owner, WORKER, "Check task", direction=direction)
    return sent["id"]


def test_recipient_records_and_status_shows(
    enrolled: tuple[Store, Direction, SenderContext],
) -> None:
    store, direction, owner = enrolled
    message_id = direction_id(store, owner, direction)
    assert work_report(store, message_id) is None
    store.acknowledge(WORKER, [message_id])
    report(store, message_id, "received")
    report(store, message_id, "proceeding", "started")
    repeated = report(store, message_id, "proceeding", "started")
    shown = cast("dict[str, object]", work_report(store, message_id))
    assert repeated == shown
    assert {k: v for k, v in shown.items() if k != "reported_at"} == {
        "message_id": message_id,
        "revision": 2,
        "status": "proceeding",
        "reason": "started",
    }
    assert re.fullmatch(MILLISECOND_Z, cast("str", shown["reported_at"]))


def test_report_refusals(enrolled: tuple[Store, Direction, SenderContext]) -> None:
    store, direction, owner = enrolled
    message_id = direction_id(store, owner, direction)
    ordinary = store.send(owner, WORKER, "plain")["id"]
    store.acknowledge(WORKER, [ordinary])
    with pytest.raises(BusError, match="Acknowledge the direction"):
        report(store, message_id, "received")
    store.acknowledge(WORKER, [message_id])
    refused = (
        (message_id, "received", None, OWNER, "requires a received local"),
        (999, "received", None, WORKER, "requires a received local"),
        (ordinary, "received", None, WORKER, "requires a received local"),
        (message_id, "bogus", None, WORKER, "Invalid built-in"),
        (message_id, "proceeding", "nonsense", WORKER, "Invalid built-in"),
    )
    for target, status, reason, address, message in refused:
        with pytest.raises(BusError, match=message):
            report(store, target, status, reason, address=address)
    assert rows(store) == 0
    report(store, message_id, "proceeding", "started")
    with pytest.raises(BusError, match=TRANSITION_CONFLICT):
        report(store, message_id, "received")
    report(store, message_id, "completed")
    with pytest.raises(BusError, match=TRANSITION_CONFLICT):
        report(store, message_id, "proceeding", "started")
    assert work_report(store, message_id) is not None
    assert cast("dict[str, object]", work_report(store, message_id))["revision"] == 2


def counts(store: Store) -> tuple[object, ...]:
    with store.transaction() as db:
        tables = [
            cast("tuple[int]", db.execute(f"SELECT count(*) FROM {name}").fetchone())[0]  # noqa: S608
            for name in ("messages", "wake_dispatch")
        ]
    return (*tables, store.pending_count(OWNER), guidance(store, OWNER, "/", full=False))


def test_report_never_wakes_or_prompts(
    enrolled: tuple[Store, Direction, SenderContext],
) -> None:
    store, direction, owner = enrolled
    message_id = direction_id(store, owner, direction)
    store.acknowledge(WORKER, [message_id])
    before = counts(store)
    report(store, message_id, "received")
    report(store, message_id, "failed", "execution_failed")
    assert counts(store) == before


def test_upgrade_from_14(enrolled: tuple[Store, Direction, SenderContext], tmp_path: Path) -> None:
    store, direction, owner = enrolled
    message_id = direction_id(store, owner, direction)
    with store.transaction() as db:
        db.execute("DROP TABLE coordination_work_reports")
        db.execute("PRAGMA user_version = 14")
    upgraded = Store(tmp_path / "bus.db")
    with upgraded.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 15
        assert db.execute("SELECT count(*) FROM coordination_work_reports").fetchone()[0] == 0
        assert db.execute("SELECT id FROM messages WHERE id=?", (message_id,)).fetchone()


def test_reupgrade_after_rollback(
    enrolled: tuple[Store, Direction, SenderContext], tmp_path: Path
) -> None:
    store, direction, owner = enrolled
    message_id = direction_id(store, owner, direction)
    store.acknowledge(WORKER, [message_id])
    report(store, message_id, "received")
    with store.transaction() as db:
        db.execute("PRAGMA user_version = 14")
    upgraded = Store(tmp_path / "bus.db")
    with upgraded.transaction() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 15
    assert rows(upgraded) == 1
