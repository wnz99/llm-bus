"""Durable labels remain bound to exact native identities."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import TYPE_CHECKING, cast

import pytest

from llm_bus.cli.app import main
from llm_bus.cli.references import resolve_reference
from llm_bus.errors import BusError
from llm_bus.mailbox.models import SenderContext
from llm_bus.mailbox.store import Store
from llm_bus.policy.config import Config, Repository
from llm_bus.routing import routing_scope

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


@pytest.mark.parametrize("output_format", ["json", "agent"])
def test_cli_claim_send_and_history_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    output_format: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_BUS_DB", str(tmp_path / "bus.sqlite3"))
    monkeypatch.setenv("CODEX_THREAD_ID", "one")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    store = Store(tmp_path / "bus.sqlite3")
    assert main(["--format", output_format, "whoami", "--label", "Builder"]) == 0
    assert json.loads(capsys.readouterr().out) == {"address": "codex:one", "label": "Builder"}
    monkeypatch.setenv("CODEX_THREAD_ID", "two")
    assert main(["whoami", "--label", "builder"]) == 1
    capsys.readouterr()
    assert main(["send", "BUILDER", "--body", "old"]) == 0
    old = cast("dict[str, object]", json.loads(capsys.readouterr().out))
    assert old["recipient"] == "codex:one"
    store.end_session("codex", "one")
    store.register("claude", "new", str(tmp_path), label="builder")
    assert main(["send", "builder", "--body", "bad reply", "--reply-to", str(old["id"])]) == 1
    capsys.readouterr()
    monkeypatch.delenv("CODEX_THREAD_ID")
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    assert main(["history", "--all", "--peer", "builder"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert main(["history", "--all", "--peer", "codex:one"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["body"] == "old"


@pytest.mark.parametrize("peer", ["collab:bob:p", "collab:reply:bob:p:q1", "collab:_daemon"])
def test_history_retains_collaboration_peer_filters_without_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    peer: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_BUS_DB", str(tmp_path / "bus.sqlite3"))
    for name in ("CODEX_THREAD_ID", "CODEX_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    store = Store(tmp_path / "bus.sqlite3")
    store.register("codex", "one", str(tmp_path))
    store.deliver_collab(peer, "codex:one", "retained collaboration")
    store.deliver_collab("collab:other:p", "codex:one", "unrelated")
    assert main(["history", "--all", "--peer", peer]) == 0
    rows = cast("list[dict[str, object]]", json.loads(capsys.readouterr().out))
    assert [(row["sender"], row["body"]) for row in rows] == [(peer, "retained collaboration")]
    with pytest.raises(BusError):
        resolve_reference(store, peer, str(tmp_path))


def test_reference_scope_and_unknown(tmp_path: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    store.register("codex", "one", "/one", label="builder")
    store.register("claude", "two", "/two", label="builder")
    assert resolve_reference(store, "builder", "/one") == "codex:one"
    assert resolve_reference(store, "builder", "/two") == "claude:two"
    assert resolve_reference(store, "machine/repo/codex:one", "/two") == "machine/repo/codex:one"
    for value in ("unknown", "a/b", "codex:", "bad label"):
        with pytest.raises(BusError):
            resolve_reference(store, value, "/one")


def test_qualified_local_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    store.register("codex", "one", str(tmp_path), label="Builder")
    config = Config(
        "https://relay.invalid",
        "local",
        tmp_path / "credential",
        (Repository(routing_scope(str(tmp_path)), "repo"),),
    )
    monkeypatch.setattr("llm_bus.cli.references.optional_config", lambda: config)
    assert resolve_reference(store, "local/repo/builder", "/other") == "codex:one"
    for value in (
        "local/unknown/builder",
        "local/repo/unknown",
        "local/re po/builder",
        "remote/repo/builder",
    ):
        with pytest.raises(BusError):
            resolve_reference(store, value, "/other")
