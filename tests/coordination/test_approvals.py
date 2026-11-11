"""Gate approval directions: send-time and exposure-time verification, claims, remote refusal."""

# Each scenario states its owner commands and stored rows explicitly.
# pylint: disable=duplicate-code,protected-access,clean-code-commented-out-code

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest

from llm_bus.cli import commands
from llm_bus.cli.parser import build_parser
from llm_bus.coordination import approvals
from llm_bus.coordination.authority import (
    _gate_decision,  # pyright: ignore[reportPrivateUsage]
    authorize,
)
from llm_bus.coordination.delivery import outgoing
from llm_bus.coordination.models import Approval, Direction
from llm_bus.errors import BusError
from llm_bus.mailbox.models import SenderContext
from llm_bus.mailbox.store import Store
from llm_bus.routing import routing_scope
from tests.coordination.test_enrollment import invoke

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

SHA = "a" * 40
DELEGATION = (
    "--delegate",
    "commit:develop",
    "--delegate",
    "push:develop",
    "--delegate",
    "merge:develop",
    "--delegate",
    "merge-admin:develop",
)
APPROVE: tuple[str, ...] = (
    "coordination",
    "approve",
    "demo",
    "--recipient",
    "claude:worker",
    "--gate",
    "merge-admin",
    "--ticket",
    "roam-22m",
    "--branch",
    "roam-22m-login",
    "--base",
    "develop",
    "--pr",
    "42",
    "--head-sha",
    SHA,
)


