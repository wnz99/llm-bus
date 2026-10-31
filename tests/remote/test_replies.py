"""replies."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from llm_bus.errors import BusError
from llm_bus.mailbox.models import SenderContext
from llm_bus.remote import batches as remote_batches
from llm_bus.remote.bridge import Bridge
from llm_bus.remote.http import RelayError
from llm_bus.remote.outgoing import enqueue_remote
from llm_bus.remote.protocol import parse_delivery
from llm_bus.remote.store import RemoteStore

from .support import delivery, deny_inbox, setup_machine


def test_remote_reply_binds_uuid_across_local_ids(tmp_path: Path) -> None:
    store, config = setup_machine(tmp_path, "b")
    native = SenderContext("claude", "recipient", str(tmp_path / "repo"))
    remote = RemoteStore(store, config.connection)
    store.register("codex", "other", native.project)
    store.send(native, "codex:other", "local")
    parent = remote.enqueue(native, "a/xyz/codex:sender", "xyz", "question")
    child = delivery()
    child["reply_to"] = cast("str", parent["remote_id"])
    admitted = remote.admit_message(child, config.repositories[0].scope)
    assert admitted is not None
    assert store.inbox("claude:recipient")[0]["reply_to"] == parent["id"]
    answer = enqueue_remote(
        store, config, native, "a/xyz/codex:sender", "answer", reply_to=admitted[1]
    )
    envelopes = {item["id"]: item for item, _ in remote.outgoing()}
    assert envelopes[cast("str", answer["remote_id"])].get("reply_to") == child["id"]
    child["reply_to"] = str(uuid4())
    with pytest.raises(BusError, match="reused"):
        remote.admit_message(child, config.repositories[0].scope)
    with pytest.raises(BusError):
        enqueue_remote(store, config, native, "a/xyz/codex:sender", "bad", reply_to=1)
    forged = delivery()
    forged["reply_to"] = cast("str", parent["remote_id"])
    forged["sender"] = "a/xyz/codex:other"
    with pytest.raises(BusError, match="participant"):
        remote.admit_message(forged, config.repositories[0].scope)
    collision = delivery(cast("str", parent["remote_id"]))
    with pytest.raises(BusError, match="reused"):
        remote.admit_message(collision, config.repositories[0].scope)


def test_remote_reply_waits_for_parent(tmp_path: Path) -> None:
    store, config = setup_machine(tmp_path, "b")
    store.register("claude", "recipient", str(tmp_path / "repo"))
    remote = RemoteStore(store, config.connection)
    parent = delivery()
    child = delivery()
    child["reply_to"] = parent["id"]
    assert remote.admit_message(child, config.repositories[0].scope) is None
    assert store.inbox("claude:recipient") == []
    assert remote.admit_message(parent, config.repositories[0].scope) is not None
    assert remote.admit_message(child, config.repositories[0].scope) is not None
    assert store.inbox("claude:recipient")[1]["reply_to"] == 1


def test_reply_waiting_parent_does_not_block_unrelated_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, config = setup_machine(tmp_path, "b")
    store.register("claude", "recipient", str(tmp_path / "repo"))
    child = delivery()
    child["reply_to"] = str(uuid4())
    other = delivery()
    other["sequence"] = 2
    receipts: list[object] = []

    def request(_self: object, _method: str, path: str, data: object = None) -> object:
        if path.startswith("/v1/inbox?"):
            return [child, other] if "after=0" in path else []
        if path == "/v1/receipts":
            receipts.append(data)
        return []

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    assert Bridge(store, config).sync()["imported"] == 1
    assert receipts == [{"id": other["id"]}]
    assert len(store.inbox("claude:recipient")) == 1


def test_reply_revocation_retains_history_without_pending_exposure(tmp_path: Path) -> None:
    store, config = setup_machine(tmp_path, "b")
    store.register("claude", "recipient", str(tmp_path / "repo"))
    remote = RemoteStore(store, config.connection)
    parent, child = delivery(), delivery()
    child["reply_to"] = parent["id"]
    remote.admit_message(parent, config.repositories[0].scope)
    remote.admit_message(child, config.repositories[0].scope)
    deny_inbox()
    assert store.inbox("claude:recipient") == []
    assert store.pending_count("claude:recipient") == 0
    assert remote.admit_message(child, config.repositories[0].scope) is None
    assert len(store.history(folder=None, conversation=1)) == 2


def test_reply_wire_requires_canonical_uuid_and_accepts_legacy_root(tmp_path: Path) -> None:
    _store, config = setup_machine(tmp_path, "b")
    item = delivery()
    assert "reply_to" not in parse_delivery(item, config.repositories[0], "b")
    for parent in (None, 1, True, "invalid", "12345678-1234-1234-1234-123456789012"):
        with pytest.raises(BusError):
            parse_delivery({**item, "reply_to": parent}, config.repositories[0], "b")


def test_reply_monitor_parent_never_promotes_into_native_history(tmp_path: Path) -> None:
    store, config = setup_machine(tmp_path, "b")
    store.register("claude", "recipient", str(tmp_path / "repo"))
    path = Path(os.environ["LLM_BUS_CONFIG"])
    original = path.read_text(encoding="utf-8")
    path.write_text(
        original.replace('receive_messages = "inbox"', 'receive_messages = "monitor"'),
        encoding="utf-8",
    )
    remote = RemoteStore(store, config.connection)
    parent = delivery()
    assert remote.admit_message(parent, config.repositories[0].scope) == ("monitor", None, True)
    child = delivery()
    child["reply_to"] = parent["id"]
    assert remote.admit_message(child, config.repositories[0].scope) is None
    path.write_text(original, encoding="utf-8")
    assert remote.admit_message(parent, config.repositories[0].scope) == ("monitor", None, False)
    assert remote.admit_message(child, config.repositories[0].scope) is None
    assert store.history(folder=None) == []
    assert store.inbox("claude:recipient") == []
    assert len(RemoteStore.monitor_history(store, repository=None)) == 1


@pytest.mark.parametrize("status", [404, 405, 501])
def test_legacy_relay_holds_linked_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    # pylint: disable=protected-access,clean-code-commented-out-code
    store, config = setup_machine(tmp_path, "a")
    native = SenderContext("codex", "sender", str(tmp_path / "repo"))
    remote = RemoteStore(store, config.connection)
    parent = remote.enqueue(native, "b/xyz/claude:recipient", "xyz", "question")
    remote.set_state(cast("str", parent["remote_id"]), "acknowledged")
    child = enqueue_remote(
        store, config, native, "b/xyz/claude:recipient", "reply", reply_to=cast("int", parent["id"])
    )
    calls: list[str] = []

    def request(_self: object, _method: str, path: str, _data: object = None) -> object:
        calls.append(path)
        if path == "/v1/capabilities":
            raise RelayError("rejected", http_status=status)
        return {}

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    bridge = Bridge(store, config)
    assert remote_batches.sync_outgoing(bridge) == (0, 1)
    assert "/v1/messages" not in calls
    assert (
        remote.sync_snapshot("upload", cast("str", child["remote_id"]))["error_code"]
        == "unsupported_reply_link"
    )
    assert remote.outgoing()[0][1] == "queued_local"


@pytest.mark.parametrize(
    ("capabilities", "code"),
    [
        ({}, "unsupported_reply_link"),
        ({"reply_to": False}, "unsupported_reply_link"),
        ({"reply_to": "true"}, "protocol_error"),
        ({"reply_to": True, "unknown": True}, "protocol_error"),
        ({"reply_to": True, "batch_status": 1}, "protocol_error"),
    ],
)
def test_reply_capability_requires_explicit_boolean_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capabilities: object, code: str
) -> None:
    # pylint: disable=protected-access,clean-code-commented-out-code
    store, config = setup_machine(tmp_path, "a")
    native = SenderContext("codex", "sender", str(tmp_path / "repo"))
    remote = RemoteStore(store, config.connection)
    parent = remote.enqueue(native, "b/xyz/claude:recipient", "xyz", "question")
    remote.set_state(cast("str", parent["remote_id"]), "acknowledged")
    child = enqueue_remote(
        store, config, native, "b/xyz/claude:recipient", "reply", reply_to=cast("int", parent["id"])
    )

    def request(_self: object, _method: str, path: str, _data: object = None) -> object:
        assert path == "/v1/capabilities"
        return capabilities

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    assert remote_batches.sync_outgoing(Bridge(store, config)) == (0, 1)
    assert remote.sync_snapshot("upload", cast("str", child["remote_id"]))["error_code"] == code
