"""Local owner records. Callers serialize mutations with BEGIN IMMEDIATE."""

# ruff: noqa: ISC004 - adjacent SQL fragments form individual table definitions

import json
from sqlite3 import Connection
from typing import cast

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
    return result