@pytest.fixture(autouse=True)
def no_wake(monkeypatch: pytest.MonkeyPatch) -> None:
    def unsupported(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {"status": "unsupported"}

    monkeypatch.setattr(commands, "dispatch_pending", unsupported)


class Project:
    """One coordinator, one claimed worker, with a delegation."""

    path: Path
    store: Store

    def __init__(self, tmp_path: Path, *delegate: str, claim: bool = True) -> None:
        self.path = tmp_path
        self.store = Store(tmp_path / "bus.db")
        self.store.register("codex", "owner", str(tmp_path))
        self.store.register("claude", "worker", str(tmp_path))
        (tmp_path / ".beads").mkdir(exist_ok=True)
        invoke(
            self.store,
            tmp_path,
            "coordinate",
            "demo",
            "--objective",
            "Build safely",
            "--beads-workspace",
            str(tmp_path),
            "--memory-ticket",
            "bus-1",
            *delegate,
        )
        invoke(self.store, tmp_path, "join", "demo", address="claude:worker")
        if claim:
            self.claim("claude:worker", "roam-22m", "roam-22m-login")

    def claim(self, address: str, ticket: str, branch: str) -> None:
        invoke(
            self.store,
            self.path,
            "coordination",
            "claim",
            "demo",
            "--ticket",
            ticket,
            "--branch",
            branch,
            address=address,
        )

    def owner(self, *argv: str) -> dict[str, object]:
        return invoke(self.store, self.path, *argv)

    def approve(self, *overrides: str) -> object:
        argv: list[str] = list(APPROVE)
        for index in range(0, len(overrides), 2):
            argv[argv.index(overrides[index]) + 1] = overrides[index + 1]
        args = vars(build_parser().parse_args(argv))
        return approvals.approve(self.store, args, SenderContext("codex", "owner", str(self.path)))

    def coordination(self, address: str = "claude:worker") -> dict[str, object] | None:
        for item in self.store.inbox(address):
            if "coordination" in item:
                return item["coordination"]
        return None

    def rows(self, table: str) -> int:
        with self.store.transaction() as db:
            return cast("int", db.execute(f"SELECT count(*) FROM {table}").fetchone()[0])  # noqa: S608

    def direction(self, operation: str) -> Direction:
        with self.store.transaction() as db:
            project_id, generation = cast(
                "tuple[str, str]",
                db.execute("SELECT project_id,generation FROM coordination_projects").fetchone(),
            )
            (grant_id,) = cast(
                "tuple[str]", db.execute("SELECT grant_id FROM coordination_grants").fetchone()
            )
        return Direction(1, project_id, generation, grant_id, operation)

    def authorize(
        self, approval: Approval | None, operation: str = "approve-merge-admin", sender: str = ""
    ) -> str:
        with self.store.transaction() as db:
            return authorize(
                db,
                sender=sender or "codex:owner",
                recipient="claude:worker",
                scope=cast(
                    "str", db.execute("SELECT scope FROM coordination_projects").fetchone()[0]
                ),
                direction=self.direction(operation),
                now=datetime.now(UTC),
                approval=approval,
            ).reason


def expiry(**delta: float) -> str:
    return (datetime.now(UTC) + timedelta(**delta)).isoformat(timespec="seconds")


def merge_approval(**changes: object) -> Approval:
    fields: dict[str, object] = {
        "ticket": "roam-22m",
        "branch": "roam-22m-login",
        "base": "develop",
        "expires_at": expiry(hours=1),
        "pr": 42,
        "head_sha": SHA,
    }
    return Approval(**{**fields, **changes})  # pyright: ignore[reportArgumentType]


def test_verified_approval_is_exposed_with_its_evidence(tmp_path: Path) -> None:
    project = Project(tmp_path, *DELEGATION)
    project.approve()
    shown = project.coordination()
    assert shown is not None
    assert shown["verified"] is True
    assert shown["operation"] == "approve-merge-admin"
    approval = cast("dict[str, object]", shown["approval"])
    assert {key: approval[key] for key in ("ticket", "branch", "base", "pr", "head_sha")} == {
        "ticket": "roam-22m",
        "branch": "roam-22m-login",
        "base": "develop",
        "pr": 42,
        "head_sha": SHA,
    }
    assert project.rows("coordination_approvals") == 1


@pytest.mark.parametrize(("gate", "extra"), [("commit", []), ("push", ["--head-sha", SHA])])
def test_commit_and_push_approvals_verify(tmp_path: Path, gate: str, extra: list[str]) -> None:
    project = Project(tmp_path, *DELEGATION)
    argv = [
        "coordination",
        "approve",
        "demo",
        "--recipient",
        "claude:worker",
        "--gate",
        gate,
        "--ticket",
        "roam-22m",
        "--branch",
        "roam-22m-login",
        "--base",
        "develop",
        *extra,
    ]
    approvals.approve(
        project.store,
        vars(build_parser().parse_args(argv)),
        SenderContext("codex", "owner", str(tmp_path)),
    )
    shown = project.coordination()
    assert shown is not None
    assert shown["verified"] is True
    assert shown["operation"] == f"approve-{gate}"
    assert "pr" not in cast("dict[str, object]", shown["approval"])


def test_ordinary_direction_has_no_approval_key(tmp_path: Path) -> None:
    project = Project(tmp_path, *DELEGATION)
    with project.store.transaction() as db:
        direction = outgoing(
            db,
            sender="codex:owner",
            scope=routing_scope(str(tmp_path)),
            label="demo",
            recipient="claude:worker",
            operation="assign",
        )
    project.store.send(
        SenderContext("codex", "owner", str(tmp_path)),
        "claude:worker",
        "do it",
        direction=direction,
    )
    shown = project.coordination()
    assert shown is not None
    assert shown["verified"] is True
    assert "approval" not in shown


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (("--base", "main"), "target_denied"),
        (("--branch", "develop"), "target_denied"),
        (("--ticket", "other-1"), "wrong_ticket"),
    ],
)
def test_send_denials_store_nothing(
    tmp_path: Path, overrides: tuple[str, ...], reason: str
) -> None:
    project = Project(tmp_path, *DELEGATION)
    before = project.rows("messages")
    with pytest.raises(BusError, match=f"Direction denied: {reason}"):
        project.approve(*overrides)
    assert project.rows("messages") == before
    assert project.rows("coordination_approvals") == 0
    assert project.rows("coordination_messages") == 0


def test_gate_without_delegation_is_rejected(tmp_path: Path) -> None:
    project = Project(tmp_path)
    with pytest.raises(BusError, match="Direction denied: gate_not_delegated"):
        project.approve()


def test_gate_outside_delegated_gates_is_rejected(tmp_path: Path) -> None:
    project = Project(tmp_path, "--delegate", "commit:develop")
    with pytest.raises(BusError, match="Direction denied: gate_not_delegated"):
        project.approve()


