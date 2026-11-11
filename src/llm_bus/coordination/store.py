"""Local owner records. Callers serialize mutations with BEGIN IMMEDIATE."""

# ruff: noqa: ISC004 - adjacent SQL fragments form individual table definitions
# Lifecycle ends consent here; join invokes lifecycle and authority lazily.
# pylint: disable=cyclic-import,clean-code-commented-out-code

import json
from datetime import UTC, datetime
from sqlite3 import Connection
from typing import cast
from uuid import uuid4

from llm_bus.coordination import delegation
from llm_bus.errors import BusError


def create_tables(db: Connection) -> None:
    for statement in (
        "CREATE TABLE IF NOT EXISTS coordination_projects (scope TEXT NOT NULL, "
        "project_id TEXT NOT NULL, label TEXT NOT NULL, generation TEXT NOT NULL, "
        "coordinator TEXT NOT NULL, objective TEXT NOT NULL, beads_workspace TEXT NOT NULL, "
        "memory_ticket TEXT NOT NULL, references_json TEXT NOT NULL, revoked_at TEXT, "
        "PRIMARY KEY(scope,project_id), UNIQUE(scope,label))",
        "CREATE TABLE IF NOT EXISTS coordination_grants (scope TEXT NOT NULL, "
        "project_id TEXT NOT NULL, recipient TEXT NOT NULL, grant_id TEXT NOT NULL UNIQUE, "
        "generation TEXT NOT NULL, coordinator TEXT NOT NULL, operations_json TEXT NOT NULL, "
        "expires_at TEXT, revoked_at TEXT, PRIMARY KEY(grant_id))",
        "CREATE TABLE IF NOT EXISTS coordination_generations (scope TEXT NOT NULL, "
        "project_id TEXT NOT NULL, generation TEXT NOT NULL UNIQUE, "
        "PRIMARY KEY(scope,project_id,generation))",
        "CREATE TABLE IF NOT EXISTS coordination_enrollments (scope TEXT NOT NULL, "
        "project_id TEXT NOT NULL, address TEXT NOT NULL UNIQUE, role TEXT NOT NULL "
        "CHECK(role IN ('coordinator','worker')), generation TEXT NOT NULL, "
        "PRIMARY KEY(scope,project_id,address))",
        "CREATE TABLE IF NOT EXISTS coordination_messages "
        "(message_id INTEGER PRIMARY KEY REFERENCES messages(id), metadata_json TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS coordination_targets (scope TEXT NOT NULL, "
        "project_id TEXT NOT NULL, recipient TEXT NOT NULL, grant_id TEXT NOT NULL, "
        "generation TEXT NOT NULL, PRIMARY KEY(scope,project_id,recipient))",
        "CREATE TABLE IF NOT EXISTS coordination_session_consents (grant_id TEXT PRIMARY KEY "
        "REFERENCES coordination_grants(grant_id), address TEXT NOT NULL, ended_at TEXT)",
        "CREATE TABLE IF NOT EXISTS coordination_local_availability (grant_id TEXT PRIMARY KEY "
        "REFERENCES coordination_grants(grant_id), message_id INTEGER NOT NULL UNIQUE "
        "REFERENCES messages(id))",
        "CREATE TABLE IF NOT EXISTS coordination_availability_events "
        "(message_id INTEGER PRIMARY KEY REFERENCES messages(id), grant_id TEXT NOT NULL, "
        "sender TEXT NOT NULL, sent_at TEXT NOT NULL, event_id TEXT NOT NULL, "
        "UNIQUE(sender,grant_id))",
    ):
        db.execute(statement)


