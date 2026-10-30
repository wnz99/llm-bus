"""admission."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from llm_bus.errors import BusError
from llm_bus.mailbox.models import SenderContext
from llm_bus.mailbox.store import Store
from llm_bus.policy.config import load_config
from llm_bus.policy.inbound import MessageIdentity, permits_inbox
from llm_bus.remote.credentials import read_token
from llm_bus.remote.outgoing import enqueue_remote
from llm_bus.remote.store import RemoteStore
from tests.policy.support import policy_text

from .support import admit_delivery, assert_inbox_admission, delivery, deny_inbox, setup_machine

if TYPE_CHECKING:
    from pathlib import Path


def test_pending_page_malformed_metadata_fails_closed(tmp_path: Path) -> None:
    store, config = setup_machine(tmp_path, "b")
    recipient = store.register("claude", "recipient", str(tmp_path / "repo"))
    remote = RemoteStore(store, config.connection)
    assert remote.admit_message(delivery(), config.repositories[0].scope) is not None
    with store.transaction() as db:
        db.execute("UPDATE remote_imports SET envelope='null'")
    assert store.inbox(recipient) == []
    assert store.pending_count(recipient) == 0
    assert not store.pending_allowed(1)
    assert len(store.history(folder=None)) == 1


def test_pending_page_skips_denied_prefix_and_replays(tmp_path: Path) -> None:
    store, config = setup_machine(tmp_path, "b")
    recipient = store.register("claude", "recipient", str(tmp_path / "repo"))
    remote = RemoteStore(store, config.connection)
    for _ in range(105):
        assert remote.admit_message(delivery(), config.repositories[0].scope) is not None
    local = store.send(SenderContext("codex", "local", str(tmp_path / "repo")), recipient, "local")
    deny_inbox()
    assert [item["id"] for item in store.inbox(recipient, 1)] == [local["id"]]
    assert store.inbox(recipient, 1) == store.inbox(recipient, 1)
    assert store.pending_count(recipient, limit=1) == 1
    assert len(store.history(folder=None, limit=100)) == 100


@pytest.mark.parametrize("operation", ["inbox", "count"])
def test_pending_page_rechecks_recipient_scope_between_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    store, config = setup_machine(tmp_path, "b")
    recipient = store.register("claude", "recipient", str(tmp_path / "repo"))
    remote = RemoteStore(store, config.connection)
    for _ in range(2):
        assert remote.admit_message(delivery(), config.repositories[0].scope) is not None
    mover = Store(store.path)
    checked = 0

    def move_after_check(
        envelope: MessageIdentity, stored_scope: str, current_scope: str, connection: str
    ) -> bool:
        nonlocal checked
        allowed = permits_inbox(envelope, stored_scope, current_scope, connection)
        checked += 1
        if checked == 1:
            mover.register("claude", "recipient", str(tmp_path / "other"))
        return allowed

    monkeypatch.setattr("llm_bus.policy.inbound.permits_inbox", move_after_check)
    if operation == "inbox":
        assert [item["id"] for item in store.inbox(recipient)] == [1]
    else:
        assert store.pending_count(recipient, limit=2) == 1
    assert not store.pending_allowed(2)
    assert len(store.history(folder=None)) == 2


def test_remote_send_survives_reopen_and_cannot_bypass_repo_policy(tmp_path: Path) -> None:
    store, config = setup_machine(tmp_path, "a")
    sender = SenderContext("codex", "sender", str(tmp_path / "repo"))
    sent = enqueue_remote(store, config, sender, "b/xyz/claude:recipient", "hello")
    reopened = RemoteStore(Store(store.path), config.connection)
    assert reopened.outgoing()[0][0]["id"] == sent["remote_id"]
    assert reopened.outgoing()[0][1] == "queued_local"
    assert store.inbox("b/xyz/claude:recipient")[0]["body"] == "hello"
    assert [peer["address"] for peer in store.agents()] == ["codex:sender"]
    for recipient in ("b/other/claude:recipient", "a/xyz/claude:recipient"):
        with pytest.raises(BusError, match="same repository"):
            enqueue_remote(store, config, sender, recipient, "denied")
    disabled = replace(config, repositories=(replace(config.repositories[0], peers=()),))
    with pytest.raises(BusError, match="disabled"):
        enqueue_remote(store, disabled, sender, "b/xyz/claude:recipient", "denied")
    with pytest.raises(BusError, match="another relay"):
        RemoteStore(store, "https://different.test|a")


def test_import_deduplicates_after_restart_and_preserves_ack_ownership(tmp_path: Path) -> None:
    store, config = setup_machine(tmp_path, "b")
    store.register("claude", "recipient", str(tmp_path / "repo"))
    item = delivery()
    admitted = admit_delivery(store, config, item)
    local_id = assert_inbox_admission(admitted)
    reopened = RemoteStore(Store(store.path), config.connection)
    assert reopened.admit_message(item, config.repositories[0].scope) == ("inbox", local_id, False)
    assert len(store.inbox("claude:recipient")) == 1
    with pytest.raises(BusError, match="different content"):
        reopened.admit_message({**item, "body": "changed"}, config.repositories[0].scope)
    with pytest.raises(BusError, match="do not belong"):
        store.acknowledge("codex:other", [local_id])
    store.acknowledge("claude:recipient", [local_id])
    assert reopened.acknowledgements() == [(item["id"], "xyz")]
    reopened.mark_acknowledged(item["id"])
    assert reopened.acknowledgements() == []


def test_session_moving_repository_does_not_import_old_repo_messages(tmp_path: Path) -> None:
    store, config = setup_machine(tmp_path, "b")
    store.register("claude", "recipient", str(tmp_path / "elsewhere"))
    remote = RemoteStore(store, config.connection)
    assert remote.admit_message(delivery(), config.repositories[0].scope) is None
    assert store.inbox("claude:recipient") == []


def test_config_fails_closed_and_tokens_are_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, config = setup_machine(tmp_path, "b")
    path = tmp_path / "config.toml"
    monkeypatch.setenv("LLM_BUS_CONFIG", str(path))
    content = policy_text() + 'send_messages = true\nreceive_messages = "deny"\n'
    path.write_text(content)
    loaded = load_config()
    assert loaded is not None
    peer = loaded.peer("xyz", "a")
    assert peer is not None
    assert peer.send_messages
    assert peer.receive_messages == "deny"
    assert read_token(config.credential_file) == "a" * 43
    path.write_text(content.replace('machine_id = "a"', 'machine_id = "b"'))
    with pytest.raises(BusError, match="Self-machine"):
        load_config()
    path.write_text(content.replace("https://bus.test", "http://bus.test"))
    with pytest.raises(BusError, match="HTTPS"):
        load_config()
    if config.credential_file.stat().st_mode & 0o077 == 0:
        config.credential_file.chmod(0o644)
        if os.name == "posix":
            with pytest.raises(BusError, match="mode 600"):
                read_token(config.credential_file)
    link = tmp_path / "link"
    try:
        link.symlink_to(config.credential_file)
    except OSError:
        pytest.skip("Symlinks unavailable")
    with pytest.raises(BusError, match="symlink"):
        read_token(link)
