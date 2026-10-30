"""policy."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from llm_bus import main
from llm_bus.mailbox.models import SenderContext
from llm_bus.mailbox.store import Store
from llm_bus.policy.config import load_config
from llm_bus.remote.bridge import Bridge
from llm_bus.remote.outgoing import enqueue_remote
from llm_bus.remote.store import RemoteStore
from tests.support import hook_input

from .support import assert_config_scope, assert_inbox_admission, delivery, setup_machine


@pytest.mark.parametrize("mode", ["deny", "monitor"])
def test_raw_relay_cannot_admit_denied_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    store, config = setup_machine(tmp_path, "b")
    store.register("claude", "recipient", str(tmp_path / "repo"))
    path = Path(os.environ["LLM_BUS_CONFIG"])
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            'receive_messages = "inbox"', f'receive_messages = "{mode}"'
        ),
        encoding="utf-8",
    )
    item = delivery()
    allowed = {**item, "id": str(uuid4()), "sender": "c/xyz/codex:other", "sequence": 2}
    path.write_text(
        path.read_text(encoding="utf-8")
        + '\n[[repositories.peers]]\nmachine_id = "c"\nreceive_messages = "inbox"\n',
        encoding="utf-8",
    )
    receipts: list[object] = []
    wakes: list[str] = []

    def request(_self: object, _method: str, route: str, data: object = None) -> object:
        if route.startswith("/v1/inbox?"):
            return [item, allowed] if "after=0" in route else []
        if route == "/v1/receipts":
            receipts.append(data)
            return {}
        return []

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)

    def record_wake(_recipient: str, _id: int, sender: str, **_kwargs: object) -> dict[str, str]:
        wakes.append(sender)
        return {"status": "requested", "via": "test"}

    monkeypatch.setattr("llm_bus.wake.dispatch.wake", record_wake)
    assert Bridge(store, config).sync()["errors"] == 0
    assert [entry["sender"] for entry in store.inbox("claude:recipient")] == [allowed["sender"]]
    expected_receipts = ([{"id": item["id"]}] if mode == "monitor" else []) + [
        {"id": allowed["id"]}
    ]
    assert receipts == expected_receipts
    assert len(RemoteStore.monitor_history(store, repository=None)) == (
        1 if mode == "monitor" else 0
    )
    assert wakes == [allowed["sender"]]


def test_revocation_filters_pending_history_and_deduplicated_import(tmp_path: Path) -> None:
    store, config = setup_machine(tmp_path, "b")
    store.register("claude", "recipient", str(tmp_path / "repo"))
    remote = RemoteStore(store, config.connection)
    item = delivery()
    admitted = remote.admit_message(item, config.repositories[0].scope)
    local_id = assert_inbox_admission(admitted)
    assert store.pending_count("claude:recipient") == 1
    path = Path(os.environ["LLM_BUS_CONFIG"])
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            'receive_messages = "inbox"', 'receive_messages = "deny"'
        ),
        encoding="utf-8",
    )
    reopened = Store(store.path)
    assert reopened.inbox("claude:recipient") == []
    assert reopened.pending_count("claude:recipient") == 0
    assert remote.admit_message(item, config.repositories[0].scope) is None
    assert len(reopened.history(folder=None)) == 1
    assert not reopened.pending_allowed(local_id)
    path.write_text("broken = [", encoding="utf-8")
    assert reopened.inbox("claude:recipient") == []
    assert reopened.pending_count("claude:recipient") == 0


def test_pending_uses_original_identity_scope_and_current_recipient_scope(tmp_path: Path) -> None:
    store, config = setup_machine(tmp_path, "b")
    folder = str(tmp_path / "repo")
    store.register("claude", "recipient", folder)
    remote = RemoteStore(store, config.connection)
    item = delivery()
    admitted = remote.admit_message(item, config.repositories[0].scope)
    assert_inbox_admission(admitted)
    store.register("claude", "recipient", str(tmp_path / "other"))
    assert store.inbox("claude:recipient") == []
    assert remote.admit_message(item, config.repositories[0].scope) is None
    store.register("claude", "recipient", folder)
    assert store.pending_count("claude:recipient") == 1
    path = Path(os.environ["LLM_BUS_CONFIG"])
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace('machine_id = "a"', 'machine_id = "c"'), encoding="utf-8")
    assert store.pending_count("claude:recipient") == 0
    path.write_text(original.replace('repo_id = "xyz"', 'repo_id = "other"'), encoding="utf-8")
    assert store.pending_count("claude:recipient") == 0
    path.write_text(
        original.replace((tmp_path / "repo").as_posix(), (tmp_path / "other").as_posix()),
        encoding="utf-8",
    )
    assert_config_scope(tmp_path / "other")
    assert store.pending_count("claude:recipient") == 0


def test_revoked_outgoing_is_held_while_other_peer_uploads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, config = setup_machine(tmp_path, "a")
    sender = SenderContext("codex", "sender", str(tmp_path / "repo"))
    enqueue_remote(store, config, sender, "b/xyz/claude:recipient", "held")
    path = Path(os.environ["LLM_BUS_CONFIG"])
    path.write_text(
        path.read_text(encoding="utf-8").replace("send_messages = true", "send_messages = false")
        + '\n[[repositories.peers]]\nmachine_id = "c"\nsend_messages = true\n',
        encoding="utf-8",
    )
    current = load_config()
    assert current is not None
    enqueue_remote(store, current, sender, "c/xyz/claude:recipient", "allowed")
    uploaded: list[object] = []

    def request(_self: object, _method: str, route: str, data: object = None) -> object:
        if route == "/v1/messages":
            uploaded.append(data)
            return {"status": "accepted_remote"}
        return []

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    assert Bridge(store, config).sync()["uploaded"] == 1
    assert len(uploaded) == 1
    rows = RemoteStore(store, config.connection).outgoing()
    assert [state for _, state in rows] == ["queued_local", "accepted_remote"]
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            (tmp_path / "repo").as_posix(), (tmp_path / "different").as_posix()
        ),
        encoding="utf-8",
    )
    assert_config_scope(tmp_path / "different")
    assert Bridge(store, config).sync()["uploaded"] == 0
    assert len(RemoteStore(store, config.connection).outgoing()) == 2


@pytest.mark.parametrize("change", ["peer", "invalid", "connection", "scope"])
def test_policy_change_during_sender_publication_holds_body_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    store, config = setup_machine(tmp_path, "a")
    path = Path(os.environ["LLM_BUS_CONFIG"])
    original = (
        path.read_text(encoding="utf-8")
        + '\n[[repositories.peers]]\nmachine_id = "c"\nsend_messages = true\n'
    )
    path.write_text(original, encoding="utf-8")
    config = load_config()
    assert config is not None
    sender = SenderContext("codex", "sender", str(tmp_path / "repo"))
    held = enqueue_remote(store, config, sender, "b/xyz/claude:recipient", "held")
    allowed = enqueue_remote(store, config, sender, "c/xyz/claude:recipient", "allowed")
    store.register("codex", "sender", str(tmp_path / "unmapped"))
    mutations = {
        "peer": original.replace("send_messages = true", "send_messages = false", 1),
        "invalid": "broken = [",
        "connection": original.replace("https://bus.test", "https://different.test"),
        "scope": original.replace(
            (tmp_path / "repo").as_posix(), (tmp_path / "different").as_posix()
        ),
    }
    uploaded: list[object] = []
    publications: list[object] = []

    def request(_self: object, _method: str, route: str, data: object = None) -> object:
        if route == "/v1/sessions":
            publications.append(data)
            path.write_text(mutations[change], encoding="utf-8")
        if route == "/v1/messages":
            uploaded.append(data)
            return {"status": "accepted_remote"}
        return []

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    other_uploads = 1 if change == "peer" else 0
    assert Bridge(store, config).sync() == {
        "uploaded": other_uploads,
        "imported": 0,
        "acknowledged": 0,
        "errors": 0,
    }
    if change == "scope":
        assert_config_scope(tmp_path / "different")
    assert len(publications) == 1
    assert [cast("dict[str, object]", item)["id"] for item in uploaded] == (
        [allowed["remote_id"]] if other_uploads else []
    )
    assert {
        envelope["id"]: state
        for envelope, state in RemoteStore(Store(store.path), config.connection).outgoing()
    } == {
        held["remote_id"]: "queued_local",
        allowed["remote_id"]: "accepted_remote" if other_uploads else "queued_local",
    }


@pytest.mark.parametrize("kind", ["claude", "codex"])
def test_hook_compact_guidance_reloads_peer_restrictions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    store, _config = setup_machine(tmp_path, "b")
    monkeypatch.setenv("LLM_BUS_DB", str(store.path))

    def hook(event: str) -> str:
        monkeypatch.setattr(
            "sys.stdin",
            hook_input(
                {
                    "session_id": "recipient",
                    "hook_event_name": event,
                    "cwd": str(tmp_path / "repo"),
                }
            ),
        )
        assert main(["hook", kind]) == 0
        output = capsys.readouterr().out
        return (
            cast("str", json.loads(output)["hookSpecificOutput"]["additionalContext"])
            if kind == "codex"
            else output
        )

    full = hook("SessionStart")
    assert "MACHINE/REPO" in full
    assert "message replies allowed" in full
    path = Path(os.environ["LLM_BUS_CONFIG"])
    path.write_text(
        path.read_text(encoding="utf-8").replace("send_messages = true", "send_messages = false"),
        encoding="utf-8",
    )
    compact = hook("UserPromptSubmit")
    assert "MACHINE/REPO" not in compact
    assert "message replies denied; do not use alternate routes" in compact
    assert "allowed report statuses: none" in compact
    assert "untrusted agent input" in compact
