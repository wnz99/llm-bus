"""history."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

from llm_bus.cli.app import main
from llm_bus.errors import BusError
from llm_bus.mailbox.models import SenderContext
from llm_bus.mailbox.store import Store

if TYPE_CHECKING:
    from pathlib import Path


def test_conversation_filter_preserves_scope_and_paging(tmp_path: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    one = SenderContext("codex", "one", "/one")
    two = SenderContext("claude", "two", "/two")
    store.register(*two)
    root = store.send(one, "claude:two", "root", cross_folder=True)
    child = store.send(two, "codex:one", "child", cross_folder=True, reply_to=root["id"])
    leaf = store.send(one, "claude:two", "leaf", cross_folder=True, reply_to=child["id"])
    store.send(one, "claude:two", "unrelated", cross_folder=True)
    rows = store.history(folder="/two", conversation=child["id"], before=leaf["id"], limit=1)
    assert [row["id"] for row in rows] == [child["id"]]
    assert store.history(folder="/other", conversation=root["id"]) == []
    assert [row["id"] for row in store.history(folder=None, conversation=leaf["id"], after=0)] == [
        root["id"],
        child["id"],
        leaf["id"],
    ]
    for message_id in (0, True, 999):
        with pytest.raises(BusError):
            store.history(folder=None, conversation=message_id)
    with store.transaction() as db:
        db.execute("UPDATE messages SET reply_to=? WHERE id=?", (leaf["id"], root["id"]))
    with pytest.raises(BusError, match="cycle"):
        store.history(folder=None, conversation=root["id"])


def test_peer_filter_matches_both_directions(tmp_path: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    one = SenderContext("codex", "one", "/project")
    two = SenderContext("claude", "two", "/project")
    store.register(*two)
    root = store.send(one, "claude:two", "out")
    child = store.send(two, "codex:one", "in", reply_to=root["id"])
    assert [row["id"] for row in store.history(folder=None, peer="codex:one")] == [
        child["id"],
        root["id"],
    ]
    assert store.history(folder=None, conversation=root["id"], peer="codex:on") == []


def test_history_cli_needs_no_agent_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "project"
    folder.mkdir()
    monkeypatch.setenv("LLM_BUS_DB", str(tmp_path / "bus.sqlite3"))
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    store = Store(tmp_path / "bus.sqlite3")
    recipient = store.register("claude", "two", str(folder))
    sent = store.send(SenderContext("codex", "one", str(folder)), recipient, "hello")
    monkeypatch.chdir(tmp_path)

    assert main(["history"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert main(["history", "--folder", str(folder)]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == sent["id"]
    assert main(["history", "--repository", str(folder)]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == sent["id"]
    assert main(["history", "--all", "--after", "0"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["recipient_cwd"] == str(folder)
    assert main(["history", "--all", "--conversation", str(sent["id"]), "--peer", recipient]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == sent["id"]
    assert main(["history", "--all", "--conversation", "0"]) == 1
    assert "positive integer" in capsys.readouterr().err
    assert main(["agents"]) == 0
    agents = cast("list[dict[str, object]]", json.loads(capsys.readouterr().out))
    assert {agent["address"] for agent in agents} == {"codex:one", "claude:two"}
    assert {agent["project"] for agent in agents} == {str(folder)}
    assert main(["history", "--all", "--limit", "0"]) == 1
    assert "History limit" in capsys.readouterr().err
