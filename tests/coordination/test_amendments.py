"""Objective amendments and the coordinate objective guard."""

# Each scenario states its owner commands explicitly.
# pylint: disable=duplicate-code,clean-code-commented-out-code

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from llm_bus.coordination import amendments, store
from llm_bus.errors import BusError
from tests.coordination.test_approvals import DELEGATION, Project
from tests.coordination.test_enrollment import invoke

if TYPE_CHECKING:
    from pathlib import Path

OBJECTIVE = "Build safely"
DIFFERS = "Objective differs from the stored objective"


@pytest.fixture(autouse=True)
def no_lock_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_timer(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("llm_bus.locks.service.schedule_check", no_timer)


def stored(project: Project) -> list[dict[str, object]]:
    with project.store.transaction() as db:
        record = store.project(db, str(project.path), "demo")
        return amendments.history(db, record)


def test_same_objective_resumes_and_differing_objective_stores_nothing(tmp_path: Path) -> None:
    project = Project(tmp_path)
    resumed = project.owner("coordinate", "demo", "--objective", OBJECTIVE)
    assert resumed["objective"] == OBJECTIVE
    with pytest.raises(BusError, match=DIFFERS):
        project.owner("coordinate", "demo", "--objective", "Something else")
    assert stored(project) == []
    assert project.owner("coordination", "show", "demo")["objective"] == OBJECTIVE
    assert project.owner("coordinate", "demo")["objective"] == OBJECTIVE


def test_amend_appends_history_and_changes_effective_objective(tmp_path: Path) -> None:
    project = Project(tmp_path)
    shown = project.owner("coordination", "show", "demo")
    assert "amendments" not in shown
    first = project.owner("coordination", "amend", "demo", "--objective", "Ship the login")
    project.owner("coordination", "amend", "demo", "--objective", "Ship login and logout")
    rows = cast("list[dict[str, object]]", first["amendments"])
    assert [row["text"] for row in rows] == ["Ship the login"]
    history = stored(project)
    assert [row["text"] for row in history] == ["Ship the login", "Ship login and logout"]
    assert all(row["recorded_by"] == "codex:owner" for row in history)
    with project.store.transaction() as db:
        record = store.project(db, str(project.path), "demo")
        assert amendments.effective_objective(db, record) == "Ship login and logout"
    assert project.owner("coordination", "show", "demo")["objective"] == OBJECTIVE
    project.owner("coordinate", "demo", "--objective", "Ship login and logout")
    with pytest.raises(BusError, match=DIFFERS):
        project.owner("coordinate", "demo", "--objective", OBJECTIVE)


def test_amend_objective_alone_keeps_delegation_and_bindings(tmp_path: Path) -> None:
    project = Project(tmp_path, *DELEGATION)
    before = project.owner("coordination", "show", "demo")
    after = project.owner("coordination", "amend", "demo", "--objective", "New aim")
    assert after["delegation"] == before["delegation"]
    assert after["bindings"] == before["bindings"]
    assert len(stored(project)) == 1


def test_amend_objective_with_delegation_change_applies_both(tmp_path: Path) -> None:
    project = Project(tmp_path, *DELEGATION)
    result = project.owner(
        "coordination", "amend", "demo", "--objective", "New aim", "--no-delegation"
    )
    assert "delegation" not in result
    assert len(stored(project)) == 1
    with pytest.raises(BusError, match="auto-merge needs a merge or merge-admin gate"):
        project.owner("coordination", "amend", "demo", "--objective", "Lost aim", "--auto-merge")
    assert [row["text"] for row in stored(project)] == ["New aim"]


def test_amend_needs_objective_or_delegation_change(tmp_path: Path) -> None:
    project = Project(tmp_path)
    with pytest.raises(BusError, match="amend needs --objective or a delegation change"):
        project.owner("coordination", "amend", "demo")


def test_amend_rejects_non_coordinator_and_bad_text(tmp_path: Path) -> None:
    project = Project(tmp_path)
    with pytest.raises(BusError, match="Only the enrolled coordinator may amend"):
        invoke(
            project.store,
            project.path,
            "coordination",
            "amend",
            "demo",
            "--objective",
            "Hijack",
            address="claude:worker",
        )
    with pytest.raises(BusError, match="nonempty bounded reference"):
        project.owner("coordination", "amend", "demo", "--objective", "  ")
    assert stored(project) == []
