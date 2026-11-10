"""Hook guidance shows the session banner and delegation rules to enrolled sessions only."""

# Scenarios reuse the delegation fixtures and state explicit owner commands.
# pylint: disable=duplicate-code

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from llm_bus.cli.guidance import guidance
from tests.coordination.test_delegation import TWO_GATES, join, start
from tests.coordination.test_enrollment import invoke

if TYPE_CHECKING:
    from pathlib import Path

    from llm_bus.mailbox.store import Store

WORKER = "claude:worker"
OWNER = "codex:owner"
NOTICE = (
    "Project delegation includes gates not in your binding; only a fresh direct owner "
    "join in this session accepts them."
)
FLOOR_TEXT = "Never delegated: force-push; deploys or production changes;"


def text(mailbox: Store, tmp_path: Path, address: str, *, full: bool) -> str:
    return guidance(mailbox, address, str(tmp_path), full=full)


def claim(mailbox: Store, tmp_path: Path) -> None:
    invoke(
        mailbox,
        tmp_path,
        "coordination",
        "claim",
        "demo",
        "--ticket",
        "roam-22m",
        "--branch",
        "roam-22m-login",
        address=WORKER,
    )


@pytest.mark.parametrize("full", [True, False])
def test_banner_for_coordinator_and_worker(tmp_path: Path, *, full: bool) -> None:
    mailbox = start(tmp_path)
    mailbox.register("claude", "worker", str(tmp_path), label="builder")
    join(mailbox, tmp_path)
    before = text(mailbox, tmp_path, WORKER, full=full)
    assert (
        f"Session banner: project demo; role worker; label builder; ticket none; "
        f"branch none; folder {tmp_path}."
    ) in before
    claim(mailbox, tmp_path)
    after = text(mailbox, tmp_path, WORKER, full=full)
    assert "ticket roam-22m; branch roam-22m-login;" in after
    assert "Prefix every request for direct owner confirmation with the session banner." in after
    assert "name the owning session from coordination show." in after
    owner = text(mailbox, tmp_path, OWNER, full=full)
    assert (
        f"Session banner: project demo; role coordinator; label none; ticket none; branch none; folder {tmp_path}."
        in owner
    )
    assert "Prefix every request" not in owner
    assert "Delegated gates" not in owner


def test_no_delegation_text_without_project_delegation(tmp_path: Path) -> None:
    mailbox = start(tmp_path)
    join(mailbox, tmp_path)
    for address in (OWNER, WORKER):
        shown = text(mailbox, tmp_path, address, full=True)
        assert "Delegated gates" not in shown
        assert "Never delegated" not in shown
        assert NOTICE not in shown


def test_worker_sees_effective_gates_and_floor(tmp_path: Path) -> None:
    mailbox = start(tmp_path, *TWO_GATES)
    join(mailbox, tmp_path, "--narrow", "commit:develop")
    worker = text(mailbox, tmp_path, WORKER, full=False)
    assert "Delegated gates bound by your owner's join: commit→[develop]." in worker
    assert "push→" not in worker
    assert FLOOR_TEXT in worker
    assert "merge with failing or pending CI" in worker
    assert "Verify CI and review evidence live before acting; refuse on mismatch." in worker
    # The deliberately narrowed worker also gets the neutral notice.
    assert NOTICE in worker
    owner = text(mailbox, tmp_path, OWNER, full=False)
    assert "Delegated gates: commit→[develop]; push→[develop];" in owner
    assert "`llm-bus coordination approve`; workers whose binding is narrower: 1." in owner
    assert "Never delegated" not in owner


def test_full_binding_has_no_notice_and_widening_adds_it(tmp_path: Path) -> None:
    mailbox = start(tmp_path, *TWO_GATES)
    join(mailbox, tmp_path)
    assert NOTICE not in text(mailbox, tmp_path, WORKER, full=True)
    assert "workers whose binding is narrower: 0." in text(mailbox, tmp_path, OWNER, full=True)
    invoke(
        mailbox,
        tmp_path,
        "coordination",
        "amend",
        "demo",
        "--delegate",
        "commit:develop",
        "--delegate",
        "push:develop",
        "--delegate",
        "merge:main",
    )
    worker = text(mailbox, tmp_path, WORKER, full=True)
    assert NOTICE in worker
    assert "commit→[develop]; push→[develop]." in worker
    assert "merge→" not in worker
    assert "workers whose binding is narrower: 1." in text(mailbox, tmp_path, OWNER, full=True)


def test_unenrolled_session_output_has_no_banner(tmp_path: Path) -> None:
    mailbox = start(tmp_path, *TWO_GATES)
    mailbox.register("claude", "stranger", str(tmp_path))
    shown = text(mailbox, tmp_path, "claude:stranger", full=True)
    assert "Session banner" not in shown
    assert "Delegated gates" not in shown
    assert "Coordination recovery" not in shown
