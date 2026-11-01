"""Main-agent routing, including retained registrations created by older clients."""

import io
import json
import sqlite3
from pathlib import Path
from typing import cast

import pytest

from llm_bus.cli.app import main
from llm_bus.errors import BusError
from llm_bus.hosts.sessions import is_subagent
from llm_bus.mailbox.models import SenderContext
from llm_bus.mailbox.store import Store
from llm_bus.remote.bridge import Bridge
from llm_bus.remote.discovery import publish_sessions
from llm_bus.remote.outgoing import permits_outgoing
from llm_bus.remote.store import RemoteStore
from llm_bus.wake.adapters import wake
from llm_bus.wake.dispatch import dispatch_pending
from tests.remote.support import delivery, setup_machine


@pytest.fixture(name="native_state")
def native_state_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "codex"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    path = home / "state_5.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE thread_spawn_edges(parent_thread_id, child_thread_id, status)")
    return path


def mark_child(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO thread_spawn_edges VALUES ('parent','child','open')")


def test_retained_child_cannot_receive_or_send(
    native_state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    child = store.register("codex", "child", str(tmp_path))
    parent = store.register("codex", "parent", str(tmp_path))
    sender = SenderContext("codex", "parent", str(tmp_path))
    stored = store.send(sender, child, "retained pending work")
    mark_child(native_state)

    assert is_subagent(child)
    assert not is_subagent(parent)
    assert [a["address"] for a in store.agents()] == [parent]
    assert len(store.all_agents()) == 2
    with pytest.raises(BusError, match="Sub-agents"):
        store.send(sender, child, "new work")
    with pytest.raises(BusError, match="Sub-agents"):
        store.send(SenderContext("codex", "child", str(tmp_path)), parent, "child report")
    with pytest.raises(BusError, match="Sub-agents"):
        store.deliver_collab("collab:peer", child, "collaborator work")
    assert store.inbox(child) == []
    assert store.pending_count(child) == 0
    assert not store.pending_allowed(stored["id"])
    assert len(store.history(folder=None)) == 1

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("A child must never reach native wake")

    monkeypatch.setattr("llm_bus.wake.dispatch.wake", forbidden)
    dispatch_pending(store, child)
    assert wake(child, stored["id"], parent)["status"] == "unsupported"


def test_remote_child_admission_denied_and_advertisement_withdrawn(
    native_state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_BUS_CONFIG", str(tmp_path / "config.toml"))
    store, config = setup_machine(tmp_path, "b")
    folder = str(tmp_path / "repo")
    store.register("codex", "child", folder)
    store.register("codex", "parent", folder)
    bridge = Bridge(store, config)
    publications: list[dict[str, object]] = []

    def request(_method: str, _path: str, payload: object = None) -> object:
        assert isinstance(payload, dict)
        publications.append(cast("dict[str, object]", payload))
        return {}

    monkeypatch.setattr(bridge.relay, "request", request)
    assert publish_sessions(bridge) == 0
    assert all(not p["ended"] for p in publications)
    publications.clear()
    mark_child(native_state)
    assert publish_sessions(bridge) == 0
    assert len(publications) == 1
    assert publications[0]["native_address"] == "codex:child"
    assert publications[0]["ended"] is True
    remote = RemoteStore(store, config.connection)
    item = delivery()
    item["recipient"] = "b/xyz/codex:child"
    assert remote.admit_message(item, config.repositories[0].scope) is None
    assert store.history(folder=None) == []
    item["recipient"] = "b/xyz/codex:parent"
    assert remote.admit_message(item, config.repositories[0].scope) is not None
    assert len(store.inbox("codex:parent")) == 1
    with pytest.raises(BusError, match="Sub-agents"):
        remote.enqueue(
            SenderContext("codex", "child", folder), "a/xyz/codex:parent", "xyz", "report"
        )


@pytest.mark.parametrize("event", ["SessionStart", "UserPromptSubmit", "SessionEnd"])
def test_claude_child_hook_cannot_change_parent_lifecycle(
    event: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "bus.sqlite3"
    monkeypatch.setenv("LLM_BUS_DB", str(path))
    store = Store(path)
    parent = store.register("claude", "parent", str(tmp_path))
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            json.dumps({"session_id": "parent", "agent_id": "child", "hook_event_name": event})
        ),
    )
    assert main(["hook", "claude"]) == 0
    assert "Report only to your parent" in capsys.readouterr().out
    assert not store.is_ended(parent)
    assert len(store.all_agents()) == 1


def test_queued_child_outgoing_is_held(
    native_state: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_BUS_CONFIG", str(tmp_path / "config.toml"))
    store, config = setup_machine(tmp_path, "b")
    remote = RemoteStore(store, config.connection)
    remote.enqueue(
        SenderContext("codex", "child", str(tmp_path / "repo")),
        "a/xyz/codex:parent",
        "xyz",
        "old queued report",
    )
    envelope, _state = remote.outgoing()[0]
    bridge = Bridge(store, config)
    assert permits_outgoing(bridge, envelope)
    mark_child(native_state)
    assert not permits_outgoing(bridge, envelope)
    assert len(store.history(folder=None)) == 1


def test_codex_child_hook_and_cli_do_not_register(
    native_state: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mark_child(native_state)
    monkeypatch.setenv("LLM_BUS_DB", str(tmp_path / "bus.sqlite3"))
    monkeypatch.setenv("CODEX_THREAD_ID", "child")
    assert main(["whoami"]) == 1
    assert "only main agents" in capsys.readouterr().err
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"session_id": "child", "hook_event_name": "SessionStart"})),
    )
    assert main(["hook", "codex"]) == 0
    result = cast("dict[str, dict[str, str]]", json.loads(capsys.readouterr().out))
    assert "Report only to your parent" in result["hookSpecificOutput"]["additionalContext"]
    assert Store(tmp_path / "bus.sqlite3").all_agents() == []
