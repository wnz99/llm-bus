"""Collaboration policy ownership."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

from llm_bus.collab.models import (
    DEFAULT_MODE,
    FULL_MAX_MINUTES,
    FULL_MODE,
    GITHUB_USER,
    MODES,
    TIME_FORMAT,
    CollabError,
    Grant,
    Peer,
)


def now() -> datetime:
    return datetime.now(UTC)


def stamp(moment: datetime) -> str:
    return moment.strftime(TIME_FORMAT)


def parse_stamp(value: str) -> datetime:
    return datetime.strptime(value, TIME_FORMAT).replace(tzinfo=UTC)


def github_user(value: object) -> str:
    if not isinstance(value, str) or not GITHUB_USER.fullmatch(value) or "--" in value:
        raise CollabError("Invalid GitHub username")
    return value


def grant(
    mode: object,
    full_minutes: object = None,
    *,
    review_replies: bool = True,
    agent_replies: bool = False,
    wake_on_answer: bool = False,
) -> Grant:
    """Validate a human's mode choice; full access always expires."""
    if mode not in MODES:
        raise CollabError(f"Mode must be one of {', '.join(MODES)}")
    full_until = None
    if mode == FULL_MODE:
        if not isinstance(full_minutes, int) or not 1 <= full_minutes <= FULL_MAX_MINUTES:
            raise CollabError(f"Full mode needs an expiry between 1 and {FULL_MAX_MINUTES} minutes")
        full_until = stamp(now() + timedelta(minutes=full_minutes))
    return Grant(cast("str", mode), full_until, review_replies, agent_replies, wake_on_answer)


def effective_mode(peer: Peer) -> str | None:
    """Current local mode, or None when revoked; expired full access falls back to monitor."""
    if peer["revoked_at"] is not None:
        return None
    if peer["mode"] == FULL_MODE and (
        peer["full_until"] is None or parse_stamp(peer["full_until"]) <= now()
    ):
        return DEFAULT_MODE
    return peer["mode"]


def grant_values(choice: Grant) -> tuple[str, str | None, int, int, int]:
    """Encode one owner's grant in the shared SQLite column order."""
    return (
        choice.mode,
        choice.full_until,
        int(choice.review_replies),
        int(choice.agent_replies),
        int(choice.wake_on_answer),
    )
