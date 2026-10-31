"""Models ownership."""

from __future__ import annotations

from typing import NotRequired, TypedDict
from uuid import UUID

from llm_bus.errors import BusError

REPLY_UUID_VERSION = 4


def validate_reply_uuid(value: str) -> None:
    """Accept canonical UUIDv4 parent identities only."""
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise BusError("Invalid remote reply UUID") from error
    if parsed.version != REPLY_UUID_VERSION or str(parsed) != value:
        raise BusError("Invalid remote reply UUID")


class Envelope(TypedDict):
    id: str
    sender: str
    recipient: str
    repo_id: str
    body: str
    reply_to: NotRequired[str]


class Peer(TypedDict):
    address: str
    machine_id: str
    repo_id: str
    native_address: str
    last_seen: str
    ended: int
    synchronization: NotRequired[dict[str, object]]


class Delivery(Envelope):
    sequence: int
    sent_at: str
