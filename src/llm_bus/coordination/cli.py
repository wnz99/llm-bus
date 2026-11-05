"""Explicit owner setup and exact native session enrollment."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from llm_bus.coordination import store
from llm_bus.coordination.authority import authorize
from llm_bus.coordination.models import OPERATIONS, Direction, timestamp, uuid4_string
from llm_bus.errors import BusError
from llm_bus.routing import routing_scope

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable
    from sqlite3 import Connection

    from llm_bus.mailbox.store import Store

MAX_TEXT = 4096
MAX_REFERENCES = 32
CONTROL_BOUNDARY = 32


def add_parsers(add_parser: Callable[..., argparse.ArgumentParser]) -> None:
    coordinate = add_parser("coordinate", help="Owner enrolls this coordinator")
    coordinate.add_argument("project")
    _references(coordinate, required=False)
    add_parser("join", help="Enroll this granted main session").add_argument("project")
    actions = add_parser("coordination").add_subparsers(dest="action", required=True)
    for name in ("show", "setup", "grant", "revoke", "replace", "leave"):
        action = actions.add_parser(name)
        action.add_argument("project")
        if name == "setup":
            action.add_argument("--project-id", required=True)
            action.add_argument("--generation", required=True)
            action.add_argument("--coordinator", required=True)
            _references(action, required=True)
        if name == "replace":
            action.add_argument("--generation", required=True)
            action.add_argument("--coordinator", required=True)
        if name in {"grant", "revoke"}:
            action.add_argument("--recipient", required=name == "grant")
        if name == "grant":
            lifetime = action.add_mutually_exclusive_group(required=True)
            lifetime.add_argument("--expires-at")
            lifetime.add_argument("--until-revoked", action="store_true")


def _references(parser: argparse.ArgumentParser, *, required: bool) -> None:
    for name in ("objective", "beads-workspace", "memory-ticket"):
        parser.add_argument(f"--{name}", required=required)
    parser.add_argument("--reference", action="append", default=[])


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise BusError("Expected nonempty bounded reference (at most 4096 characters)")
    if any(ord(char) < CONTROL_BOUNDARY for char in value):
        raise BusError("Control characters forbidden in references")
    return value


def _address(value: object) -> str:
    address = _text(value)
    if re.fullmatch(r"(?:[^/:\s]+/[^/:\s]+/)?(?:codex|claude):[^/:\s]+", address) is None:
        raise BusError("Expected exact native or machine/repository qualified address")
    return address


def _path(value: object) -> str:
    path = Path(_text(value))
    if not path.is_absolute() or not path.exists():
        raise BusError("Expected existing absolute local path")
    return str(path.resolve())


def run(mailbox: Store, args: dict[str, object], address: str, cwd: str) -> dict[str, object]:
    """Use app-selected native identity. These commands grant no remote owner power."""
    scope = routing_scope(cwd)
    label = _text(args["project"])
    action = str(args["command"] if args["command"] != "coordination" else args["action"])
    with mailbox.transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        if action == "setup":
            _create(db, scope, label, args, _address(args["coordinator"]))
        elif action == "coordinate":
            exists = cast(
                "tuple[int] | None",
                db.execute(
                    "SELECT 1 FROM coordination_projects WHERE scope=? AND label=?", (scope, label)
                ).fetchone(),
            )
            if not exists:
                _create(db, scope, label, args, address)
        record = store.project(db, scope, label)
        if action == "show":
            return store.show(db, record)
        if action in {"coordinate", "join", "grant"} and record["revoked_at"] is not None:
            raise BusError("Project revoked; owner must replace coordinator binding")
        _transition(db, action, record, args, address)
        return store.show(db, store.project(db, scope, label))


def _create(
    db: Connection, scope: str, label: str, args: dict[str, object], coordinator: str
) -> None:
    references = cast("list[object]", args.get("reference", []))
    if len(references) > MAX_REFERENCES:
        raise BusError("At most 32 local references allowed")
    workspace = _path(args.get("beads_workspace"))
    if not Path(workspace).is_dir() or not (Path(workspace) / ".beads").exists():
        raise BusError("Coordination requires an existing Beads workspace")
    db.execute(
        "INSERT INTO coordination_projects VALUES (?,?,?,?,?,?,?,?,?,NULL)",
        (
            scope,
            uuid4_string(str(args.get("project_id") or uuid4())),
            label,
            uuid4_string(str(args.get("generation") or uuid4())),
            coordinator,
            _text(args.get("objective")),
            workspace,
            _text(args.get("memory_ticket")),
            json.dumps([_path(ref) for ref in references]),
        ),
    )
    record = store.project(db, scope, label)
    db.execute(
        "INSERT INTO coordination_generations VALUES (?,?,?)",
        (scope, record["project_id"], record["generation"]),
    )


def _transition(  # noqa: C901, PLR0912 - bounded CLI action dispatch
    db: Connection,
    action: str,
    record: dict[str, object],
    args: dict[str, object],
    address: str,
) -> None:
    # pylint: disable=too-many-branches
    key = (record["scope"], record["project_id"])
    if action == "coordinate":
        if record["coordinator"] != address:
            raise BusError("Different coordinator; owner must use coordination replace")
        store.enroll(db, record, address, "coordinator")
    elif action == "join":
        grant = cast(
            "tuple[str] | None",
            db.execute(
                "SELECT grant_id FROM coordination_grants WHERE scope=? "
                "AND project_id=? AND recipient=? AND revoked_at IS NULL",
                (*key, address),
            ).fetchone(),
        )
        if grant is None:
            raise BusError("Current session needs exact owner grant")
        # Enrollment and grant verification share the same transaction; denial rolls back.
        store.enroll(db, record, address, "worker")
        decision = authorize(
            db,
            sender=str(record["coordinator"]),
            recipient=address,
            scope=str(record["scope"]),
            now=datetime.now(UTC),
            direction=Direction(
                1, str(record["project_id"]), str(record["generation"]), grant[0], "assign"
            ),
        )
        if not decision.verified:
            raise BusError(f"Cannot join: {decision.reason}")
    elif action == "grant":
        expiry = args.get("expires_at")
        if expiry is None and args.get("until_revoked") is not True:
            raise BusError("Select explicit grant lifetime")
        if expiry is not None and timestamp(str(expiry)) <= datetime.now(UTC):
            raise BusError("Expiry must be in future")
        recipient = _address(args["recipient"])
        _revoke(db, record, recipient)
        db.execute(
            "INSERT INTO coordination_grants VALUES (?,?,?,?,?,?,?,?,NULL)",
            (
                *key,
                recipient,
                str(uuid4()),
                record["generation"],
                record["coordinator"],
                json.dumps(OPERATIONS),
                expiry,
            ),
        )
    elif action == "leave":
        db.execute(
            "DELETE FROM coordination_enrollments WHERE scope=? AND project_id=? AND address=?",
            (*key, address),
        )
    elif action == "revoke":
        _revoke(db, record, args.get("recipient"))
    elif action == "replace":
        generation = uuid4_string(str(args["generation"]))
        if generation == record["generation"]:
            raise BusError("Replacement requires fresh generation UUIDv4")
        coordinator = _address(args["coordinator"])
        db.execute("INSERT INTO coordination_generations VALUES (?,?,?)", (*key, generation))
        _revoke(db, record, None)
        db.execute("DELETE FROM coordination_targets WHERE scope=? AND project_id=?", key)
        db.execute(
            "UPDATE coordination_projects SET generation=?,coordinator=?,revoked_at=NULL "
            "WHERE scope=? AND project_id=?",
            (generation, coordinator, *key),
        )
        if coordinator == address:
            store.enroll(db, {**record, "generation": generation}, address, "coordinator")


def _revoke(db: Connection, record: dict[str, object], recipient: object) -> None:
    key = (record["scope"], record["project_id"])
    clause = " AND recipient=?" if recipient is not None else ""
    values = (*key, _address(recipient)) if recipient is not None else key
    now = datetime.now(UTC).isoformat()
    db.execute(
        # Only fixed internal clauses enter SQL; all owner data remains bound.
        "UPDATE coordination_grants SET revoked_at=? "  # noqa: S608
        "WHERE scope=? AND project_id=?" + clause,
        (now, *values),
    )
    if recipient is None:
        db.execute(
            "UPDATE coordination_projects SET revoked_at=? WHERE scope=? AND project_id=?",
            (now, *key),
        )
    db.execute(
        "DELETE FROM coordination_enrollments WHERE scope=? AND project_id=?"  # noqa: S608
        + (" AND address=?" if recipient is not None else ""),
        values,
    )
