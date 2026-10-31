"""Standalone client and validation for the llm-bus history JSON contract."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import cast

PAGE_SIZE = 50


@dataclass(frozen=True)
class Message:  # pylint: disable=too-many-instance-attributes
    # Fields mirror the history JSON contract, including nullable legacy wake metadata.
    id: int
    sender: str
    recipient: str
    body: str
    sender_cwd: str | None
    recipient_cwd: str | None
    sent_at: str
    acknowledged_at: str | None
    wake_status: str | None
    wake_via: str | None
    wake_reason: str | None
    wake_checked_at: str | None
    reply_to: int | None = None


def clean(value: str) -> str:
    """Keep terminal control and Unicode formatting characters out of curses."""
    return "".join(char if char.isprintable() else "?" for char in value)


def parse_messages(raw: str) -> list[Message]:
    # pylint: disable=too-many-locals
    # Validate the full record before constructing a typed message.
    try:
        records = cast("object", json.loads(raw))
    except json.JSONDecodeError as exc:
        raise ValueError("llm-bus returned invalid JSON") from exc
    if not isinstance(records, list):
        raise ValueError("llm-bus history must return a JSON array")  # noqa: TRY004
    messages: list[Message] = []
    for item in cast("list[object]", records):
        if not isinstance(item, dict):
            raise ValueError("llm-bus history contains a non-object message")  # noqa: TRY004
        record = cast("dict[str, object]", item)
        message_id = record.get("id")
        if not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0:
            raise ValueError("llm-bus history message has an invalid ID")
        reply_to = record.get("reply_to")
        if reply_to is not None and (
            isinstance(reply_to, bool) or not isinstance(reply_to, int) or reply_to <= 0
        ):
            raise ValueError("llm-bus history message has an invalid reply ID")
        sender, recipient, body, sent_at = (
            record.get("sender"),
            record.get("recipient"),
            record.get("body"),
            record.get("sent_at"),
        )
        if not all(isinstance(value, str) for value in (sender, recipient, body, sent_at)):
            raise ValueError("llm-bus history message has an invalid text field")
        (
            sender_cwd,
            recipient_cwd,
            acknowledged_at,
            wake_status,
            wake_via,
            wake_reason,
            wake_checked_at,
        ) = (
            record.get("sender_cwd"),
            record.get("recipient_cwd"),
            record.get("acknowledged_at"),
            record.get("wake_status"),
            record.get("wake_via"),
            record.get("wake_reason"),
            record.get("wake_checked_at"),
        )
        if any(
            value is not None and not isinstance(value, str)
            for value in (
                sender_cwd,
                recipient_cwd,
                acknowledged_at,
                wake_status,
                wake_via,
                wake_reason,
                wake_checked_at,
            )
        ):
            raise ValueError("llm-bus history message has an invalid optional field")
        messages.append(
            Message(
                message_id,
                cast("str", sender),
                cast("str", recipient),
                cast("str", body),
                cast("str | None", sender_cwd),
                cast("str | None", recipient_cwd),
                cast("str", sent_at),
                cast("str | None", acknowledged_at),
                cast("str | None", wake_status),
                cast("str | None", wake_via),
                cast("str | None", wake_reason),
                cast("str | None", wake_checked_at),
                reply_to,
            )
        )
    return messages


def fetch_history(binary: str, folder: str | None, before: int | None = None) -> list[Message]:
    command = (
        [binary, "history", "--repository", folder] if folder else [binary, "history", "--all"]
    )
    command.extend(("--limit", str(PAGE_SIZE)))
    if before is not None:
        command.extend(("--before", str(before)))
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command, capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Cannot run llm-bus history: {exc}") from exc
    if result.returncode:
        detail = (
            clean(result.stderr.strip().splitlines()[-1]) if result.stderr.strip() else "no details"
        )
        # pylint: disable-next=clean-code-business-policy-literal
        raise ValueError(f"llm-bus history failed ({result.returncode}): {detail}")
    return parse_messages(result.stdout)
