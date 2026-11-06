"""Owner joins bind consent to the exact native session."""

# Explicit authority and command arguments keep the lifecycle matrix independently readable.
# pylint: disable=duplicate-code

from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from llm_bus.coordination.authority import authorize
from llm_bus.coordination.models import Direction
from llm_bus.errors import BusError
from llm_bus.mailbox.store import Store
from llm_bus.routing import routing_scope
from tests.coordination.test_enrollment import create, invoke


def test_owner_join_and_durable_arrival(tmp_path: Path) -> None:
    mailbox = Store(tmp_path / "bus.sqlite3")
    mailbox.register("codex", "owner", str(tmp_path))
    mailbox.register("claude", "worker", str(tmp_path))
    create(mailbox, tmp_path)
    first = invoke(mailbox, tmp_path, "join", "demo", address="claude:worker")
    mailbox = Store(mailbox.path)
    second = invoke(mailbox, tmp_path, "join", "demo", address="claude:worker")
    assert first["grants"] == second["grants"]
    with mailbox.transaction() as db:
        assert db.execute("SELECT count(*) FROM coordination_local_availability").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 1
    mailbox.end_session("claude", "worker")
    with pytest.raises(BusError, match="live native session"):
        invoke(mailbox, tmp_path, "join", "demo", address="claude:worker")
    mailbox.register("claude", "worker", str(tmp_path))
    third = invoke(mailbox, tmp_path, "join", "demo", address="claude:worker")
    assert third["grants"] != first["grants"]
    with mailbox.transaction() as db:
        assert db.execute("SELECT count(*) FROM coordination_local_availability").fetchone()[0] == 2


@pytest.mark.parametrize("ending", ["leave", "revoke", "replace", "scope", "expiry", "close"])
def test_lifecycle_ends_authority(tmp_path: Path, ending: str) -> None:
    mailbox = Store(tmp_path / "bus.sqlite3")
    record = create(mailbox, tmp_path)
    invoke(mailbox, tmp_path, "join", "demo", address="claude:worker")
    with mailbox.transaction() as db:
        grant_id = cast(
            "tuple[str]", db.execute("SELECT grant_id FROM coordination_grants").fetchone()
        )[0]
    direction = Direction(
        1, str(record["project_id"]), str(record["generation"]), grant_id, "assign"
    )
    if ending == "leave":
        invoke(mailbox, tmp_path, "coordination", "leave", "demo", address="claude:worker")
    elif ending == "revoke":
        invoke(mailbox, tmp_path, "coordination", "revoke", "demo", "--recipient", "claude:worker")
    elif ending == "replace":
        invoke(
            mailbox,
            tmp_path,
            "coordination",
            "replace",
            "demo",
            "--coordinator",
            "codex:owner",
            "--generation",
            str(uuid4()),
        )
    elif ending == "scope":
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        mailbox.register("claude", "worker", str(elsewhere))
    elif ending == "expiry":
        with mailbox.transaction() as db:
            db.execute(
                "UPDATE agents SET last_seen='2020-01-01T00:00:00Z' WHERE address='claude:worker'"
            )
    else:
        mailbox.end_session("claude", "worker")
    with mailbox.transaction() as db:
        assert not authorize(
            db,
            sender="codex:owner",
            recipient="claude:worker",
            scope=routing_scope(str(tmp_path)),
            direction=direction,
            now=datetime.now(UTC),
        ).verified
        assert (
            db.execute(
                "SELECT ended_at FROM coordination_session_consents WHERE grant_id=?", (grant_id,)
            ).fetchone()[0]
            is not None
        )


def test_turn_refresh_preserves_consent_and_project_conflict(tmp_path: Path) -> None:
    mailbox = Store(tmp_path / "bus.sqlite3")
    create(mailbox, tmp_path)
    first = invoke(mailbox, tmp_path, "join", "demo", address="claude:worker")
    mailbox.register("claude", "worker", str(tmp_path))
    second = invoke(Store(mailbox.path), tmp_path, "join", "demo", address="claude:worker")
    assert first["grants"] == second["grants"]
    with pytest.raises(BusError, match="Leave current"):
        invoke(
            mailbox,
            tmp_path,
            "coordinate",
            "other",
            "--objective",
            "Other",
            "--beads-workspace",
            str(tmp_path),
            "--memory-ticket",
            "bus-2",
            address="claude:worker",
        )


def test_delayed_close_does_not_end_reused_label_owner(tmp_path: Path) -> None:
    mailbox = Store(tmp_path / "bus.db")
    create(mailbox, tmp_path)
    mailbox.register("claude", "worker", str(tmp_path), label="builder")
    invoke(mailbox, tmp_path, "join", "demo", address="claude:worker")
    mailbox.end_session("claude", "worker")
    mailbox.register("claude", "replacement", str(tmp_path), label="builder")
    invoke(mailbox, tmp_path, "join", "demo", address="claude:replacement")
    mailbox.end_session("claude", "worker")
    with mailbox.transaction() as db:
        assert db.execute(
            "SELECT ended_at FROM coordination_session_consents WHERE address='claude:replacement'"
        ).fetchone() == (None,)
    assert mailbox.resolve_label("builder", str(tmp_path)) == "claude:replacement"


def test_leaving_other_project_preserves_current_consent(tmp_path: Path) -> None:
    mailbox = Store(tmp_path / "bus.db")
    create(mailbox, tmp_path)
    first = invoke(mailbox, tmp_path, "join", "demo", address="claude:worker")
    mailbox.register("codex", "other", str(tmp_path))
    invoke(
        mailbox,
        tmp_path,
        "coordinate",
        "other",
        "--objective",
        "Other",
        "--beads-workspace",
        str(tmp_path),
        "--memory-ticket",
        "bus-2",
        address="codex:other",
    )
    invoke(mailbox, tmp_path, "coordination", "leave", "other", address="claude:worker")
    second = invoke(mailbox, tmp_path, "join", "demo", address="claude:worker")
    assert first["grants"] == second["grants"]