def create_delegation_tables(db: Connection) -> None:
    """Additive schema 13 storage for delegation, claims, gates, amendments and decisions."""
    for statement in (
        "CREATE TABLE IF NOT EXISTS coordination_delegations (scope TEXT NOT NULL, "
        "project_id TEXT NOT NULL, generation TEXT NOT NULL, delegation_json TEXT NOT NULL, "
        "updated_by TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "PRIMARY KEY(scope,project_id,generation))",
        "CREATE TABLE IF NOT EXISTS coordination_grant_delegations (grant_id TEXT PRIMARY KEY "
        "REFERENCES coordination_grants(grant_id), delegation_json TEXT NOT NULL, "
        "bound_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS coordination_claims (address TEXT PRIMARY KEY, "
        "scope TEXT NOT NULL, project_id TEXT NOT NULL, ticket TEXT NOT NULL, "
        "branch TEXT NOT NULL, claimed_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS coordination_approvals (message_id INTEGER PRIMARY KEY "
        "REFERENCES messages(id), approval_json TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS coordination_gate_records (record_id TEXT PRIMARY KEY, "
        "scope TEXT NOT NULL, project_id TEXT NOT NULL, generation TEXT NOT NULL, "
        "grant_id TEXT NOT NULL REFERENCES coordination_grants(grant_id), "
        "ticket TEXT NOT NULL, branch TEXT NOT NULL, base TEXT NOT NULL, "
        "pr INTEGER NOT NULL, head_sha TEXT NOT NULL, "
        "verdict TEXT NOT NULL CHECK(verdict='approved'), evidence_json TEXT NOT NULL, "
        "checks_json TEXT NOT NULL, submitted_by TEXT NOT NULL, submitted_at TEXT NOT NULL, "
        "status TEXT NOT NULL CHECK(status IN ('submitted','accepted','rejected','superseded')), "
        "decided_by TEXT, decided_at TEXT, expires_at TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS coordination_gate_records_pr "
        "ON coordination_gate_records(scope,project_id,generation,pr)",
        "CREATE TABLE IF NOT EXISTS coordination_amendments (amendment_id TEXT PRIMARY KEY, "
        "scope TEXT NOT NULL, project_id TEXT NOT NULL, generation TEXT NOT NULL, "
        "text TEXT NOT NULL, sha256 TEXT NOT NULL, decision_id TEXT, "
        "recorded_by TEXT NOT NULL, recorded_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS coordination_instructions (instruction_id TEXT PRIMARY KEY, "
        "scope TEXT NOT NULL, project_id TEXT NOT NULL, "
        "audience TEXT NOT NULL CHECK(audience IN ('coordinator','worker')), "
        "kind TEXT NOT NULL CHECK(kind IN ('set','amend')), text TEXT NOT NULL, "
        "sha256 TEXT NOT NULL, recorded_by TEXT NOT NULL, recorded_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS coordination_owner_turns (address TEXT PRIMARY KEY, "
        "turn INTEGER NOT NULL, at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS coordination_decisions (decision_id TEXT PRIMARY KEY, "
        "scope TEXT NOT NULL, project_id TEXT NOT NULL, generation TEXT NOT NULL, "
        "class TEXT NOT NULL CHECK(class IN "
        "('scope','security','cost','data','irreversible','objective')), "
        "ticket TEXT, worker TEXT, question TEXT NOT NULL, recommended TEXT, "
        "status TEXT NOT NULL CHECK(status IN ('pending','answered','defaulted')), "
        "answer TEXT, answer_sha256 TEXT, source_session TEXT, source_turn INTEGER, "
        "created_at TEXT NOT NULL, deadline_at TEXT, resolved_at TEXT)",
    ):
        db.execute(statement)


def project(db: Connection, scope: str, label: str) -> dict[str, object]:
    cursor = db.execute(
        "SELECT * FROM coordination_projects WHERE scope=? AND label=?", (scope, label)
    )
    row = cast("tuple[object, ...] | None", cursor.fetchone())
    if row is None:
        raise BusError("Unknown coordination project")
    return dict(zip((column[0] for column in cursor.description), row, strict=True))


def enroll(db: Connection, record: dict[str, object], address: str, role: str) -> None:
    existing = cast(
        "tuple[str, str] | None",
        db.execute(
            "SELECT scope,project_id FROM coordination_enrollments WHERE address=?", (address,)
        ).fetchone(),
    )
    if existing and existing != (record["scope"], record["project_id"]):
        raise BusError("Leave current project before enrolling elsewhere")
    db.execute(
        "INSERT OR REPLACE INTO coordination_enrollments VALUES (?,?,?,?,?)",
        (record["scope"], record["project_id"], address, role, record["generation"]),
    )


def end_session_consents(db: Connection, address: str) -> None:
    """End automatic consent without altering explicit owner grant lifetimes."""
    now = datetime.now(UTC).isoformat()
    db.execute(
        "UPDATE coordination_grants SET revoked_at=? WHERE grant_id IN "
        "(SELECT grant_id FROM coordination_session_consents WHERE address=? AND ended_at IS NULL)",
        (now, address),
    )
    db.execute(
        "DELETE FROM coordination_targets WHERE grant_id IN "
        "(SELECT grant_id FROM coordination_session_consents WHERE address=? AND ended_at IS NULL)",
        (address,),
    )
    db.execute(
        "DELETE FROM coordination_enrollments WHERE address=? AND EXISTS "
        "(SELECT 1 FROM coordination_session_consents WHERE address=? AND ended_at IS NULL)",
        (address, address),
    )
    db.execute(
        "UPDATE coordination_session_consents SET ended_at=? WHERE address=? AND ended_at IS NULL",
        (now, address),
    )


