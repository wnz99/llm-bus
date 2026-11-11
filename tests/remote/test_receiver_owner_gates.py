"""Receiver side: a worker of a remote-coordinated project cannot run its owner commands."""

# pylint: disable=duplicate-code

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from llm_bus.errors import BusError
from tests.coordination.test_enrollment import invoke
from tests.remote.test_remote_approvals import COORDINATOR, Receiver, references

if TYPE_CHECKING:
    from pathlib import Path

WORKER = "claude:worker"


@pytest.fixture(name="receiver")
def receiver_fixture(tmp_path: Path) -> Receiver:
    receiver = Receiver(tmp_path, gates=("merge",))
    receiver.join()
    return receiver


def as_worker(receiver: Receiver, action: str, *argv: str) -> dict[str, object]:
    return invoke(
        receiver.store, receiver.repo, "coordination", action, "demo", *argv, address=WORKER
    )


@pytest.mark.parametrize(
    ("action", "argv", "verb"),
    [
        ("replace", ("--generation", str(uuid4()), "--coordinator", WORKER), "replace the"),
        ("amend", ("--delegate", "merge:main"), "amend its delegation"),
        ("amend", ("--no-delegation",), "amend its delegation"),
        ("grant", ("--recipient", WORKER, "--until-revoked"), "grant"),
        ("revoke", (), "revoke"),
        ("target", ("--recipient", WORKER, "--grant-id", str(uuid4())), "set direction"),
    ],
)
def test_worker_cannot_run_receiver_owner_commands(
    receiver: Receiver, action: str, argv: tuple[str, ...], verb: str
) -> None:
    with pytest.raises(BusError, match=f"A worker of this project cannot {verb}"):
        as_worker(receiver, action, *argv)
    assert receiver.owner("coordination", "show", "demo")["revoked_at"] is None


def test_worker_cannot_set_up_the_project_again(receiver: Receiver) -> None:
    with pytest.raises(BusError, match="A worker of this project cannot set up the project"):
        as_worker(
            receiver,
            "setup",
            "--project-id",
            receiver.project_id,
            "--generation",
            str(uuid4()),
            "--coordinator",
            COORDINATOR,
            *references(receiver.path),
        )


def test_worker_who_left_still_cannot_replace(receiver: Receiver) -> None:
    as_worker(receiver, "leave")
    with pytest.raises(BusError, match="A worker of this project cannot replace the"):
        as_worker(receiver, "replace", "--generation", str(uuid4()), "--coordinator", WORKER)


def test_receiver_owner_keeps_its_commands(receiver: Receiver) -> None:
    receiver.owner("coordination", "amend", "demo", "--no-delegation")
    receiver.owner("coordination", "revoke", "demo", "--recipient", WORKER)
    shown = receiver.owner(
        "coordination",
        "replace",
        "demo",
        "--generation",
        str(uuid4()),
        "--coordinator",
        COORDINATOR,
    )
    assert shown["coordinator"] == COORDINATOR
    assert shown["enrollments"] == []
