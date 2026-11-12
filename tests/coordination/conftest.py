"""Coordination fixtures never submit native host wakes."""

from pathlib import Path
from typing import cast

import pytest

from llm_bus.cli.parser import build_parser
from llm_bus.coordination import cli
from llm_bus.coordination.cli import run
from llm_bus.coordination.delivery import outgoing
from llm_bus.coordination.models import Direction
from llm_bus.mailbox.models import SenderContext
from llm_bus.mailbox.store import Store
from llm_bus.routing import routing_scope


@pytest.fixture(autouse=True)
def isolated_join_wake(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_host_wake(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"status": "unsupported"}

    monkeypatch.setattr(cli, "dispatch_pending", no_host_wake)


@pytest.fixture(name="enrolled")
def enrolled_setup(tmp_path: Path) -> tuple[Store, Direction, SenderContext]:
    (tmp_path / ".beads").mkdir()
    store = Store(tmp_path / "bus.db")
    owner = SenderContext("codex", "owner", str(tmp_path))
    store.register("codex", "owner", str(tmp_path))
    store.register("claude", "worker", str(tmp_path))

    def command(*argv: str, address: str = "codex:owner") -> dict[str, object]:
        return run(store, vars(build_parser().parse_args(argv)), address, str(tmp_path))

    command(
        "coordinate",
        "demo",
        "--objective",
        "Test authority",
        "--beads-workspace",
        str(tmp_path),
        "--memory-ticket",
        "delivery-1",
    )
    command("coordination", "grant", "demo", "--recipient", "claude:worker", "--until-revoked")
    command("join", "demo", address="claude:worker")
    with store.transaction() as db:
        grant = cast(
            "tuple[str]", db.execute("SELECT grant_id FROM coordination_grants").fetchone()
        )[0]
    command("coordination", "target", "demo", "--recipient", "claude:worker", "--grant-id", grant)
    with store.transaction() as db:
        direction = outgoing(
            db,
            sender="codex:owner",
            scope=routing_scope(str(tmp_path)),
            label="demo",
            recipient="claude:worker",
            operation="assign",
        )
    return store, direction, owner
