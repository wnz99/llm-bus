"""Strict direction wire data and bounded local references."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from llm_bus.errors import BusError

OPERATIONS = ("assign", "reprioritize", "clarify", "pause", "cancel", "review")
GATE_OPERATIONS: dict[str, str] = {
    "approve-commit": "commit",
    "approve-push": "push",
    "approve-merge": "merge",
    "approve-merge-admin": "merge-admin",
}
UUID_VERSION = 4
BRANCH = re.compile(r"(?!-)(?!.*\.\.)[A-Za-z0-9._/-]{1,200}")
SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
MAX_TICKET = 128
APPROVAL_KEYS = frozenset({"ticket", "branch", "base", "expires_at", "pr", "head_sha"})
REQUIRED_APPROVAL_KEYS = frozenset({"ticket", "branch", "base", "expires_at"})


def ticket_text(value: object) -> str:
    """Bounded ticket identifier shared by claims and approvals."""
    if not isinstance(value, str) or not 0 < len(value) <= MAX_TICKET or not value.isprintable():
        raise BusError("Invalid ticket")
    return value


def branch_name(value: object) -> str:
    if not isinstance(value, str) or BRANCH.fullmatch(value) is None:
        raise BusError("Invalid branch name")
    return value


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
        if (
            type(self.version) is not int
            or self.version != 1
            or (self.operation not in OPERATIONS and self.operation not in GATE_OPERATIONS)
        ):
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
class Approval:
    """Exact gate evidence a coordinator approves; never authority by itself."""

    ticket: str
    branch: str
    base: str
    expires_at: str
    pr: int | None = None
    head_sha: str | None = None

    def validate(self, operation: str) -> None:
        # bool subclasses int but is not a valid pull request number.
        # pylint: disable=unidiomatic-typecheck
        if operation not in GATE_OPERATIONS:
            raise BusError("Invalid approval operation")
        ticket_text(self.ticket)
        branch_name(self.branch)
        branch_name(self.base)
        timestamp(self.expires_at)
        merge = operation in {"approve-merge", "approve-merge-admin"}
        if self.pr is not None and (type(self.pr) is not int or self.pr < 1):
            raise BusError("Invalid approval pull request")
        if (self.pr is not None) != merge:
            raise BusError("Approval pull request is required for merge only")
        if self.head_sha is None:
            if operation != "approve-commit":
                raise BusError("Approval requires head_sha")
        elif operation == "approve-commit" or SHA.fullmatch(self.head_sha) is None:
            raise BusError("Invalid approval head_sha")

    def wire(self) -> dict[str, object]:
        fields: dict[str, object] = {
            "ticket": self.ticket,
            "branch": self.branch,
            "base": self.base,
            "expires_at": self.expires_at,
        }
        if self.pr is not None:
            fields["pr"] = self.pr
        if self.head_sha is not None:
            fields["head_sha"] = self.head_sha
        return fields

    @classmethod
    def parse(cls, value: object, operation: str) -> "Approval":
        if not isinstance(value, dict):
            raise BusError("Invalid approval fields")
        fields = cast("dict[str, object]", value)
        if not REQUIRED_APPROVAL_KEYS <= set(fields) <= APPROVAL_KEYS:
            raise BusError("Invalid approval fields")
        texts = {key: fields[key] for key in ("ticket", "branch", "base", "expires_at")}
        if not all(isinstance(item, str) for item in texts.values()):
            raise BusError("Invalid approval types")
        head_sha = fields.get("head_sha")
        if head_sha is not None and not isinstance(head_sha, str):
            raise BusError("Invalid approval types")
        result = cls(
            cast("str", texts["ticket"]),
            cast("str", texts["branch"]),
            cast("str", texts["base"]),
            cast("str", texts["expires_at"]),
            cast("int | None", fields.get("pr")),
            head_sha,
        )
        result.validate(operation)
        return result


@dataclass(frozen=True)
class Availability:
    """Authenticated observation; never a grant of authority."""

    version: int
    project_id: str
    generation: str
    grant_id: str
    kind: str

    def validate(self) -> None:
        # bool is not a wire integer.
        # pylint: disable=unidiomatic-typecheck
        if type(self.version) is not int or self.version != 1 or self.kind != "joined":
            raise BusError("Invalid availability")
        for value in (self.project_id, self.generation, self.grant_id):
            uuid4_string(value)

    @classmethod
    def parse(cls, value: object) -> "Availability":
        # pylint: disable=unidiomatic-typecheck
        if not isinstance(value, dict):
            raise BusError("Invalid availability fields")
        fields = cast("dict[str, object]", value)
        if set(fields) != {"version", "project_id", "generation", "grant_id", "kind"}:
            raise BusError("Invalid availability fields")
        if type(fields["version"]) is not int or not all(
            isinstance(fields[key], str) for key in ("project_id", "generation", "grant_id", "kind")
        ):
            raise BusError("Invalid availability types")
        result = cls(
            fields["version"],
            cast("str", fields["project_id"]),
            cast("str", fields["generation"]),
            cast("str", fields["grant_id"]),
            cast("str", fields["kind"]),
        )
        result.validate()
        return result


@dataclass(frozen=True)
class AuthorityDecision:
    """Receiver-derived decision, never a sender assertion."""

    verified: bool
    reason: str
    grant_id: str | None = None
