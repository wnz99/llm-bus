"""Protocol ownership."""

from __future__ import annotations

import re
from datetime import datetime
from typing import TypedDict

from llm_bus.errors import BusError
from llm_bus.policy.fields import object_fields

UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")


def report_deadline(value: object) -> str:
    if not isinstance(value, str) or not UTC_TIMESTAMP.fullmatch(value):
        raise BusError("Invalid relay report deadline")
    try:
        datetime.fromisoformat(value)
    except ValueError as error:
        raise BusError("Invalid relay report deadline") from error
    return value


REPORT_REASONS: dict[str, tuple[str | None, ...]] = {
    "received": (None,),
    "proceeding": ("started", "resumed"),
    "not_proceeding": (
        "needs_approval",
        "missing_information",
        "dependency_blocked",
        "out_of_scope",
        "declined",
    ),
    "completed": (None,),
    "failed": ("validation_failed", "execution_failed", "permission_denied", "unavailable"),
}

REPORT_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")

MAX_REVISION = 2**53 - 1


class Report(TypedDict):
    message_id: str
    revision: int
    status: str | None
    reason: str | None


def validate_status(status: object, reason: object) -> tuple[str, str | None]:
    if (
        not isinstance(status, str)
        or status not in REPORT_REASONS
        or (reason is not None and not isinstance(reason, str))
        or reason not in REPORT_REASONS[status]
    ):
        raise BusError("Invalid built-in report status/reason")
    return status, reason


def parse_report(value: object, message_id: str) -> Report:
    data = object_fields(value)
    revision = data.get("revision")
    if (
        set(data) != {"message_id", "revision", "status", "reason"}
        or not REPORT_UUID.fullmatch(message_id)
        or data.get("message_id") != message_id
    ):
        raise BusError("Invalid work report response")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or not 0 <= revision <= MAX_REVISION
    ):
        raise BusError("Invalid work report revision")
    if revision == 0:
        if data["status"] is not None or data["reason"] is not None:
            raise BusError("Invalid initial work report")
        return Report(message_id=message_id, revision=0, status=None, reason=None)
    status, reason = validate_status(data["status"], data["reason"])
    return Report(message_id=message_id, revision=revision, status=status, reason=reason)
