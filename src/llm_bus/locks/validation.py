"""Validation support for advisory resource locks."""

from __future__ import annotations

from llm_bus.errors import BusError
from llm_bus.locks.models import (
    CHECK_HELP,
    CHECK_PATTERN,
    CHECK_UNITS,
    DEFAULT_TTL_MINUTES,
    MAX_REASON_LENGTH,
    MAX_TTL_MINUTES,
)


def clean_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    text = " ".join(reason.split())
    if len(text) > MAX_REASON_LENGTH:
        raise BusError("Reason must be at most 500 characters")
    return text or None


def lease_minutes(minutes: int | None) -> int:
    if minutes is None:
        return DEFAULT_TTL_MINUTES
    if isinstance(minutes, bool) or not 1 <= minutes <= MAX_TTL_MINUTES:
        raise BusError("TTL must be 1-10080 minutes")
    return minutes


def check_minutes(check: str | None, ttl: int) -> int:
    """Parse the holder's release estimate; it must fall within the lease."""
    if check is None:
        raise BusError(f"Estimate when you can release with --check, {CHECK_HELP}")
    match = CHECK_PATTERN.match(check.strip().lower())
    if match is None:
        raise BusError(f"--check must be {CHECK_HELP}")
    minutes = int(match.group(1)) * CHECK_UNITS[match.group(2)]
    if minutes < 1:
        raise BusError("--check must be at least one minute ahead")
    if minutes > ttl:
        raise BusError(f"--check of {minutes} minutes is beyond the lease; raise --ttl")
    return minutes
