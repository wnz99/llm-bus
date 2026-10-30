"""Models support for advisory resource locks."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, NamedTuple, TypedDict

if TYPE_CHECKING:
    from llm_bus.wake.adapters import WakeResult


DEFAULT_TTL_MINUTES = 24 * 60
MAX_TTL_MINUTES = 7 * 24 * 60
NUDGE_INTERVAL_MINUTES = 5
MAX_RESOURCE_LENGTH = 200
MAX_REASON_LENGTH = 500
MAX_DESCRIPTION_LENGTH = 200
HOOK_NAME_LIMIT = 10
HOOK_DESCRIPTION_CHARS = 60
CATALOG_IDLE_DAYS = 30
SIMILAR_RATIO = 0.8
SHARED_SCOPE = "shared"
CHECK_UNITS = {"": 1, "m": 1, "h": 60, "d": 24 * 60}
CHECK_PATTERN = re.compile(r"^(\d{1,6})\s*([mhd]?)$")
CHECK_HELP = "a duration such as 45m, 2h, or 1d"


class Waiter(TypedDict):
    """Session queued for a held resource, in request order."""

    waiter: str
    reason: str | None
    requested_at: str
    nudged_at: str | None


class Lock(TypedDict):
    """Current holder of a resource, its lease and check times, and the queue behind it."""

    resource: str
    scope: str
    holder: str
    reason: str | None
    acquired_at: str
    expires_at: str
    check_at: str
    checked_at: str | None
    waiters: list[Waiter]


class Delivery(TypedDict):
    """Bus message sent because a lock changed hands, was requested, or came up for a check."""

    to: str
    message_id: int
    wake: WakeResult


class Notice(NamedTuple):
    recipient: str
    body: str
    sender: str | None = None


class Key(NamedTuple):
    scope: str
    resource: str


class Request(NamedTuple):
    """One acquire: release estimate, lease, purpose, and how to treat names and holders."""

    check: str | None = None
    reason: str | None = None
    ttl_minutes: int | None = None
    describe: str | None = None
    new: bool = False
    notify: bool = False