def test_worker_without_binding_is_denied(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.owner("coordination", "amend", "demo", *DELEGATION)
    with pytest.raises(BusError, match="Direction denied: gate_not_delegated"):
        project.approve()
    assert project.rows("coordination_approvals") == 0
    assert project.rows("coordination_messages") == 0


def test_narrowed_join_denies_gate_outside_binding(tmp_path: Path) -> None:
    project = Project(tmp_path, *DELEGATION)
    invoke(
        project.store,
        tmp_path,
        "join",
        "demo",
        "--narrow",
        "commit:develop",
        address="claude:worker",
    )
    with pytest.raises(BusError, match="Direction denied: gate_not_delegated"):
        project.approve()
    assert project.rows("coordination_approvals") == 0
    assert project.rows("coordination_messages") == 0


def test_branch_may_not_be_another_delegated_target(tmp_path: Path) -> None:
    project = Project(tmp_path, *DELEGATION, "--delegate", "commit:release")
    project.claim("claude:worker", "roam-22m", "release")
    with pytest.raises(BusError, match="Direction denied: target_denied"):
        project.approve("--branch", "release")


def test_unclaimed_worker_is_denied(tmp_path: Path) -> None:
    project = Project(tmp_path, *DELEGATION, claim=False)
    with pytest.raises(BusError, match="Direction denied: wrong_ticket"):
        project.approve()


def test_authorize_reasons_for_direct_inputs(tmp_path: Path) -> None:
    project = Project(tmp_path, *DELEGATION)
    assert project.authorize(merge_approval()) == "verified"
    assert project.authorize(None) == "invalid_direction"
    assert project.authorize(merge_approval(pr=None)) == "invalid_direction"
    assert project.authorize(merge_approval(head_sha="zz")) == "invalid_direction"
    assert project.authorize(merge_approval(expires_at=expiry(minutes=-1))) == "expired"
    assert project.authorize(merge_approval(branch="roam-22m-other")) == "wrong_ticket"
    assert project.authorize(merge_approval(), "approve-merge") == "verified"
    assert project.authorize(merge_approval(), "approve-commit") == "invalid_direction"


def test_remote_coordinator_without_peer_policy_is_not_delegated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_BUS_CONFIG", str(tmp_path / "absent.toml"))
    project = Project(tmp_path, *DELEGATION)
    with project.store.transaction() as db:
        record: dict[str, object] = {"scope": "s", "project_id": "p", "generation": "g"}
        decision = _gate_decision(
            db,
            record,
            coordinator="mac/repo/codex:owner",
            recipient="claude:worker",
            direction=project.direction("approve-merge-admin"),
            approval=merge_approval(),
            now=datetime.now(UTC),
        )
    # No local peer policy exists here, so the default-deny cap leaves no gate.
    assert decision.reason == "gate_not_delegated"


def test_remote_admission_without_approval_is_not_verified(tmp_path: Path) -> None:
    project = Project(tmp_path, *DELEGATION)
    # Relay admission calls authorize with no approval; gate operations then fail closed.
    for operation in ("approve-commit", "approve-push", "approve-merge", "approve-merge-admin"):
        assert project.authorize(None, operation) == "invalid_direction"


def test_send_choices_do_not_include_gate_operations() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["send", "claude:worker", "x", "--project", "demo", "--direction", "approve-merge"]
        )


def test_gate_operation_requires_approval_at_outgoing(tmp_path: Path) -> None:
    project = Project(tmp_path, *DELEGATION)
    with project.store.transaction() as db, pytest.raises(BusError, match="require an approval"):
        outgoing(
            db,
            sender="codex:owner",
            scope=routing_scope(str(tmp_path)),
            label="demo",
            recipient="claude:worker",
            operation="approve-merge",
        )


@pytest.mark.parametrize("expires_in", ["0h", "25h", "2d", "x", "1441m"])
def test_expiry_bounds(expires_in: str) -> None:
    with pytest.raises(BusError, match=r"expires-in|24h"):
        approvals.parse_duration(expires_in)


def test_expiry_accepts_default_and_limit() -> None:
    assert approvals.parse_duration("2h") == timedelta(hours=2)
    assert approvals.parse_duration("24h") == timedelta(hours=24)


def test_expired_stored_approval_is_hidden(tmp_path: Path) -> None:
    project = Project(tmp_path, *DELEGATION)
    project.approve()
    assert project.coordination() is not None
    with project.store.transaction() as db:
        row = cast(
            "tuple[str]", db.execute("SELECT approval_json FROM coordination_approvals").fetchone()
        )
        stored = cast("dict[str, object]", json.loads(row[0]))
        stored["expires_at"] = expiry(seconds=-5)
        db.execute("UPDATE coordination_approvals SET approval_json=?", (json.dumps(stored),))
    assert project.coordination() is None


def test_amend_narrowing_hides_stored_approval(tmp_path: Path) -> None:
    project = Project(tmp_path, *DELEGATION)
    project.approve()
    assert project.coordination() is not None
    project.owner(
        "coordination",
        "amend",
        "demo",
        "--delegate",
        "commit:develop",
        "--delegate",
        "push:develop",
    )
    assert project.coordination() is None
    assert project.rows("coordination_approvals") == 1


