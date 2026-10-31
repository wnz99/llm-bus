"""retry."""

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
from llm_bus.reports import sync as report_sync
from llm_bus.reports.store import Reports

from .support import FakeClock, setup_machine


@pytest.mark.parametrize("status", [404, 405, 501, 401, 403, 429, 503])
def test_batch_fallback_only_for_unsupported_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    # pylint: disable=protected-access,clean-code-commented-out-code,clean-code-redundant-comment
    store, config = setup_machine(tmp_path, "a")
    bridge = Bridge(store, config)
    remote_id = str(uuid4())
    calls: list[str] = []

    def request(_self: object, _method: str, path: str, _data: object = None) -> object:
        calls.append(path)
        if path.endswith("/batch"):
            raise RelayError("rejected", http_status=status)
        return {"id": remote_id, "status": "accepted_remote"}

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    if status in {404, 405, 501}:
        values = remote_batches.read_batch(bridge, "status", [remote_id])
        assert values[remote_id][0] == {"id": remote_id, "status": "accepted_remote"}
        assert len(calls) == 2
    else:
        with pytest.raises(RelayError):
            remote_batches.read_batch(bridge, "status", [remote_id])
        assert len(calls) == 1


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "extra", "wrong_id", "bad_error", "bad_report"]
)
def test_batch_rejects_malformed_membership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    # pylint: disable=protected-access,clean-code-commented-out-code,clean-code-redundant-comment
    store, config = setup_machine(tmp_path, "a")
    bridge = Bridge(store, config)
    remote_id = str(uuid4())

    def request(*_args: object) -> object:
        valid: dict[str, object] = {
            "id": remote_id,
            "value": {"id": remote_id, "status": "accepted_remote"},
            "error": None,
        }
        if fault == "missing":
            return {"results": []}
        if fault == "duplicate":
            return {"results": [valid, valid]}
        if fault == "extra":
            return {"results": [valid], "extra": True}
        if fault == "wrong_id":
            return {"results": [{**valid, "id": str(uuid4())}]}
        if fault == "bad_error":
            return {"results": [{**valid, "value": None, "error": {"code": {"injected": "text"}}}]}
        return {"results": [{**valid, "value": {"message_id": remote_id, "status": "custom"}}]}

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    with pytest.raises(BusError):
        remote_batches.read_batch(
            bridge, "report" if fault == "bad_report" else "status", [remote_id]
        )


def test_batch_capability_negative_cache_expires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # pylint: disable=protected-access,clean-code-commented-out-code,clean-code-redundant-comment
    store, config = setup_machine(tmp_path, "a")
    clock = FakeClock()
    bridge = Bridge(store, config, clock=clock)
    key = str(uuid4())
    batch_attempts: list[str] = []

    def request(_self: object, _method: str, path: str, _data: object = None) -> object:
        if path.endswith("/batch"):
            batch_attempts.append(path)
            raise RelayError("rejected", http_status=404)
        return {"id": key, "status": "accepted_remote"}

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    remote_batches.read_batch(bridge, "status", [key])
    clock.advance(299)
    remote_batches.read_batch(bridge, "status", [key])
    assert len(batch_attempts) == 1
    clock.advance(1)
    remote_batches.read_batch(bridge, "status", [key])
    assert len(batch_attempts) == 2


@pytest.mark.parametrize("malformed", [False, True])
def test_batch_status_applies_authorized_neighbors_after_full_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, malformed: bool
) -> None:
    # pylint: disable=protected-access,clean-code-commented-out-code,clean-code-redundant-comment
    store, config = setup_machine(tmp_path, "a")
    clock = FakeClock()
    bridge = Bridge(store, config, clock=clock)
    for body in ("one", "two"):
        bridge.remote.enqueue(
            SenderContext("codex", "s", str(tmp_path / "repo")), "b/xyz/claude:r", "xyz", body
        )
    envelopes = [e for e, _state in bridge.remote.outgoing()]
    for envelope in envelopes:
        bridge.remote.set_state(envelope["id"], "accepted_remote")

    def request(_self: object, _method: str, _path: str, _data: object = None) -> object:
        return {
            "results": [
                {
                    "id": envelopes[0]["id"],
                    "value": {"id": envelopes[0]["id"], "status": "delivered_local"},
                    "error": None,
                },
                {
                    "id": envelopes[1]["id"],
                    "value": {"id": str(uuid4()), "status": "acknowledged"} if malformed else None,
                    "error": None if malformed else {"code": "not_allowed"},
                },
            ]
        }

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    assert remote_batches.poll_page(bridge, "status", envelopes) == (2 if malformed else 1)
    by_id = {row["remote_id"]: row for row in bridge.remote.statuses()}
    assert by_id[envelopes[0]["id"]]["status"] == (
        "accepted_remote" if malformed else "delivered_local"
    )
    assert by_id[envelopes[1]["id"]]["status"] == "accepted_remote"


def test_batch_pages_cover_large_outbox_and_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # pylint: disable=protected-access,clean-code-commented-out-code,clean-code-redundant-comment
    store, config = setup_machine(tmp_path, "a")
    path = Path(os.environ["LLM_BUS_CONFIG"])
    path.write_text(
        path.read_text(encoding="utf-8") + 'receive_reports = ["received"]\n', encoding="utf-8"
    )
    clock = FakeClock()
    bridge = Bridge(store, config, clock=clock)
    reports = Reports(store, config.connection)
    for index in range(205):
        sent = bridge.remote.enqueue(
            SenderContext("codex", "s", str(tmp_path / "repo")), "b/xyz/claude:r", "xyz", str(index)
        )
        key = str(sent["remote_id"])
        bridge.remote.set_state(key, "accepted_remote")
        reports.deadline(key, "2099-01-01T00:00:00.000Z")
    batches: dict[str, list[int]] = {"status": [], "reports": []}

    def request(_self: object, _method: str, route: str, data: object = None) -> object:
        kind = "status" if route == "/v1/status/batch" else "reports"
        assert route in {"/v1/status/batch", "/v1/reports/batch"}
        assert isinstance(data, dict)
        ids = cast(
            "list[str]",
            cast("dict[str, object]", data)["ids" if kind == "status" else "message_ids"],
        )
        batches[kind].append(len(ids))
        return {
            "results": [
                {
                    "id": key,
                    "value": {"id": key, "status": "delivered_local"}
                    if kind == "status"
                    else {"message_id": key, "revision": 0, "status": None, "reason": None},
                    "error": None,
                }
                for key in ids
            ]
        }

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    assert remote_batches.sync_outgoing(bridge) == (205, 0)
    assert report_sync.poll_reports(bridge) == 0
    assert batches == {"status": [100, 100, 5], "reports": [100, 100, 5]}
