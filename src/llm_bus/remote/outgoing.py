"""Outgoing ownership."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, cast

from llm_bus.coordination.delivery import require_coordinator
from llm_bus.coordination.models import Direction
from llm_bus.errors import BusError
from llm_bus.hosts.sessions import is_subagent
from llm_bus.policy.config import NATIVE, Config
from llm_bus.policy.fields import IDENTIFIER, object_fields
from llm_bus.remote import discovery, protocol
from llm_bus.remote.constants import BATCH_CAPABILITY_SECONDS, UNSUPPORTED_BATCH
from llm_bus.remote.http import HTTP_GET, HTTP_POST, RelayError
from llm_bus.remote.protocol import ADDRESS_PARTS
from llm_bus.remote.store import RemoteStore
from llm_bus.reports.store import Reports

if TYPE_CHECKING:
    from llm_bus.mailbox.models import SenderContext
    from llm_bus.mailbox.store import Store
    from llm_bus.remote.models import Envelope

if TYPE_CHECKING:
    from llm_bus.remote.bridge import Bridge


def enqueue_remote(  # noqa: PLR0913 - additive reply keyword preserves callers
    store: Store,
    config: Config,
    sender: SenderContext,
    recipient: str,
    body: str,
    *,
    reply_to: int | None = None,
    direction: Direction | None = None,
) -> dict[str, object]:
    # pylint: disable=too-many-arguments
    repo = config.repository(sender.project)
    if repo is None:
        raise BusError("Remote sending is disabled for this repository")
    parts = recipient.split("/")
    if (
        len(parts) != ADDRESS_PARTS
        or not IDENTIFIER.fullmatch(parts[0])
        or parts[0] == config.machine_id
        or parts[1] != repo.repo_id
        or not NATIVE.fullmatch(parts[2])
    ):
        raise BusError("Remote recipient must belong to another machine in the same repository")
    peer = config.peer(repo.repo_id, parts[0])
    if peer is None or not peer.send_messages:
        raise BusError("Local capability policy: message sending is disabled for this peer")
    return RemoteStore(store, config.connection).enqueue(
        sender,
        recipient,
        repo.repo_id,
        body,
        scope=repo.scope,
        reply_to=reply_to,
        direction=direction,
    )


def permits_outgoing(self: Bridge, envelope: Envelope) -> bool:
    try:
        if is_subagent(envelope["sender"]):
            return False
        current = self.current_config()
    except BusError:
        return False
    if current is None:
        return False
    if "coordination" in envelope:
        try:
            with self.store.transaction() as db:
                require_coordinator(
                    db,
                    envelope["sender"],
                    self.remote.source_scope(envelope["id"]) or "",
                    Direction.parse(envelope["coordination"]),
                )
        except BusError:
            return False
    peer = current.peer(envelope["repo_id"], envelope["recipient"].split("/", 1)[0])
    repo = next(
        (item for item in current.repositories if item.repo_id == envelope["repo_id"]),
        None,
    )
    return (
        peer is not None
        and peer.send_messages
        and repo is not None
        and repo.scope == self.remote.source_scope(envelope["id"])
    )


def upload(self: Bridge, envelope: Envelope) -> bool:
    if "coordination" in envelope and not supports_coordination(self):
        raise RelayError("unsupported_coordination")
    if "reply_to" in envelope and not supports_reply(self):
        raise RelayError("unsupported_reply_link")
    discovery.publish_original_sender(self, envelope)
    if not permits_outgoing(self, envelope):
        return False
    route = "/v2/coordination/messages" if "coordination" in envelope else "/v1/messages"
    result = self.relay.request(HTTP_POST, route, envelope)
    apply_status(self, envelope, result)
    return True


def supports_coordination(self: Bridge) -> bool:
    """Probe separately so old clients keep their exact capability contract."""
    try:
        value = self.relay.request(HTTP_GET, "/v2/coordination/capabilities")
    except RelayError as error:
        if error.http_status not in UNSUPPORTED_BATCH:
            raise
        return False
    if not isinstance(value, dict):
        return False
    data = object_fields(cast("object", value))
    return (
        set(data) == {"version"}
        and isinstance(data["version"], int)
        and not isinstance(data["version"], bool)
        and data["version"] == 1
    )


def supports_reply(self: Bridge) -> bool:
    cached = self.reply_capability
    if cached is not None and self.clock() < cached[0]:
        return cached[1]
    try:
        data = object_fields(self.relay.request(HTTP_GET, "/v1/capabilities"))
    except RelayError as error:
        if error.http_status not in UNSUPPORTED_BATCH:
            raise
        supported = False
    else:
        if set(data) - {"reply_to", "batch_status", "batch_reports"} or any(
            not isinstance(value, bool) for value in data.values()
        ):
            raise RelayError("protocol_error")
        supported = data.get("reply_to") is True
    self.reply_capability = (
        self.clock() + timedelta(seconds=BATCH_CAPABILITY_SECONDS),
        supported,
    )
    return supported


def apply_status(self: Bridge, envelope: Envelope, value: object) -> None:
    data = protocol.validate_status(envelope["id"], value)
    if "report_expires_at" in data:
        Reports(self.store, self.config.connection).deadline(
            envelope["id"], data["report_expires_at"]
        )
    self.remote.set_state(envelope["id"], cast("str", data["status"]))
    self.remote.observe_acceptance(envelope["id"], self.clock())
