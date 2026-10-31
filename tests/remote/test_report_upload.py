"""reports."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from llm_bus import main
from llm_bus.errors import BusError
from llm_bus.mailbox.store import Store
from llm_bus.remote.bridge import Bridge
from llm_bus.remote.store import RemoteStore
from llm_bus.reports.store import Reports

from .support import (
    FakeClock,
    admit_delivery,
    allow_reports,
    assert_config_scope,
    assert_inbox_admission,
    delivery,
    imported_delivery,
    setup_machine,
)

if TYPE_CHECKING:
    from llm_bus.remote.models import Delivery


def test_reports_bind_native_recipient_and_survive_lost_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The retry clock is independent of report and recipient state.
    # pylint: disable=too-many-locals
    store, config = setup_machine(tmp_path, "b")
    path = Path(os.environ["LLM_BUS_CONFIG"])
    path.write_text(
        path.read_text(encoding="utf-8")
        + 'send_reports = ["received", "proceeding", "completed"]\n',
        encoding="utf-8",
    )
    store.register("claude", "recipient", str(tmp_path / "repo"))
    item = delivery()
    admitted = admit_delivery(store, config, item)
    local_id = assert_inbox_admission(admitted)
    reports = Reports(store, config.connection)
    with pytest.raises(BusError, match="native recipient"):
        reports.enqueue(local_id, "codex:other", "received", None)
    with pytest.raises(BusError, match="imported message"):
        reports.enqueue(local_id + 1, "claude:recipient", "received", None)
    queued = reports.enqueue(local_id, "claude:recipient", "received", None)
    later = reports.enqueue(local_id, "claude:recipient", "proceeding", "started")
    assert later["expected_revision"] == 1
    submissions: list[object] = []

    def request(_self: object, _method: str, route: str, data: object = None) -> object:
        if route == "/v1/reports":
            submissions.append(data)
            if len(submissions) == 1:
                raise BusError("response lost")
            return {"message_id": item["id"], "revision": 1, "status": "received", "reason": None}
        return []

    def forbid_wake(*_args: object, **_kwargs: object) -> None:
        pytest.fail("reports must not wake agents")

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    monkeypatch.setattr("llm_bus.wake.dispatch.wake", forbid_wake)
    clock = FakeClock()
    # Only report retry is under test; the imported inbox request predates this bridge pass.
    with store.transaction() as db:
        db.execute("UPDATE wake_dispatch SET next_attempt_at='9999-01-01T00:00:00+00:00'")
    assert Bridge(store, config, clock=clock).sync()["errors"] == 1
    assert Reports(Store(store.path), config.connection).pending() == [(local_id, queued)]
    clock.advance(5)
    assert Bridge(store, config, clock=clock).sync()["errors"] == 0
    assert submissions == [queued, queued]
    assert reports.pending() == [(local_id, later)]
    assert len(store.history(folder=None)) == 1
    assert (
        reports.enqueue(local_id, "claude:recipient", "completed", None)["expected_revision"] == 2
    )
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '"received", "proceeding", "completed"', '"received"'
        ),
        encoding="utf-8",
    )
    assert Bridge(store, config).sync()["errors"] == 0
    assert len(submissions) == 2
    with pytest.raises(BusError, match="disabled"):
        reports.enqueue(local_id, "claude:recipient", "completed", None)


def test_report_recovers_lost_receipt_after_inbox_permission_is_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Separate clock, receipt, report, and policy observations exercise recovery.
    # pylint: disable=too-many-locals
    store, config = setup_machine(tmp_path, "b")
    path = Path(os.environ["LLM_BUS_CONFIG"])
    original = path.read_text(encoding="utf-8") + 'send_reports = ["received"]\n'
    path.write_text(original, encoding="utf-8")
    store.register("claude", "recipient", str(tmp_path / "repo"))
    item = delivery()
    receipt_attempts = 0
    submitted: list[object] = []
    inbox_polls: list[str] = []

    def request(_self: object, _method: str, route: str, data: object = None) -> object:
        nonlocal receipt_attempts
        if route.startswith("/v1/inbox?"):
            inbox_polls.append(route)
            return [item] if "after=0" in route else []
        if route == "/v1/receipts":
            assert data == {"id": item["id"]}
            assert len(Store(store.path).history(folder=None)) == 1
            receipt_attempts += 1
            if receipt_attempts <= 2:
                raise BusError("receipt never reached relay")
            return {"id": item["id"], "status": "delivered_local"}
        if route == "/v1/reports":
            if receipt_attempts <= 2:
                raise BusError("Message not allowed")
            submitted.append(data)
            return {"message_id": item["id"], "revision": 1, "status": "received", "reason": None}
        return []

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    clock = FakeClock()
    assert Bridge(store, config, clock=clock).sync()["errors"] == 1
    local_id = store.history(folder=None)[0]["id"]
    assert isinstance(local_id, int)
    path.write_text(
        original.replace('receive_messages = "inbox"', 'receive_messages = "deny"'),
        encoding="utf-8",
    )
    reports = Reports(Store(store.path), config.connection)
    queued = reports.enqueue(local_id, "claude:recipient", "received", None)

    clock.advance(5)
    assert Bridge(Store(store.path), config, clock=clock).sync()["errors"] == 1
    assert receipt_attempts == 2
    assert reports.pending() == [(local_id, queued)]
    assert submitted == []
    clock.advance(10)
    assert Bridge(Store(store.path), config, clock=clock).sync()["errors"] == 0
    assert reports.pending() == []
    assert submitted == [queued]
    assert len(inbox_polls) == 1
    assert store.pending_count("claude:recipient") == 0
    assert len(store.history(folder=None)) == 1


@pytest.mark.parametrize("change", ["scope", "connection", "capability", "peer"])
def test_report_permission_revoked_during_receipt_prevents_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    store, config = setup_machine(tmp_path, "b")
    path = Path(os.environ["LLM_BUS_CONFIG"])
    original = path.read_text(encoding="utf-8") + 'send_reports = ["received"]\n'
    path.write_text(original, encoding="utf-8")
    store.register("claude", "recipient", str(tmp_path / "repo"))
    item, local_id = imported_delivery(store, config)
    reports = Reports(store, config.connection)
    queued = reports.enqueue(local_id, "claude:recipient", "received", None)
    original = original.replace('receive_messages = "inbox"', 'receive_messages = "deny"')
    path.write_text(original, encoding="utf-8")
    receipts: list[object] = []
    submissions: list[object] = []

    def request(_self: object, _method: str, route: str, data: object = None) -> object:
        if route == "/v1/receipts":
            receipts.append(data)
            replacements = {
                "scope": original.replace(
                    (tmp_path / "repo").as_posix(), (tmp_path / "elsewhere").as_posix()
                ),
                "connection": original.replace("https://bus.test", "https://different.test"),
                "capability": original.replace('send_reports = ["received"]', "send_reports = []"),
                "peer": original.replace('machine_id = "a"', 'machine_id = "c"'),
            }
            path.write_text(replacements[change], encoding="utf-8")
            return {"id": item["id"], "status": "delivered_local"}
        if route == "/v1/reports":
            submissions.append(data)
            return {"message_id": item["id"], "revision": 1, "status": "received", "reason": None}
        return []

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    assert Bridge(store, config).sync()["errors"] == 0
    if change == "scope":
        assert_config_scope(tmp_path / "elsewhere")
    assert receipts == [{"id": item["id"]}]
    assert submissions == []
    assert reports.pending() == [(local_id, queued)]


def test_cli_enqueues_new_report_while_older_upload_is_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store, config = setup_machine(tmp_path, "b")
    allow_reports()
    store.register("claude", "recipient", str(tmp_path / "repo"))
    admitted = RemoteStore(store, config.connection).admit_message(
        delivery(), config.repositories[0].scope
    )
    local_id = assert_inbox_admission(admitted)
    monkeypatch.setenv("LLM_BUS_DB", str(store.path))
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "recipient")
    monkeypatch.chdir(tmp_path / "repo")
    assert main(["report", str(local_id), "--status", "received"]) == 0
    capsys.readouterr()

    def request(_self: object, _method: str, route: str, data: object = None) -> object:
        if route == "/v1/reports":
            submission = cast("dict[str, object]", data)
            assert main(["report", str(local_id), "--status", "completed"]) == 0
            return {
                "message_id": submission["message_id"],
                "revision": 1,
                "status": "received",
                "reason": None,
            }
        return []

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    assert Bridge(store, config).sync()["errors"] == 0
    pending = Reports(Store(store.path), config.connection).pending()
    assert len(pending) == 1
    assert pending[0][1]["status"] == "completed"
    assert pending[0][1]["expected_revision"] == 1


@pytest.mark.parametrize("change", ["scope", "connection", "capability"])
def test_report_upload_reloads_all_binding_policy_at_final_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    store, config = setup_machine(tmp_path, "b")
    path = Path(os.environ["LLM_BUS_CONFIG"])
    original = path.read_text(encoding="utf-8") + 'send_reports = ["received"]\n'
    path.write_text(original, encoding="utf-8")
    store.register("claude", "recipient", str(tmp_path / "repo"))
    admitted = RemoteStore(store, config.connection).admit_message(
        delivery(), config.repositories[0].scope
    )
    local_id = assert_inbox_admission(admitted)
    Reports(store, config.connection).enqueue(local_id, "claude:recipient", "received", None)
    imported = Reports.imported

    def mutate_after_binding(reports: Reports, message: int, native: str | None = None) -> Delivery:
        item = imported(reports, message, native)
        replacements = {
            "scope": original.replace(
                (tmp_path / "repo").as_posix(), (tmp_path / "elsewhere").as_posix()
            ),
            "connection": original.replace("https://bus.test", "https://different.test"),
            "capability": original.replace('send_reports = ["received"]', "send_reports = []"),
        }
        path.write_text(replacements[change], encoding="utf-8")
        return item

    def request(_self: object, _method: str, route: str, _data: object = None) -> object:
        assert route != "/v1/reports"
        return []

    monkeypatch.setattr(Reports, "imported", mutate_after_binding)
    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    assert Bridge(store, config).sync()["errors"] == 0
    if change == "scope":
        assert_config_scope(tmp_path / "elsewhere")
    assert len(Reports(store, config.connection).pending()) == 1
