"""Durable labels remain bound to exact native identities."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import TYPE_CHECKING

import pytest

from llm_bus.errors import BusError
from llm_bus.mailbox.models import SenderContext
from llm_bus.mailbox.store import Store

from .support import linked_worktree

if TYPE_CHECKING:
    from pathlib import Path


def test_concurrent_claim_across_hosts_and_worktrees(tmp_path: Path) -> None:
    repo, worktree, _git = linked_worktree(tmp_path)
    store = Store(tmp_path / "bus.sqlite3")
    barrier = Barrier(2)

    def claim(kind: str, project: str, label: str) -> str:
        peer = Store(store.path)
        barrier.wait()
        try:
            return peer.register(kind, kind, project, label=label)
        except BusError:
            return "occupied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(claim, "claude", str(repo), "Builder")
        b = pool.submit(claim, "codex", str(worktree), "BUILDER")
        results = [a.result(), b.result()]
    assert results.count("occupied") == 1
    assert store.resolve_label("builder", str(repo)) in results
    assert len(store.all_agents()) == 1


def test_rename_is_atomic_and_preserves_display(tmp_path: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    one = store.register("codex", "one", "/project", label="Builder")
    store.register("codex", "one", "/project", label="BUILDER")
    assert store.agents()[0].get("label") == "Builder"
    store.register("claude", "two", "/project", label="reviewer")
    with pytest.raises(BusError):
        store.register("codex", "one", "/other", label="bad label")
    with pytest.raises(BusError, match="claude:two"):
        store.register("codex", "one", "/project", label="reviewer")
    assert store.resolve_label("builder", "/project") == one
    store.register("codex", "one", "/project", label="new")
    with pytest.raises(BusError):
        store.resolve_label("builder", "/project")


@pytest.mark.parametrize("label", ["", "a/b", "a:b", "a b", "é", "a\n", "-a", "a" * 65])
def test_invalid_labels_leave_no_registration(tmp_path: Path, label: str) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    with pytest.raises(BusError):
        store.register("codex", "one", "/project", label=label)
    assert store.all_agents() == []


def test_close_expiry_resume_and_scope_movement(tmp_path: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    one = store.register("codex", "one", "/project", label="builder")
    store.register("claude", "two", "/other", label="builder")
    store.end_session("codex", "one")
    new = store.register("codex", "new", "/project", label="builder")
    store.end_session("codex", "one")
    assert store.resolve_label("builder", "/project") == new
    store.register("codex", "one", "/project")
    assert next(a for a in store.agents() if a["address"] == one).get("label") is None
    store.send(SenderContext("codex", "new", "/other"), "claude:two", "retained")
    with pytest.raises(BusError):
        store.resolve_label("builder", "/project")
    with store.transaction() as db:
        db.execute("UPDATE agents SET last_seen='2000-01-01' WHERE address='claude:two'")
    with pytest.raises(BusError):
        store.resolve_label("builder", "/other")
    assert store.history(folder=None)[0]["body"] == "retained"
