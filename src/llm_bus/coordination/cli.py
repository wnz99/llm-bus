"""Explicit owner setup and exact native session enrollment."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from llm_bus.cli.guidance import guidance
from llm_bus.cli.references import resolve_reference
from llm_bus.coordination import delegation, store
from llm_bus.coordination.availability import record_local, record_remote
from llm_bus.coordination.models import (
    OPERATIONS,
    branch_name,
    ticket_text,
    timestamp,
    uuid4_string,
)
from llm_bus.errors import BusError
from llm_bus.routing import routing_scope
from llm_bus.wake.dispatch import dispatch_pending

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
    coordinate.add_argument("--delegate", action="append", metavar="GATE:BRANCH")
    coordinate.add_argument(
        "--setup",
        action="store_true",
        help="Print the setup profile, or run the confirmation wizard in a terminal",
    )
    join = add_parser("join", help="Owner consents and enrolls this main session")
    join.add_argument("project")
    join.add_argument("--narrow", action="append", metavar="GATE:BRANCH")
    actions = add_parser("coordination").add_subparsers(dest="action", required=True)
    amend = actions.add_parser("amend")
    amend.add_argument("project")
    change = amend.add_mutually_exclusive_group(required=True)
    change.add_argument("--delegate", action="append", metavar="GATE:BRANCH")
    change.add_argument("--no-delegation", action="store_true")
    approve = actions.add_parser("approve")
    approve.add_argument("project")
    approve.add_argument("--recipient", required=True)
    approve.add_argument("--gate", required=True, choices=delegation.GATES)
    approve.add_argument("--ticket", required=True)
    approve.add_argument("--branch", required=True)
    approve.add_argument("--base", required=True)
    approve.add_argument("--pr", type=int)
    approve.add_argument("--head-sha")
    approve.add_argument("--expires-in", default="2h")
    claim = actions.add_parser("claim")
    claim.add_argument("project")
    claim.add_argument("--ticket", required=True)
    claim.add_argument("--branch", required=True)
    for name in ("show", "setup", "grant", "revoke", "replace", "leave", "target"):
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
        if name in {"grant", "revoke", "target"}:
            action.add_argument("--recipient", required=name != "revoke")
        if name == "target":
            action.add_argument("--grant-id", required=True)
        if name == "grant":
            lifetime = action.add_mutually_exclusive_group(required=True)
            lifetime.add_argument("--expires-at")
            lifetime.add_argument("--until-revoked", action="store_true")


def _references(parser: argparse.ArgumentParser, *, required: bool) -> None:
    for name in ("objective", "beads-workspace", "memory-ticket"):
        parser.add_argument(f"--{name}", required=required)
    parser.add_argument("--reference", action="append", default=[])


def text_reference(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise BusError("Expected nonempty bounded reference (at most 4096 characters)")
    if any(ord(char) < CONTROL_BOUNDARY for char in value):
        raise BusError("Control characters forbidden in references")
    return value


def _address(value: object) -> str:
    address = text_reference(value)
    if re.fullmatch(r"(?:[^/:\s]+/[^/:\s]+/)?(?:codex|claude):[^/:\s]+", address) is None:
        raise BusError("Expected exact native or machine/repository qualified address")
    return address


def _path(value: object) -> str:
    path = Path(text_reference(value))
    if not path.is_absolute() or not path.exists():
        raise BusError("Expected existing absolute local path")
    return str(path.resolve())


def beads_workspace(value: object) -> str:
    workspace = _path(value)
    if not Path(workspace).is_dir() or not (Path(workspace) / ".beads").exists():
        raise BusError("Coordination requires an existing Beads workspace")
    return workspace


def run(  # noqa: C901, PLR0912 - owner action dispatch
    mailbox: Store, args: dict[str, object], address: str, cwd: str
) -> dict[str, object]:
    """Use app-selected native identity. These commands grant no remote owner power."""
    # pylint: disable=too-many-branches,too-many-locals
    scope = routing_scope(cwd)
    args = dict(args)
    for field in ("coordinator", "recipient"):
        if args.get(field) is not None:
            args[field] = resolve_reference(mailbox, str(args[field]), cwd)
    label = text_reference(args["project"])
    action = str(args["command"] if args["command"] != "coordination" else args["action"])
    with mailbox.transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        new_project = False
        if action == "setup":
            _create(db, scope, label, args, _address(args["coordinator"]))
        elif action == "coordinate":
            exists = cast(
                "tuple[int] | None",
                db.execute(
                    "SELECT 1 FROM coordination_projects WHERE scope=? AND label=?", (scope, label)
                ).fetchone(),
            )
            new_project = not exists
            if new_project:
                _create(db, scope, label, args, address)
        record = store.project(db, scope, label)
        if action == "show":
            return store.show(db, record)
        if action in {"coordinate", "join", "grant"} and record["revoked_at"] is not None:
            raise BusError("Project revoked; owner must replace coordinator binding")
        if action == "coordinate" and args.get("delegate"):
            _declare_delegation(
                db, record, cast("list[str]", args["delegate"]), address, new=new_project
            )
        remote_notice = None
        if action == "join" and "/" in str(record["coordinator"]):
            grant_id = store.join_session(db, record, address)
            delegation.bind(db, record, grant_id, cast("list[str] | None", args.get("narrow")))
            remote_notice = record_remote(db, mailbox, record, address, grant_id, cwd=cwd)
            notice_id = None
        else:
            notice_id = _transition(db, action, record, args, address)
        result = store.show(db, store.project(db, scope, label))
        if action == "amend":
            result["rejoin_required"] = delegation.rejoin_required(db, record)
        if remote_notice is not None:
            result["availability"] = remote_notice
    if notice_id is not None:
        result["availability_wake"] = dispatch_pending(
            mailbox, str(record["coordinator"]), trigger_id=notice_id
        )
    if action in {"coordinate", "join"}:
        # Reuse hook recovery text; command success must teach the same role duties.
        result["guidance"] = guidance(mailbox, address, cwd, full=True)
    return result


def new_project_label(mailbox: Store, cwd: str, label: object) -> str:
    """Validate a label exactly like coordinate and require that the project is new."""
    label = text_reference(label)
    with mailbox.transaction() as db:
        exists = cast(
            "tuple[int] | None",
            db.execute(
                "SELECT 1 FROM coordination_projects WHERE scope=? AND label=?",
                (routing_scope(cwd), label),
            ).fetchone(),
        )
    if exists:
        raise BusError(
            "Project already exists; run `llm-bus coordinate PROJECT` to resume "
            "or use coordination amend"
        )
    return label


def create_for(  # noqa: PLR0913 - explicit setup inputs
    mailbox: Store,
    cwd: str,
    label: str,
    args: dict[str, object],
    coordinator: str,
    *,
    current: str | None = None,
) -> dict[str, object]:
    """Create a project and its delegation in one transaction without registering a session.

    The coordinator is enrolled only when it is the caller's own identity ``current``;
    otherwise that session must run ``coordinate PROJECT`` itself.
    """
    # pylint: disable=too-many-arguments
    scope = routing_scope(cwd)
    label = new_project_label(mailbox, cwd, label)
    coordinator = _address(coordinator)
    with mailbox.transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        _create(db, scope, label, args, coordinator)
        record = store.project(db, scope, label)
        specs = cast("list[str]", args.get("delegate") or [])
        if specs:
            delegation.set_project_delegation(
                db, record, delegation.Delegation.from_specs(specs), coordinator
            )
        enrolled = current is not None and current == coordinator
        if enrolled:
            store.enroll(db, record, coordinator, "coordinator")
        result = store.show(db, record)
    result["enrolled"] = enrolled
    if not enrolled:
        result["next"] = f"Session {coordinator} must run `llm-bus coordinate {label}`"
    return result


def _declare_delegation(
    db: Connection, record: dict[str, object], specs: list[str], address: str, *, new: bool
) -> None:
    """Set delegation on a new project; a resume may only repeat it, never change it."""
    wanted = delegation.Delegation.from_specs(specs)
    if new:
        delegation.set_project_delegation(db, record, wanted, address)
    elif wanted != delegation.project_delegation(db, record):
        raise BusError("Delegation differs; use coordination amend PROJECT")


def _create(
    db: Connection, scope: str, label: str, args: dict[str, object], coordinator: str
) -> None:
    references = cast("list[object]", args.get("reference", []))
    if len(references) > MAX_REFERENCES:
        raise BusError("At most 32 local references allowed")
    workspace = beads_workspace(args.get("beads_workspace"))
    db.execute(
        "INSERT INTO coordination_projects VALUES (?,?,?,?,?,?,?,?,?,NULL)",
        (
            scope,
            uuid4_string(str(args.get("project_id") or uuid4())),
            label,
            uuid4_string(str(args.get("generation") or uuid4())),
            coordinator,
            text_reference(args.get("objective")),
            workspace,
            text_reference(args.get("memory_ticket")),
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
) -> int | None:
    # pylint: disable=too-many-branches
    key = (record["scope"], record["project_id"])
    if action == "coordinate":
        if record["coordinator"] != address:
            raise BusError("Different coordinator; owner must use coordination replace")
        store.enroll(db, record, address, "coordinator")
    elif action == "join":
        grant_id = store.join_session(db, record, address)
        delegation.bind(db, record, grant_id, cast("list[str] | None", args.get("narrow")))
        if "/" not in str(record["coordinator"]):
            db.execute(
                "INSERT INTO coordination_targets(scope,project_id,recipient,grant_id,generation) "
                "VALUES (?,?,?,?,?) ON CONFLICT(scope,project_id,recipient) DO UPDATE SET "
                "grant_id=excluded.grant_id,generation=excluded.generation",
                (*key, address, grant_id, record["generation"]),
            )
            return record_local(db, record, address, grant_id)
    elif action == "amend":
        enrolled = cast(
            "tuple[int] | None",
            db.execute(
                "SELECT 1 FROM coordination_enrollments "
                "WHERE scope=? AND project_id=? AND address=? AND role='coordinator'",
                (*key, address),
            ).fetchone(),
        )
        if record["coordinator"] != address or enrolled is None:
            raise BusError("Only the enrolled coordinator may amend delegation")
        specs = cast("list[str] | None", args.get("delegate"))
        wanted = (
            None if args.get("no_delegation") else delegation.Delegation.from_specs(specs or [])
        )
        delegation.set_project_delegation(db, record, wanted, address)
    elif action == "claim":
        _claim(db, record, args, address)
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
    elif action == "target":
        db.execute(
            "INSERT INTO coordination_targets VALUES (?,?,?,?,?) "
            "ON CONFLICT(scope,project_id,recipient) DO UPDATE SET "
            "grant_id=excluded.grant_id,generation=excluded.generation",
            (
                *key,
                _address(args["recipient"]),
                uuid4_string(str(args["grant_id"])),
                record["generation"],
            ),
        )
    elif action == "leave":
        if (
            db.execute(
                "SELECT 1 FROM coordination_enrollments "
                "WHERE scope=? AND project_id=? AND address=?",
                (*key, address),
            ).fetchone()
            is not None
        ):
            store.end_session_consents(db, address)
        db.execute(
            "DELETE FROM coordination_enrollments WHERE scope=? AND project_id=? AND address=?",
            (*key, address),
        )
        db.execute(
            "DELETE FROM coordination_claims WHERE address=? AND scope=? AND project_id=?",
            (address, *key),
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
    return None


def _claim(
    db: Connection, record: dict[str, object], args: dict[str, object], address: str
) -> None:
    """Enrolled worker claims one ticket and branch; no other current worker may hold either."""
    key = (record["scope"], record["project_id"])
    ticket = ticket_text(args["ticket"])
    branch = branch_name(args["branch"])
    enrolled = cast(
        "tuple[int] | None",
        db.execute(
            "SELECT 1 FROM coordination_enrollments WHERE scope=? AND project_id=? AND address=? "
            "AND role='worker' AND generation=?",
            (*key, address, record["generation"]),
        ).fetchone(),
    )
    if enrolled is None:
        raise BusError("Only an enrolled worker may claim a ticket")
    holder = cast(
        "tuple[str] | None",
        db.execute(
            "SELECT c.address FROM coordination_claims c JOIN coordination_enrollments e "
            "ON e.scope=c.scope AND e.project_id=c.project_id AND e.address=c.address "
            "WHERE c.scope=? AND c.project_id=? AND c.address<>? AND e.role='worker' "
            "AND e.generation=? AND (c.ticket=? OR c.branch=?)",
            (*key, address, record["generation"], ticket, branch),
        ).fetchone(),
    )
    if holder is not None:
        raise BusError(f"Ticket or branch already claimed by {holder[0]}")
    db.execute(
        "INSERT OR REPLACE INTO coordination_claims VALUES (?,?,?,?,?,?)",
        (address, *key, ticket, branch, datetime.now(UTC).isoformat()),
    )


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
    db.execute(
        "UPDATE coordination_session_consents SET ended_at=? WHERE ended_at IS NULL "
        "AND grant_id IN (SELECT grant_id FROM coordination_grants WHERE revoked_at IS NOT NULL)",
        (now,),
    )
    db.execute(
        "DELETE FROM coordination_targets WHERE grant_id IN "
        "(SELECT grant_id FROM coordination_grants WHERE revoked_at IS NOT NULL)"
    )
    if recipient is None:
        db.execute(
            "UPDATE coordination_projects SET revoked_at=? WHERE scope=? AND project_id=?",
            (now, *key),
        )
    db.execute(
        "DELETE FROM coordination_claims WHERE scope=? AND project_id=?"  # noqa: S608
        + (" AND address=?" if recipient is not None else ""),
        values,
    )
    db.execute(
        "DELETE FROM coordination_enrollments WHERE scope=? AND project_id=?"  # noqa: S608
        + (" AND address=?" if recipient is not None else ""),
        values,
    )
