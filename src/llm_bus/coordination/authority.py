"""One receiver-side predicate for all direction exposure consumers."""

import json
from datetime import datetime
from sqlite3 import Connection
from typing import cast

from llm_bus.coordination import delegation
from llm_bus.coordination.models import (
    GATE_OPERATIONS,
    OPERATIONS,
    Approval,
    AuthorityDecision,
    Direction,
    timestamp,
)
from llm_bus.errors import BusError
from llm_bus.mailbox.agents import expire_idle_agents


def authorize(  # noqa: PLR0913, C901, PLR0911, PLR0912 - explicit denial precedence
    db: Connection,
    *,
    sender: str,
    recipient: str,
    scope: str,
    direction: Direction,
    now: datetime,
    approval: Approval | None = None,
) -> AuthorityDecision:
    """Check exact local grant and enrollment; caller owns admission transaction."""
    # pylint: disable=clean-code-commented-out-code,clean-code-redundant-comment
    # pylint: disable=too-many-arguments,too-many-return-statements
    # pylint: disable=too-many-branches,too-many-locals

    def deny(reason: str) -> AuthorityDecision:
        return AuthorityDecision(verified=False, reason=reason)

    expire_idle_agents(db)
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
    consent = cast(
        "tuple[str, str | None, str | None, str | None] | None",
        db.execute(
            "SELECT c.address,c.ended_at,a.scope,a.ended_at FROM coordination_session_consents c "
            "LEFT JOIN agents a ON a.address=c.address WHERE c.grant_id=?",
            (direction.grant_id,),
        ).fetchone(),
    )
    if consent is not None:
        owner, ended, current_scope, session_ended = consent
        if (
            owner != recipient
            or ended is not None
            or current_scope != scope
            or session_ended is not None
        ):
            return deny("revoked")
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
        if direction.operation in GATE_OPERATIONS:
            return _gate_decision(
                db,
                {"scope": scope, "project_id": direction.project_id, "generation": generation},
                coordinator=sender,
                recipient=recipient,
                direction=direction,
                approval=approval,
                now=now,
            )
        if direction.operation not in allowed:
            return deny("operation_denied")
    except (BusError, ValueError, TypeError):
        return deny("invalid_direction")
    return AuthorityDecision(verified=True, reason="verified", grant_id=grant_id)


def _gate_decision(  # noqa: PLR0911, PLR0913 - explicit denial precedence
    db: Connection,
    record: dict[str, object],
    *,
    coordinator: str,
    recipient: str,
    direction: Direction,
    approval: Approval | None,
    now: datetime,
) -> AuthorityDecision:
    """Gate operations rest on delegation, target limits, expiry and the worker's own claim."""
    # pylint: disable=clean-code-commented-out-code,clean-code-redundant-comment
    # pylint: disable=too-many-return-statements,too-many-arguments

    def deny(reason: str) -> AuthorityDecision:
        return AuthorityDecision(verified=False, reason=reason)

    try:
        if approval is None:
            return deny("invalid_direction")
        approval.validate(direction.operation)
    except BusError:
        return deny("invalid_direction")
    if "/" in coordinator:
        return deny("remote_gate_unsupported")
    gate = GATE_OPERATIONS[direction.operation]
    project = delegation.project_delegation(db, record)
    granted = delegation.effective(db, record, direction.grant_id, now=now)
    if project is None or granted is None or not granted.targets(gate):
        return deny("gate_not_delegated")
    if approval.base not in granted.targets(gate):
        return deny("target_denied")
    if approval.branch == approval.base or approval.branch in project.all_targets():
        return deny("target_denied")
    if timestamp(approval.expires_at) <= now:
        return deny("expired")
    claim = cast(
        "tuple[int] | None",
        db.execute(
            "SELECT 1 FROM coordination_claims WHERE address=? AND scope=? AND project_id=? "
            "AND ticket=? AND branch=?",
            (recipient, record["scope"], record["project_id"], approval.ticket, approval.branch),
        ).fetchone(),
    )
    if claim is None:
        return deny("wrong_ticket")
    return AuthorityDecision(verified=True, reason="verified", grant_id=direction.grant_id)