def join_session(db: Connection, record: dict[str, object], address: str) -> str:
    """Record an owner-directed join inside the caller's immediate transaction."""
    # Deferred imports keep the lifecycle owner usable during mailbox import.
    # pylint: disable=import-outside-toplevel,clean-code-redundant-comment
    from llm_bus.coordination.authority import authorize  # noqa: PLC0415
    from llm_bus.coordination.models import OPERATIONS, Direction  # noqa: PLC0415
    from llm_bus.mailbox.agents import expire_idle_agents  # noqa: PLC0415

    expire_idle_agents(db)
    if (
        db.execute(
            "SELECT 1 FROM agents WHERE address=? AND scope=? AND ended_at IS NULL",
            (address, record["scope"]),
        ).fetchone()
        is None
    ):
        raise BusError("Join requires live native session in current scope")
    if record["revoked_at"] is not None:
        raise BusError("Project revoked; owner must replace coordinator binding")
    enroll(db, record, address, "worker")
    grant = cast(
        "tuple[str] | None",
        db.execute(
            "SELECT g.grant_id FROM coordination_grants g "
            "LEFT JOIN coordination_session_consents c ON c.grant_id=g.grant_id "
            "WHERE g.scope=? AND g.project_id=? AND g.recipient=? AND g.generation=? "
            "AND g.revoked_at IS NULL AND c.ended_at IS NULL ORDER BY g.rowid DESC LIMIT 1",
            (record["scope"], record["project_id"], address, record["generation"]),
        ).fetchone(),
    )
    grant_id = grant[0] if grant else str(uuid4())
    if grant is None:
        db.execute(
            "INSERT INTO coordination_grants "
            "(scope,project_id,recipient,grant_id,generation,coordinator,operations_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                record["scope"],
                record["project_id"],
                address,
                grant_id,
                record["generation"],
                record["coordinator"],
                json.dumps(OPERATIONS),
            ),
        )
        db.execute(
            "INSERT INTO coordination_session_consents(grant_id,address) VALUES (?,?)",
            (grant_id, address),
        )
    decision = authorize(
        db,
        sender=str(record["coordinator"]),
        recipient=address,
        scope=str(record["scope"]),
        direction=Direction(
            1, str(record["project_id"]), str(record["generation"]), grant_id, "assign"
        ),
        now=datetime.now(UTC),
    )
    if not decision.verified:
        raise BusError(f"Cannot join: {decision.reason}")
    return grant_id


def show(db: Connection, record: dict[str, object]) -> dict[str, object]:
    result = dict(record)
    result["references"] = json.loads(str(result.pop("references_json")))
    for table in ("grants", "enrollments", "targets"):
        cursor = db.execute(
            # pylint: disable=clean-code-commented-out-code
            f"SELECT * FROM coordination_{table} "  # noqa: S608
            "WHERE scope=? AND project_id=?",
            (record["scope"], record["project_id"]),
        )
        result[table] = [
            dict(zip((c[0] for c in cursor.description), row, strict=True))
            for row in cast("list[tuple[object, ...]]", cursor.fetchall())
        ]
    current = delegation.project_delegation(db, record)
    if current is not None:
        result["delegation"] = current.wire()
        result["bindings"] = delegation.bindings(db, record)
    cursor = db.execute(
        "SELECT record_id,ticket,branch,base,pr,head_sha,evidence_json,checks_json,status,"
        "submitted_by,submitted_at,decided_by,expires_at "
        "FROM coordination_gate_records WHERE scope=? AND project_id=? AND generation=? "
        "ORDER BY rowid",
        (record["scope"], record["project_id"], record["generation"]),
    )
    gates = [
        dict(zip((c[0] for c in cursor.description), row, strict=True))
        for row in cast("list[tuple[object, ...]]", cursor.fetchall())
    ]
    for gate in gates:
        gate["review_evidence"] = json.loads(str(gate.pop("evidence_json")))
        gate["required_checks"] = json.loads(str(gate.pop("checks_json")))
    if gates:
        result["gate_records"] = gates
    return result
