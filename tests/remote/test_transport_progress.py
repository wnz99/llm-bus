"""status."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from llm_bus.errors import BusError
from llm_bus.mailbox.models import SenderContext
from llm_bus.mailbox.store import Store
from llm_bus.remote import outgoing
from llm_bus.remote.bridge import Bridge
from llm_bus.remote.store import RemoteStore

from .support import queued_delivery, setup_machine


def test_transport_progress_rejects_stale_response(tmp_path: Path) -> None:
    store, config = setup_machine(tmp_path, "a")
    remote = RemoteStore(store, config.connection)
    sent = queued_delivery(remote, tmp_path / "repo")
    remote_id = str(sent["remote_id"])
    remote.set_state(remote_id, "acknowledged")
    timestamp = store.history(folder=None)[0]["acknowledged_at"]
    remote.set_state(remote_id, "accepted_remote")
    assert remote.statuses()[0]["status"] == "acknowledged"
    remote.set_state(remote_id, "acknowledged")
    assert store.history(folder=None)[0]["acknowledged_at"] == timestamp
    with pytest.raises(BusError):
        remote.set_state(remote_id, "unknown")
    with pytest.raises(BusError):
        remote.set_state(str(uuid4()), "accepted_remote")


def test_message_status_distinguishes_transport_wake_ack_report(tmp_path: Path) -> None:
    store, config = setup_machine(tmp_path, "a")
    remote = RemoteStore(store, config.connection)
    sent = remote.enqueue(
        SenderContext("codex", "sender", str(tmp_path / "repo")),
        "b/xyz/claude:recipient",
        "xyz",
        "private body",
    )
    key = str(sent["remote_id"])
    now = datetime.now(UTC)
    remote.set_state(key, "acknowledged")
    remote.set_state(key, "accepted_remote")
    remote.record_sync_result("status", key, now, error_code=None)
    remote.record_sync_result("report_read", key, now, error_code="not_allowed")
    report = {"message_id": key, "revision": 1, "status": "proceeding", "reason": "started"}
    with store.transaction() as db:
        db.execute("INSERT INTO remote_reports VALUES (?,?)", (key, json.dumps(report)))
    Path(os.environ["LLM_BUS_CONFIG"]).unlink()
    before = store.path.read_bytes()
    result = store.message_status(cast("int", sent["id"]))
    assert result["transport"] == {
        "status": "acknowledged",
        "source": "relay_snapshot",
        "observed_at": now.isoformat(),
    }
    assert cast("dict[str, object]", result["wake"])["status"] is None
    assert result["acknowledged_at"] is not None
    assert result["work_report"] == report
    sync = cast("dict[str, object]", result["synchronization"])
    assert sync["stale"] is False
    operations = cast("list[dict[str, object]]", sync["operations"])
    assert {row["kind"]: row["error_code"] for row in operations} == {
        "status": None,
        "report_read": "not_allowed",
    }
    assert store.path.read_bytes() == before


def test_transport_concurrent_writers_preserve_ack(tmp_path: Path) -> None:
    store, config = setup_machine(tmp_path, "a")
    first = RemoteStore(store, config.connection)
    second = RemoteStore(Store(store.path), config.connection)
    sent = first.enqueue(
        SenderContext("codex", "sender", str(tmp_path / "repo")),
        "b/xyz/claude:recipient",
        "xyz",
        "hello",
    )
    remote_id = str(sent["remote_id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(remote.set_state, remote_id, state)
            for remote, state in ((first, "acknowledged"), (second, "accepted_remote"))
        ]
        for future in futures:
            future.result()
    second.set_state(remote_id, "expired")
    assert first.statuses()[0]["status"] == "acknowledged"
    assert store.history(folder=None)[0]["acknowledged_at"] is not None


def test_import_lookup_uses_local_id_index(tmp_path: Path) -> None:
    store, _config = setup_machine(tmp_path, "a")
    with store.transaction() as db:
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT envelope FROM remote_imports WHERE local_id=?", (1,)
        ).fetchall()
    assert "remote_imports_local_id" in str(plan)


def test_transport_progress_binds_response_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # pylint: disable=protected-access,clean-code-commented-out-code
    # Exercise status response binding without unrelated bridge network operations.
    store, config = setup_machine(tmp_path, "a")
    bridge = Bridge(store, config)
    sent = bridge.remote.enqueue(
        SenderContext("codex", "sender", str(tmp_path / "repo")),
        "b/xyz/claude:recipient",
        "xyz",
        "hello",
    )
    envelope = bridge.remote.outgoing()[0][0]

    def request(_self: object, _method: str, _path: str, _body: object = None) -> object:
        return {"id": str(uuid4()), "status": "acknowledged"}

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    with pytest.raises(BusError, match="ID conflicts"):
        outgoing.upload(bridge, envelope)
    assert bridge.remote.statuses()[0]["id"] == sent["id"]
    assert bridge.remote.statuses()[0]["status"] == "queued_local"
    assert store.history(folder=None)[0]["acknowledged_at"] is None
