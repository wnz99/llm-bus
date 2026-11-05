"""Remote transport never converts typed directions into ordinary messages."""

from dataclasses import asdict, replace
from os import environ
from pathlib import Path
from uuid import uuid4

import pytest

from llm_bus.coordination.models import Direction
from llm_bus.errors import BusError
from llm_bus.mailbox.models import SenderContext
from llm_bus.mailbox.store import Store
from llm_bus.policy.config import Config
from llm_bus.remote.bridge import Bridge
from llm_bus.remote.http import RelayError
from llm_bus.remote.incoming import receive
from llm_bus.remote.outgoing import enqueue_remote, permits_outgoing, upload
from llm_bus.remote.protocol import parse_delivery
from llm_bus.remote.store import RemoteStore

from .support import admit_delivery, assert_inbox_admission, delivery, deny_inbox, setup_machine


def enroll(store: Store, config: Config, direction: Direction) -> None:
    """Two machines may import identifiers, but only each owner's grant counts."""
    scope = config.repositories[0].scope
    store.register("claude", "recipient", str(store.path.parent / "repo"))
    with store.transaction() as db:
        db.execute("UPDATE agents SET scope=? WHERE address='claude:recipient'", (scope,))
        db.execute(
            "INSERT INTO coordination_projects VALUES (?,?,?,?,?,?,?,?,?,NULL)",
            (
                scope,
                direction.project_id,
                "demo",
                direction.generation,
                "a/xyz/codex:sender",
                "review",
                "/unused",
                "memory-1",
                "[]",
            ),
        )
        db.execute(
            "INSERT INTO coordination_grants VALUES (?,?,?,?,?,?,?,?,NULL)",
            (
                scope,
                direction.project_id,
                "claude:recipient",
                direction.grant_id,
                direction.generation,
                "a/xyz/codex:sender",
                '["assign"]',
                None,
            ),
        )
        db.execute(
            "INSERT INTO coordination_enrollments VALUES (?,?,?,?,?)",
            (scope, direction.project_id, "claude:recipient", "worker", direction.generation),
        )


def direction_fixture() -> Direction:
    return Direction(1, str(uuid4()), str(uuid4()), str(uuid4()), "assign")


@pytest.mark.parametrize("change", ["grant_id", "generation", "operation", "sender", "recipient"])
def test_receiver_owns_exact_authority(tmp_path: Path, change: str) -> None:
    store, config = setup_machine(tmp_path, "b")
    direction = direction_fixture()
    enroll(store, config, direction)
    item = delivery()
    item["coordination"] = asdict(direction)
    if change in {"grant_id", "generation"}:
        item["coordination"][change] = str(uuid4())
    elif change == "operation":
        item["coordination"][change] = "cancel"
    else:
        item[change] = "a/xyz/codex:forged" if change == "sender" else "b/xyz/claude:other"
    assert admit_delivery(store, config, item) is None
    assert store.inbox("claude:recipient") == []


def test_grant_revocation_keeps_history_suppresses_pending(tmp_path: Path) -> None:
    store, config = setup_machine(tmp_path, "b")
    direction = direction_fixture()
    enroll(store, config, direction)
    item = delivery()
    item["coordination"] = asdict(direction)
    key = assert_inbox_admission(admit_delivery(store, config, item))
    assert store.pending_allowed(key)
    assert store.inbox("claude:recipient")[0].get("coordination", {})["verified"] is True
    with store.transaction() as db:
        db.execute("UPDATE coordination_grants SET revoked_at='revoked'")
    assert not store.pending_allowed(key)
    assert store.inbox("claude:recipient") == []
    with store.transaction() as db:
        assert db.execute("SELECT body FROM messages WHERE id=?", (key,)).fetchone() == (
            item["body"],
        )


def test_policy_intersection_and_independent_store(tmp_path: Path) -> None:
    direction = direction_fixture()
    store, config = setup_machine(tmp_path, "b")
    enroll(store, config, direction)
    item = delivery()
    item["coordination"] = asdict(direction)
    other = Store(tmp_path / "other.sqlite3")
    enroll(other, config, replace(direction, grant_id=str(uuid4())))
    assert admit_delivery(other, config, item) is None
    deny_inbox()
    assert admit_delivery(store, config, item) is None


