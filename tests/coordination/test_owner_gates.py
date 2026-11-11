"""Owner actions need the coordinator role; workers of the generation are refused."""

# Each scenario states its owner commands explicitly.
# pylint: disable=duplicate-code

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest

from llm_bus.errors import BusError
from tests.coordination.test_approvals import DELEGATION, Project
from tests.coordination.test_enrollment import invoke

if TYPE_CHECKING:
    from pathlib import Path

OWNER = "codex:owner"
WORKER = "claude:worker"
FRESH = "codex:fresh"
WORKER_REPLACE = "A worker of this project cannot replace the coordinator"


@pytest.fixture(autouse=True)
def no_lock_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_timer(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("llm_bus.locks.service.schedule_check", no_timer)


@pytest.fixture(name="project")
def project_fixture(tmp_path: Path) -> Project:
    project = Project(tmp_path, *DELEGATION, "--owner-decisions")
    project.store.register("codex", "fresh", str(tmp_path))
    return project


def act(project: Project, action: str, *argv: str, address: str) -> dict[str, object]:
    return invoke(
        project.store, project.path, "coordination", action, "demo", *argv, address=address
    )


def replace(project: Project, address: str, coordinator: str = FRESH) -> dict[str, object]:
    argv = ("--generation", str(uuid4()), "--coordinator", coordinator)
    return act(project, "replace", *argv, address=address)


def setup_again(project: Project, address: str) -> dict[str, object]:
    argv = ("--project-id", str(uuid4()), "--generation", str(uuid4()), "--coordinator", FRESH)
    references = ("--objective", "Take over", "--beads-workspace", str(project.path))
    return act(project, "setup", *argv, *references, "--memory-ticket", "bus-2", address=address)


@pytest.mark.parametrize(
    ("action", "argv", "verb"),
    [
        ("grant", ("--recipient", FRESH, "--until-revoked"), "grant"),
        ("revoke", ("--recipient", WORKER), "revoke"),
        ("target", ("--recipient", FRESH, "--grant-id", str(uuid4())), "set direction targets"),
    ],
)
def test_worker_and_fresh_session_cannot_run_coordinator_actions(
    project: Project, action: str, argv: tuple[str, ...], verb: str
) -> None:
    for address in (WORKER, FRESH):
        with pytest.raises(BusError, match=f"Only the project's coordinator may {verb}"):
            act(project, action, *argv, address=address)


def test_worker_cannot_amend_replace_or_set_up(project: Project) -> None:
    with pytest.raises(BusError, match="Only the enrolled coordinator may amend delegation"):
        act(project, "amend", "--no-delegation", address=WORKER)
    with pytest.raises(BusError, match=WORKER_REPLACE):
        replace(project, WORKER, coordinator=WORKER)
    with pytest.raises(BusError, match="A worker of this project cannot set up the project"):
        setup_again(project, WORKER)
    assert act(project, "show", address=OWNER)["coordinator"] == OWNER


def test_worker_who_left_still_cannot_replace(project: Project) -> None:
    act(project, "leave", address=WORKER)
    with pytest.raises(BusError, match=WORKER_REPLACE):
        replace(project, WORKER, coordinator=WORKER)


def test_revoked_worker_still_cannot_replace(project: Project) -> None:
    act(project, "revoke", "--recipient", WORKER, address=OWNER)
    with pytest.raises(BusError, match=WORKER_REPLACE):
        replace(project, WORKER, coordinator=WORKER)


def test_coordinator_runs_every_owner_action(project: Project) -> None:
    granted = act(project, "grant", "--recipient", FRESH, "--until-revoked", address=OWNER)
    grant_id = next(
        row["grant_id"]
        for row in cast("list[dict[str, object]]", granted["grants"])
        if row["recipient"] == FRESH
    )
    act(project, "target", "--recipient", FRESH, "--grant-id", str(grant_id), address=OWNER)
    act(project, "revoke", "--recipient", FRESH, address=OWNER)
    act(project, "amend", "--no-delegation", address=OWNER)
    shown = replace(project, OWNER, coordinator=OWNER)
    assert shown["coordinator"] == OWNER
    assert [row["role"] for row in cast("list[dict[str, str]]", shown["enrollments"])] == [
        "coordinator"
    ]


def test_fresh_session_replaces_a_lost_coordinator(project: Project) -> None:
    shown = replace(project, FRESH)
    assert shown["coordinator"] == FRESH
    # The worker rule covers the current generation only: the old worker is now like any
    # fresh session until it rejoins.
    assert replace(project, WORKER, coordinator=WORKER)["coordinator"] == WORKER


def test_named_coordinator_that_was_a_worker_is_refused(tmp_path: Path) -> None:
    project = Project(tmp_path, claim=False)
    act(project, "revoke", "--recipient", WORKER, address=OWNER)
    # A fresh session names the former worker as coordinator of a new generation, where
    # it has no grant yet; it may act until it enrolls as a worker of that generation.
    project.store.register("codex", "fresh", str(tmp_path))
    replace(project, FRESH, coordinator=WORKER)
    act(project, "grant", "--recipient", OWNER, "--until-revoked", address=WORKER)
    act(project, "grant", "--recipient", WORKER, "--until-revoked", address=WORKER)
    with pytest.raises(BusError, match="Only the project's coordinator may grant"):
        act(project, "grant", "--recipient", FRESH, "--until-revoked", address=WORKER)
