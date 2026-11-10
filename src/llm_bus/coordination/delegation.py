"""Per-generation project delegation and per-grant worker bindings."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from llm_bus.coordination.models import GATE_OPERATIONS
from llm_bus.errors import BusError

if TYPE_CHECKING:
    from sqlite3 import Connection

GATES = tuple(GATE_OPERATIONS.values())
FLOOR = (
    "force-push",
    "deploys or production changes",
    "credential or secret changes",
    "paid or live runs",
    "merge with failing or pending CI",
)
BRANCH = re.compile(r"(?!-)(?!.*\.\.)[A-Za-z0-9._/-]{1,200}")
MAX_TARGETS = 8
VERSION = 1
SPEC_PARTS = 2


def _words(text: str) -> str:
    return re.sub(r"[\s_-]+", " ", text.strip().lower())


def _reject_floor(spec: str) -> None:
    normalized = _words(spec)
    for entry in FLOOR:
        if normalized.startswith(_words(entry)):
            raise BusError(f"Delegation never covers the floor action: {entry}")


def _branch(value: object) -> str:
    if not isinstance(value, str) or BRANCH.fullmatch(value) is None:
        raise BusError("Invalid delegation branch name")
    return value


@dataclass(frozen=True)
class Delegation:
    """Gate to explicit target branches; never empty per gate, canonical order."""

    gates: tuple[tuple[str, tuple[str, ...]], ...]

    @classmethod
    def _build(cls, mapping: dict[str, set[str]]) -> Delegation:
        return cls(tuple((gate, tuple(sorted(mapping[gate]))) for gate in sorted(mapping)))

    def targets(self, gate: str) -> tuple[str, ...]:
        return dict(self.gates).get(gate, ())

    def all_targets(self) -> frozenset[str]:
        return frozenset(branch for _gate, branches in self.gates for branch in branches)

    def intersect(self, other: Delegation) -> Delegation:
        common = {gate: set(branches) & set(other.targets(gate)) for gate, branches in self.gates}
        return self._build({gate: branches for gate, branches in common.items() if branches})

    def covers(self, other: Delegation) -> bool:
        return all(set(branches) <= set(self.targets(gate)) for gate, branches in other.gates)

    def items(self) -> tuple[str, ...]:
        return tuple(f"{gate}:{branch}" for gate, branches in self.gates for branch in branches)

    def wire(self) -> dict[str, object]:
        return {
            "version": VERSION,
            "gates": {gate: list(branches) for gate, branches in self.gates},
        }

    @classmethod
    def parse(cls, value: object) -> Delegation:
        if not isinstance(value, dict):
            raise BusError("Invalid delegation fields")
        fields = cast("dict[str, object]", value)
        if set(fields) != {"version", "gates"}:
            raise BusError("Invalid delegation fields")
        # bool subclasses int but is not a valid protocol version.
        # pylint: disable=unidiomatic-typecheck
        gates = fields["gates"]
        if type(fields["version"]) is not int or fields["version"] != VERSION:
            raise BusError("Invalid delegation version")
        if not isinstance(gates, dict) or not gates:
            raise BusError("Invalid delegation gates")
        mapping: dict[str, set[str]] = {}
        for gate, branches in cast("dict[str, object]", gates).items():
            if gate not in GATES or not isinstance(branches, list):
                raise BusError("Invalid delegation gate")
            listed = cast("list[object]", branches)
            if not 0 < len(listed) <= MAX_TARGETS:
                raise BusError("Invalid delegation gate")
            mapping[gate] = {_branch(branch) for branch in listed}
        return cls._build(mapping)

    @classmethod
    def load(cls, text: str) -> Delegation:
        return cls.parse(cast("object", json.loads(text)))

    @classmethod
    def from_specs(cls, specs: list[str]) -> Delegation:
        mapping: dict[str, set[str]] = {}
        for spec in specs:
            _reject_floor(spec)
            parts = spec.split(":", 1)
            if len(parts) != SPEC_PARTS or parts[0] not in GATES:
                raise BusError(f"Expected GATE:BRANCH with gate in {', '.join(GATES)}: {spec!r}")
            mapping.setdefault(parts[0], set()).add(_branch(parts[1]))
        if not mapping:
            raise BusError("Delegation needs at least one GATE:BRANCH")
        if any(len(branches) > MAX_TARGETS for branches in mapping.values()):
            raise BusError(f"At most {MAX_TARGETS} target branches per gate")
        return cls._build(mapping)

    def narrow(self, items: list[str]) -> Delegation:
        outside = [item for item in items if item not in self.items()]
        if outside:
            raise BusError(f"Not delegated by the project: {', '.join(outside)}")
        return Delegation.from_specs(items)


def project_delegation(db: Connection, record: dict[str, object]) -> Delegation | None:
    row = cast(
        "tuple[str] | None",
        db.execute(
            "SELECT delegation_json FROM coordination_delegations "
            "WHERE scope=? AND project_id=? AND generation=?",
            (record["scope"], record["project_id"], record["generation"]),
        ).fetchone(),
    )
    return None if row is None else Delegation.load(row[0])


def set_project_delegation(
    db: Connection, record: dict[str, object], delegation: Delegation | None, by: str
) -> None:
    """Replace the generation's delegation; bindings never outlive the project set."""
    key = (record["scope"], record["project_id"], record["generation"])
    if delegation is None:
        db.execute(
            "DELETE FROM coordination_delegations WHERE scope=? AND project_id=? AND generation=?",
            key,
        )
    else:
        db.execute(
            "INSERT OR REPLACE INTO coordination_delegations VALUES (?,?,?,?,?,?)",
            (*key, json.dumps(delegation.wire()), by, datetime.now(UTC).isoformat()),
        )
    # A later widening must need a rejoin, so stale binding items are dropped now.
    for grant_id, text in cast(
        "list[tuple[str, str]]",
        db.execute(
            "SELECT d.grant_id,d.delegation_json FROM coordination_grant_delegations d "
            "JOIN coordination_grants g ON g.grant_id=d.grant_id "
            "WHERE g.scope=? AND g.project_id=? AND g.generation=?",
            key,
        ).fetchall(),
    ):
        binding = Delegation.load(text)
        kept = None if delegation is None else binding.intersect(delegation)
        if kept is None or not kept.gates:
            db.execute("DELETE FROM coordination_grant_delegations WHERE grant_id=?", (grant_id,))
        elif kept != binding:
            db.execute(
                "UPDATE coordination_grant_delegations SET delegation_json=? WHERE grant_id=?",
                (json.dumps(kept.wire()), grant_id),
            )