def test_missing_approval_row_hides_gate_direction(tmp_path: Path) -> None:
    project = Project(tmp_path, *DELEGATION)
    project.approve()
    with project.store.transaction() as db:
        db.execute("DELETE FROM coordination_approvals")
    assert project.coordination() is None


@pytest.mark.parametrize("ending", ["revoke", "leave", "replace", "grant_expiry"])
def test_ended_authority_hides_stored_approval(tmp_path: Path, ending: str) -> None:
    project = Project(tmp_path, *DELEGATION)
    project.approve()
    assert project.coordination() is not None
    endings: dict[str, Callable[[], object]] = {
        "revoke": lambda: project.owner(
            "coordination", "revoke", "demo", "--recipient", "claude:worker"
        ),
        "leave": lambda: invoke(
            project.store, tmp_path, "coordination", "leave", "demo", address="claude:worker"
        ),
        "replace": lambda: project.owner(
            "coordination",
            "replace",
            "demo",
            "--generation",
            str(uuid4()),
            "--coordinator",
            "codex:owner",
        ),
    }
    if ending == "grant_expiry":
        with project.store.transaction() as db:
            db.execute("UPDATE coordination_grants SET expires_at=?", (expiry(seconds=-5),))
    else:
        endings[ending]()
    assert project.coordination() is None
    assert project.rows("coordination_approvals") == 1


def test_second_worker_cannot_claim_same_ticket_or_branch(tmp_path: Path) -> None:
    project = Project(tmp_path, *DELEGATION)
    project.store.register("claude", "second", str(tmp_path))
    invoke(project.store, tmp_path, "join", "demo", address="claude:second")
    with pytest.raises(BusError, match=r"already claimed by claude:worker"):
        project.claim("claude:second", "roam-22m", "other-branch")
    with pytest.raises(BusError, match=r"already claimed by claude:worker"):
        project.claim("claude:second", "other-ticket", "roam-22m-login")
    project.claim("claude:second", "other-ticket", "other-branch")
    project.claim("claude:worker", "roam-22m", "roam-22m-login")
    invoke(project.store, tmp_path, "coordination", "leave", "demo", address="claude:worker")
    project.claim("claude:second", "roam-22m", "roam-22m-login")


def test_claim_requires_enrolled_worker(tmp_path: Path) -> None:
    project = Project(tmp_path, *DELEGATION)
    project.store.register("claude", "stranger", str(tmp_path))
    for address in ("claude:stranger", "codex:owner"):
        with pytest.raises(BusError, match="enrolled worker"):
            project.claim(address, "x-1", "x-branch")
    with project.store.transaction() as db:
        rows = db.execute("SELECT address FROM coordination_claims").fetchall()
    assert rows == [("claude:worker",)]


def test_leave_drops_the_claim(tmp_path: Path) -> None:
    project = Project(tmp_path, *DELEGATION)
    invoke(project.store, tmp_path, "coordination", "leave", "demo", address="claude:worker")
    assert project.rows("coordination_claims") == 0


def test_approval_wire_is_strict() -> None:
    good = merge_approval().wire()
    assert Approval.parse(good, "approve-merge") == merge_approval()
    commit = {k: good[k] for k in ("ticket", "branch", "base", "expires_at")}
    assert Approval.parse(commit, "approve-commit").wire() == commit
    bad: list[tuple[object, str]] = [
        ({**good, "extra": 1}, "approve-merge"),
        ({k: v for k, v in good.items() if k != "base"}, "approve-merge"),
        ({**good, "pr": True}, "approve-merge"),
        ({**good, "pr": 0}, "approve-merge"),
        ({**good, "head_sha": "A" * 40}, "approve-merge"),
        ({**good, "head_sha": "a" * 39}, "approve-merge"),
        ({**good, "ticket": "a\nb"}, "approve-merge"),
        ({**good, "ticket": "t" * 129}, "approve-merge"),
        ({**good, "branch": "-x"}, "approve-merge"),
        ({**good, "expires_at": "2026-01-01 00:00:00"}, "approve-merge"),
        (good, "approve-commit"),
        (good, "approve-push"),
        (commit, "approve-merge"),
        (commit, "approve-push"),
        ([], "approve-merge"),
        (good, "assign"),
    ]
    for value, operation in bad:
        with pytest.raises(BusError):
            Approval.parse(value, operation)
    assert Approval.parse({**good, "head_sha": "b" * 64, "pr": 1}, "approve-merge-admin")