@pytest.mark.parametrize("metadata", [{}, {"version": 2}, {"version": True}, {"extra": "assign"}])
def test_parser_rejects_unknown_metadata(tmp_path: Path, metadata: dict[str, object]) -> None:
    _, config = setup_machine(tmp_path, "b")
    item = dict(delivery(), coordination=metadata)
    with pytest.raises(BusError):
        parse_delivery(item, config.repositories[0], "b")


def test_old_relay_never_gets_direction_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, config = setup_machine(tmp_path, "a")
    routes: list[str] = []

    def request(_self: object, _method: str, route: str, _data: object = None) -> object:
        routes.append(route)
        raise RelayError("not_found", http_status=404)

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    item = delivery()
    item["coordination"] = asdict(direction_fixture())
    with pytest.raises(RelayError) as error:
        upload(Bridge(store, config), item)
    assert error.value.code == "unsupported_coordination"
    assert routes == ["/v2/coordination/capabilities"]


def test_denied_versions_do_not_block_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, config = setup_machine(tmp_path, "b")
    direction = direction_fixture()
    enroll(store, config, direction)
    valid = dict(delivery(), sequence=2, coordination=asdict(direction))
    invalid = {**valid, "sequence": 1, "coordination": {"version": 2}}
    receipt_payloads: list[object] = []

    def request(_self: object, _method: str, route: str, data: object = None) -> object:
        if route == "/v2/coordination/capabilities":
            return {"version": 1}
        if route.startswith("/v2/coordination/inbox?") and "after=0" in route:
            return [invalid, valid]
        if route == "/v1/receipts":
            receipt_payloads.append(data)
            return {}
        return []

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    assert receive(Bridge(store, config), config.repositories[0]) == 1
    assert receipt_payloads == [{"id": valid["id"]}]
    assert len(store.inbox("claude:recipient")) == 1


def test_monitor_direction_never_enters_pending(tmp_path: Path) -> None:
    store, config = setup_machine(tmp_path, "b")
    direction = direction_fixture()
    enroll(store, config, direction)
    path = Path(environ["LLM_BUS_CONFIG"])
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            'receive_messages = "inbox"', 'receive_messages = "monitor"'
        ),
        encoding="utf-8",
    )
    item = delivery()
    item["coordination"] = asdict(direction)
    assert admit_delivery(store, config, item) == ("monitor", None, True)
    assert store.inbox("claude:recipient") == []
    assert store.pending_count("claude:recipient") == 0
    with store.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM coordination_messages").fetchone() == (0,)


def test_sender_queue_rechecks_enrollment_and_uses_only_versioned_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, config = setup_machine(tmp_path, "a")
    direction = direction_fixture()
    enroll(store, config, direction)
    scope = config.repositories[0].scope
    with store.transaction() as db:
        db.execute("UPDATE coordination_projects SET coordinator='codex:sender'")
        db.execute(
            "INSERT INTO coordination_enrollments VALUES (?,?,?,?,?)",
            (scope, direction.project_id, "codex:sender", "coordinator", direction.generation),
        )
    sender = SenderContext("codex", "sender", str(tmp_path / "repo"))
    enqueue_remote(store, config, sender, "b/xyz/claude:recipient", "task", direction=direction)
    envelope = RemoteStore(store, config.connection).outgoing()[0][0]
    assert envelope.get("coordination") == asdict(direction)
    routes: list[str] = []

    def request(_self: object, _method: str, route: str, _data: object = None) -> object:
        routes.append(route)
        if route == "/v2/coordination/capabilities":
            return {"version": 1}
        return {"id": envelope["id"], "status": "accepted_remote"}

    monkeypatch.setattr("llm_bus.remote.http.Relay.request", request)
    bridge = Bridge(store, config)
    assert permits_outgoing(bridge, envelope)
    assert upload(bridge, envelope)
    assert "/v2/coordination/messages" in routes
    assert "/v1/messages" not in routes
    with store.transaction() as db:
        db.execute("DELETE FROM coordination_enrollments WHERE address='codex:sender'")
    assert not permits_outgoing(bridge, envelope)
    with pytest.raises(BusError, match="coordinator enrollment"):
        enqueue_remote(store, config, sender, "b/xyz/claude:recipient", "task", direction=direction)
