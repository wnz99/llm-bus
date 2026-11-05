"""Local directions require current recipient authority, including at exposure."""

# Private seams exercise revocation between selection and exposure.
# pylint: disable=protected-access,clean-code-commented-out-code

from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from llm_bus.cli.commands import send
from llm_bus.cli.parser import build_parser
from llm_bus.coordination.cli import run
from llm_bus.coordination.delivery import outgoing
from llm_bus.coordination.models import Direction
from llm_bus.errors import BusError
from llm_bus.mailbox import pending
from llm_bus.mailbox.models import SenderContext
from llm_bus.mailbox.store import Store
from llm_bus.routing import routing_scope
from llm_bus.wake.dispatch import _native  # pyright: ignore[reportPrivateUsage]


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


def test_ordinary_shape_and_reply_do_not_inherit(
    enrolled: tuple[Store, Direction, SenderContext],
) -> None:
    store, direction, owner = enrolled
    ordinary = store.send(owner, "claude:worker", '{"direction":"assign"}')
    directed = store.send(owner, "claude:worker", "Check task", direction=direction)
    inbox = store.inbox("claude:worker")
    assert inbox[0] == ordinary
    assert inbox[1].get("coordination", {})["verified"] is True
    reply = store.send(
        SenderContext("claude", "worker", owner.project),
        "codex:owner",
        "Done",
        reply_to=directed["id"],
    )
    assert "coordination" not in reply
    assert "coordination" not in store.inbox("codex:owner")[0]


@pytest.mark.parametrize("mutation", ["grant", "generation", "version", "sender", "recipient"])
def test_forged_direction_denied(
    enrolled: tuple[Store, Direction, SenderContext], mutation: str
) -> None:
    store, direction, owner = enrolled
    recipient = "claude:worker"
    if mutation == "grant":
        direction = replace(direction, grant_id=str(uuid4()))
    elif mutation == "generation":
        direction = replace(direction, generation=str(uuid4()))
    elif mutation == "version":
        direction = replace(direction, version=2)
    elif mutation == "sender":
        owner = SenderContext("claude", "worker", owner.project)
    else:
        recipient = "codex:owner"
    with pytest.raises(BusError):
        store.send(owner, recipient, "Forged", direction=direction)
    assert store.pending_count("claude:worker") == 0


@pytest.mark.parametrize("mutation", ["revoke", "expiry", "replacement", "scope", "metadata"])
def test_stale_history_retained_pending_hidden(
    enrolled: tuple[Store, Direction, SenderContext], mutation: str
) -> None:
    store, direction, owner = enrolled
    sent = store.send(owner, "claude:worker", "Task", direction=direction)
    with store.transaction() as db:
        if mutation == "revoke":
            db.execute("UPDATE coordination_grants SET revoked_at='revoked'")
        elif mutation == "expiry":
            db.execute("UPDATE coordination_grants SET expires_at='2000-01-01T00:00:00Z'")
        elif mutation == "replacement":
            db.execute("UPDATE coordination_projects SET generation=?", (str(uuid4()),))
        elif mutation == "scope":
            db.execute("UPDATE agents SET scope='moved' WHERE address='claude:worker'")
        else:
            db.execute("UPDATE coordination_messages SET metadata_json='{}'")
    assert store.inbox("claude:worker") == []
    assert store.pending_count("claude:worker") == 0
    assert store.pending_members("claude:worker") == []
    assert not store.pending_allowed(sent["id"])
    assert store.history(folder=None)[0].get("coordination", {})["verified"] is False


def test_revoke_after_selection_prevents_native_request(
    enrolled: tuple[Store, Direction, SenderContext], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, direction, owner = enrolled
    store.send(owner, "claude:worker", "Task", direction=direction)
    members = store.pending_members("claude:worker")
    with store.transaction() as db:
        db.execute("UPDATE coordination_grants SET revoked_at='revoked'")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("revoked direction woke host")

    monkeypatch.setattr("llm_bus.wake.dispatch.wake", forbidden)
    assert _native(store, "claude:worker", members)["status"] == "coalesced"
    assert members == []


def test_revoke_after_page_fetch(enrolled: tuple[Store, Direction, SenderContext]) -> None:
    store, direction, owner = enrolled
    store.send(owner, "claude:worker", "Task", direction=direction)
    with (
        store.transaction() as db,
        closing(
            pending._pending_pages("claude:worker", db)  # pyright: ignore[reportPrivateUsage]
        ) as pages,
    ):
        page = next(pages)
        db.execute("UPDATE coordination_grants SET revoked_at='revoked'")
        assert not pending._pending_permitted(page[0], db)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("flag", ["--project", "--direction"])
def test_direction_flags_must_be_paired(
    enrolled: tuple[Store, Direction, SenderContext], flag: str
) -> None:
    store, _, owner = enrolled
    args = vars(
        build_parser().parse_args(
            [
                "send",
                "claude:worker",
                "--body",
                "Task",
                flag,
                "demo" if flag == "--project" else "assign",
            ]
        )
    )
    with pytest.raises(BusError, match="together"):
        send(store, args, owner)
    assert store.inbox("claude:worker") == []