def _binding(db: Connection, grant_id: str) -> Delegation | None:
    row = cast(
        "tuple[str] | None",
        db.execute(
            "SELECT delegation_json FROM coordination_grant_delegations WHERE grant_id=?",
            (grant_id,),
        ).fetchone(),
    )
    return None if row is None else Delegation.load(row[0])


def bind(
    db: Connection, record: dict[str, object], grant_id: str, narrow: list[str] | None
) -> Delegation | None:
    """Replace the grant's binding; a rejoin without --narrow accepts the full project set."""
    project = project_delegation(db, record)
    if project is None:
        if narrow:
            raise BusError("--narrow requires a project delegation")
        return None
    binding = project.narrow(narrow) if narrow else project
    db.execute(
        "INSERT OR REPLACE INTO coordination_grant_delegations VALUES (?,?,?)",
        (grant_id, json.dumps(binding.wire()), datetime.now(UTC).isoformat()),
    )
    return binding


def effective(db: Connection, record: dict[str, object], grant_id: str) -> Delegation | None:
    """Project delegation intersected with the worker's binding; None means no authority."""
    project = project_delegation(db, record)
    binding = _binding(db, grant_id)
    if project is None or binding is None:
        return None
    return project.intersect(binding)


def bindings(db: Connection, record: dict[str, object]) -> dict[str, list[str]]:
    rows = cast(
        "list[tuple[str, str]]",
        db.execute(
            "SELECT d.grant_id,d.delegation_json FROM coordination_grant_delegations d "
            "JOIN coordination_grants g ON g.grant_id=d.grant_id "
            "WHERE g.scope=? AND g.project_id=? AND g.generation=? ORDER BY g.rowid",
            (record["scope"], record["project_id"], record["generation"]),
        ).fetchall(),
    )
    return {grant_id: list(Delegation.load(text).items()) for grant_id, text in rows}


def rejoin_required(db: Connection, record: dict[str, object]) -> list[str]:
    """Enrolled workers whose current binding lacks project items; they must rejoin."""
    project = project_delegation(db, record)
    if project is None:
        return []
    rows = cast(
        "list[tuple[str, str | None]]",
        db.execute(
            "SELECT e.address,d.delegation_json FROM coordination_enrollments e "
            "LEFT JOIN coordination_grants g ON g.rowid=(SELECT MAX(rowid) "
            "FROM coordination_grants WHERE scope=e.scope AND project_id=e.project_id "
            "AND recipient=e.address AND generation=? AND revoked_at IS NULL) "
            "LEFT JOIN coordination_grant_delegations d ON d.grant_id=g.grant_id "
            "WHERE e.scope=? AND e.project_id=? AND e.role='worker' ORDER BY e.address",
            (record["generation"], record["scope"], record["project_id"]),
        ).fetchall(),
    )
    return [
        address
        for address, text in rows
        if text is None or not Delegation.load(text).covers(project)
    ]
