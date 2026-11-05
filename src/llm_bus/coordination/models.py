"""Strict direction wire data and bounded local references."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from llm_bus.errors import BusError

OPERATIONS = ("assign", "reprioritize", "clarify", "pause", "cancel", "review")
UUID_VERSION = 4


def uuid4_string(value: str) -> str:
    """Require canonical random UUIDs, without normalizing hostile input."""
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise BusError("Expected canonical UUIDv4") from error
    if parsed.version != UUID_VERSION or str(parsed) != value:
        raise BusError("Expected canonical UUIDv4")
    return value


def timestamp(value: str) -> datetime:
    """Parse an explicit timezone-aware lifetime."""
    if (
        re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value)
        is None
    ):
        raise BusError("Expected RFC3339 expiry")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as error:
        raise BusError("Expected RFC3339 expiry") from error
    if result.tzinfo is None or "T" not in value:
        raise BusError("Expected RFC3339 expiry with timezone")
    return result


@dataclass(frozen=True)
class Direction:
    """Immutable metadata; presence alone never establishes authority."""

    version: int
    project_id: str
    generation: str
    grant_id: str
    operation: str

    def validate(self) -> None:
        # bool subclasses int but is not a valid protocol version.
        # pylint: disable=unidiomatic-typecheck
        if type(self.version) is not int or self.version != 1 or self.operation not in OPERATIONS:
            raise BusError("Invalid direction")
        for value in (self.project_id, self.generation, self.grant_id):
            uuid4_string(value)

    @classmethod
    def parse(cls, value: object) -> "Direction":
        # pylint: disable=unidiomatic-typecheck
        if not isinstance(value, dict):
            raise BusError("Invalid direction fields")
        fields = cast("dict[str, object]", value)
        if set(fields) != {
            "version",
            "project_id",
            "generation",
            "grant_id",
            "operation",
        }:
            raise BusError("Invalid direction fields")
        if type(fields["version"]) is not int or not all(
            isinstance(fields[key], str)
            for key in ("project_id", "generation", "grant_id", "operation")
        ):
            raise BusError("Invalid direction types")
        result = cls(
            fields["version"],
            cast("str", fields["project_id"]),
            cast("str", fields["generation"]),
            cast("str", fields["grant_id"]),
            cast("str", fields["operation"]),
        )
        result.validate()
        return result


@dataclass(frozen=True)
class AuthorityDecision:
    """Receiver-derived decision, never a sender assertion."""

    verified: bool
    reason: str
    grant_id: str | None = None
