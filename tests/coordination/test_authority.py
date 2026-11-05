"""Authority must fail closed before enrollment."""

from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from llm_bus.cli.parser import build_parser
from llm_bus.coordination.authority import authorize
from llm_bus.coordination.cli import run
from llm_bus.coordination.models import Direction
from llm_bus.mailbox.store import Store
from llm_bus.routing import routing_scope


def test_empty_store_denies(tmp_path: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    direction = Direction(1, str(uuid4()), str(uuid4()), str(uuid4()), "assign")
    with store.transaction() as db:
        decision = authorize(
            db,
            sender="codex:a",
            recipient="codex:b",
            scope="/repo",
            direction=direction,
            now=datetime.now(UTC),
        )
    assert not decision.verified
    assert decision.reason == "unknown_project"


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("none", "verified"),
        ("scope", "wrong_scope"),
        ("sender", "wrong_coordinator"),
        ("generation", "stale_generation"),
        ("grant_id", "invalid_direction"),
        ("enrollment", "not_enrolled"),
        ("revoke", "revoked"),
        ("expiry", "expired"),
        ("operation", "operation_denied"),
    ],
)
def test_authority_receiver_matrix(tmp_path: Path, mutation: str, reason: str) -> None:
    (tmp_path / ".beads").mkdir()
    mailbox = Store(tmp_path / "bus.sqlite3")

    def command(*argv: str, address: str = "codex:owner") -> dict[str, object]:
        return run(mailbox, vars(build_parser().parse_args(argv)), address, str(tmp_path))

    record = command(
        "coordinate",
        "demo",
        "--objective",
        "Check receiver authority",
        "--beads-workspace",
        str(tmp_path),
        "--memory-ticket",
        "bus-1",
    )
    command("coordination", "grant", "demo", "--recipient", "claude:worker", "--until-revoked")
    command("join", "demo", address="claude:worker")
    scope = routing_scope(str(tmp_path))
    with mailbox.transaction() as db:
        grant_id = cast(
            "tuple[str]", db.execute("SELECT grant_id FROM coordination_grants").fetchone()
        )[0]
        if mutation == "enrollment":
            db.execute("DELETE FROM coordination_enrollments")
        if mutation == "revoke":
            db.execute("UPDATE coordination_grants SET revoked_at='revoked'")
        if mutation == "expiry":
            db.execute("UPDATE coordination_grants SET expires_at='2020-01-01T00:00:00Z'")
        if mutation == "operation":
            db.execute("UPDATE coordination_grants SET operations_json='[]'")
        direction = Direction(
            1,
            str(record["project_id"]),
            str(uuid4()) if mutation == "generation" else str(record["generation"]),
            str(uuid4()) if mutation == "grant_id" else grant_id,
            "assign",
        )
        decision = authorize(
            db,
            sender="codex:other" if mutation == "sender" else "codex:owner",
            recipient="claude:worker",
            scope="wrong" if mutation == "scope" else scope,
            direction=direction,
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert decision.reason == reason
        assert decision.verified == (reason == "verified")
