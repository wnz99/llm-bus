"""One receiver-side predicate for all direction exposure consumers."""

import json
from datetime import datetime
from sqlite3 import Connection
from typing import cast

from llm_bus.coordination.models import OPERATIONS, AuthorityDecision, Direction, timestamp
from llm_bus.errors import BusError


def authorize(  # noqa: PLR0913, C901, PLR0911, PLR0912 - explicit denial precedence
    db: Connection,
    *,
    sender: str,
    recipient: str,
    scope: str,
    direction: Direction,
    now: datetime,
) -> AuthorityDecision:
    """Check exact local grant and enrollment; caller owns admission transaction."""
    # pylint: disable=clean-code-commented-out-code,clean-code-redundant-comment
    # pylint: disable=too-many-arguments,too-many-return-statements
    # pylint: disable=too-many-branches,too-many-locals

    def deny(reason: str) -> AuthorityDecision:
        return AuthorityDecision(verified=False, reason=reason)

    try:
        direction.validate()
    except (BusError, AttributeError, TypeError):
        return deny("invalid_direction")
    project = cast(
        "tuple[str, str, str | None] | None",
        db.execute(
            "SELECT generation,coordinator,revoked_at FROM coordination_projects "
            "WHERE scope=? AND project_id=?",
            (scope, direction.project_id),
        ).fetchone(),
    )
    if project is None:
        exists = cast(
            "tuple[int] | None",
            db.execute(
                "SELECT 1 FROM coordination_projects WHERE project_id=?", (direction.project_id,)
            ).fetchone(),
        )
        return deny("wrong_scope" if exists else "unknown_project")
    generation, coordinator, revoked_at = project
    if revoked_at is not None:
        return deny("revoked")
    if generation != direction.generation:
        return deny("stale_generation")
    if coordinator != sender:
        return deny("wrong_coordinator")
    enrolled = cast(
        "tuple[str] | None",
        db.execute(
            "SELECT generation FROM coordination_enrollments WHERE scope=? AND project_id=? "
            "AND address=?",
            (scope, direction.project_id, recipient),
        ).fetchone(),
    )
    if enrolled is None:
        return deny("not_enrolled")
    if enrolled[0] != direction.generation:
        return deny("stale_generation")
    grant = cast(
        "tuple[str, str, str, str, str | None, str | None] | None",
        db.execute(
            "SELECT grant_id,generation,coordinator,operations_json,expires_at,revoked_at "
            "FROM coordination_grants WHERE scope=? AND project_id=? "
            "AND recipient=? AND grant_id=?",
            (scope, direction.project_id, recipient, direction.grant_id),
        ).fetchone(),
    )
    if grant is None:
        return deny("invalid_direction")
    grant_id, generation, coordinator, operations_json, expires_at, revoked_at = grant
    if revoked_at is not None:
        return deny("revoked")
    if generation != direction.generation:
        return deny("stale_generation")
    if coordinator != sender:
        return deny("wrong_coordinator")
    try:
        if expires_at is not None and timestamp(expires_at) <= now:
            return deny("expired")
        operations = cast("object", json.loads(operations_json))
        if not isinstance(operations, list):
            return deny("invalid_direction")
        allowed = cast("list[object]", operations)
        if not all(isinstance(item, str) and item in OPERATIONS for item in allowed):
            return deny("invalid_direction")
        if direction.operation not in allowed:
            return deny("operation_denied")
    except (BusError, ValueError, TypeError):
        return deny("invalid_direction")
    return AuthorityDecision(verified=True, reason="verified", grant_id=grant_id)
