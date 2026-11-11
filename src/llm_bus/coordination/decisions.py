"""Owner decision records: the coordinator stores the owner's answer, bound workers accept it.

The bus stores each answer verbatim with its session, turn, time and SHA-256. A record grants
nothing by itself: a worker acts on a decision only after ``status`` reports ``accepted``.
"""

from __future__ import annotations

import hashlib
import unicodedata
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from llm_bus.coordination import amendments, delegation, store
from llm_bus.coordination.models import ticket_text
from llm_bus.errors import BusError

if TYPE_CHECKING:
    from sqlite3 import Connection

    from llm_bus.mailbox.models import Message
    from llm_bus.mailbox.store import Store

CLASSES = ("scope", "security", "cost", "data", "irreversible", "objective")
OBJECTIVE = "objective"
PENDING = "pending"
ANSWERED = "answered"
NOT_PENDING = "Decision not answered: not_pending"
MAX_TEXT = 4096
DELIMITERS = ("<<<", ">>>")
COLUMNS = (
    "decision_id,class,ticket,worker,question,recommended,status,answer,answer_sha256,"
    "source_session,source_turn,created_at,deadline_at,resolved_at"
)
Result = tuple[dict[str, object], "list[Message]"]


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _text(value: object, name: str) -> str:
    """Bounded printable text that can safely be shown verbatim between delimiters."""
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise BusError(f"Decision {name} must be 1 to {MAX_TEXT} characters")
    if any(unicodedata.category(char) in {"Cc", "Cf"} for char in value):
        raise BusError(f"Decision {name} must not contain control or format characters")
    if any(mark in value for mark in DELIMITERS):
        raise BusError(f"Decision {name} must not contain '<<<' or '>>>'")
    return value


def _require_coordinator(db: Connection, record: dict[str, object], caller: str) -> None:
    if not store.coordinator_enrolled(db, record, caller):
        raise BusError("Only the enrolled coordinator may record or list decisions")


def record_turn(mailbox: Store, address: str) -> None:
    """Count one prompt turn for an enrolled coordinator; any other address writes nothing."""
    with mailbox.transaction() as db:
        enrolled = cast(
            "tuple[int] | None",
            db.execute(
                "SELECT 1 FROM coordination_enrollments WHERE address=? AND role='coordinator'",
                (address,),
            ).fetchone(),
        )
        if enrolled is None:
            return
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT INTO coordination_owner_turns VALUES (?,1,?) "
            "ON CONFLICT(address) DO UPDATE SET turn=turn+1,at=excluded.at",
            (address, _stamp(datetime.now(UTC))),
        )


def _source_turn(db: Connection, address: str) -> int | None:
    row = cast(
        "tuple[int] | None",
        db.execute(
            "SELECT turn FROM coordination_owner_turns WHERE address=?", (address,)
        ).fetchone(),
    )
    return None if row is None else row[0]


def _row(db: Connection, decision_id: str) -> dict[str, object] | None:
    cursor = db.execute(
        f"SELECT {COLUMNS},scope,project_id,generation "  # noqa: S608 - fixed column list
        "FROM coordination_decisions WHERE decision_id=?",
        (decision_id,),
    )
    found = cast("tuple[object, ...] | None", cursor.fetchone())
    if found is None:
        return None
    return dict(zip((column[0] for column in cursor.description), found, strict=True))


def _current(
    db: Connection, record: dict[str, object], decision_id: str, verb: str
) -> dict[str, object]:
    """A decision of this project's current generation, else a refusal."""
    row = _row(db, decision_id)
    if row is None or (row["scope"], row["project_id"]) != (record["scope"], record["project_id"]):
        raise BusError(f"Decision not {verb}: no_decision")
    if row["generation"] != record["generation"]:
        raise BusError(f"Decision not {verb}: stale_generation")
    return row


def _enrolled_worker(db: Connection, record: dict[str, object], address: str) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM coordination_enrollments WHERE scope=? AND project_id=? "
            "AND address=? AND role='worker' AND generation=?",
            (record["scope"], record["project_id"], address, record["generation"]),
        ).fetchone()
        is not None
    )


def _resolve(db: Connection, decision_id: str, text: str, caller: str, now: datetime) -> None:
    db.execute(
        "UPDATE coordination_decisions SET status=?,answer=?,answer_sha256=?,"
        "source_session=?,source_turn=?,resolved_at=? WHERE decision_id=?",
        (
            ANSWERED,
            text,
            hashlib.sha256(text.encode()).hexdigest(),
            caller,
            _source_turn(db, caller),
            _stamp(now),
            decision_id,
        ),
    )


def _public(row: dict[str, object]) -> dict[str, object]:
    keys = COLUMNS.split(",")
    return {key: row[key] for key in keys}


def ask(  # noqa: PLR0913 - explicit decision fields
    db: Connection,
    record: dict[str, object],
    caller: str,
    *,
    decision_class: str,
    question: str,
    recommended: str | None,
    ticket: str | None,
    worker: str | None,
    answer: str | None,
    now: datetime,
) -> Result:
    """Record a question; with ``answer`` it is stored answered through the same path."""
    # pylint: disable=too-many-arguments,redefined-outer-name
    _require_coordinator(db, record, caller)
    if decision_class not in CLASSES:
        raise BusError(f"Decision class must be one of: {', '.join(CLASSES)}")
    question = _text(question, "question")
    recommended = None if recommended is None else _text(recommended, "recommended option")
    ticket = None if ticket is None else ticket_text(ticket)
    if worker is not None and not _enrolled_worker(db, record, worker):
        raise BusError("Decision worker must be an enrolled worker of this project")
    if answer is not None:
        answer = _text(answer, "answer")
    decision_id = uuid4().hex
    db.execute(
        "INSERT INTO coordination_decisions(decision_id,scope,project_id,generation,class,"
        "ticket,worker,question,recommended,status,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            decision_id,
            record["scope"],
            record["project_id"],
            record["generation"],
            decision_class,
            ticket,
            worker,
            question,
            recommended,
            PENDING,
            _stamp(now),
        ),
    )
    if answer is not None:
        _settle(
            db, record, caller, decision_id, decision_class=decision_class, text=answer, now=now
        )
    return {"decision_id": decision_id, "status": ANSWERED if answer else PENDING}, []


def _settle(  # noqa: PLR0913 - explicit stored fields
    db: Connection,
    record: dict[str, object],
    caller: str,
    decision_id: str,
    *,
    decision_class: str,
    text: str,
    now: datetime,
) -> None:
    """Store the owner's answer; an ``objective`` answer also becomes an amendment."""
    # pylint: disable=too-many-arguments
    _resolve(db, decision_id, text, caller, now)
    if decision_class == OBJECTIVE:
        amendments.append(db, record, text, caller, decision_id=decision_id)


def answer(  # noqa: PLR0913, PLR0917 - plan signature: positional record fields
    db: Connection,
    record: dict[str, object],
    caller: str,
    decision_id: str,
    answer: str,
    now: datetime,
) -> Result:
    """Store the owner's verbatim answer to a pending decision."""
    # pylint: disable=too-many-arguments,too-many-positional-arguments,redefined-outer-name
    _require_coordinator(db, record, caller)
    text = _text(answer, "answer")
    row = _current(db, record, decision_id, ANSWERED)
    if row["status"] != PENDING:
        raise BusError(NOT_PENDING)
    _settle(db, record, caller, decision_id, decision_class=str(row["class"]), text=text, now=now)
    return {"decision_id": decision_id, "status": ANSWERED}, []


def _reason(
    db: Connection,
    record: dict[str, object],
    row: dict[str, object],
    caller: str,
    now: datetime,
) -> str | None:
    """Why the caller may not act on the decision; None means accepted."""
    if row["generation"] != record["generation"]:
        return "stale_generation"
    if row["status"] not in {ANSWERED, "defaulted"}:
        return "not_answered"
    if store.coordinator_enrolled(db, record, caller):
        return None
    grants = cast(
        "list[tuple[str]]",
        db.execute(
            "SELECT grant_id FROM coordination_grants WHERE scope=? AND project_id=? "
            "AND recipient=? AND generation=? ORDER BY rowid",
            (record["scope"], record["project_id"], caller, record["generation"]),
        ).fetchall(),
    )
    for (grant_id,) in grants:
        granted = delegation.effective(db, record, grant_id, now=now)
        if granted is not None and granted.owner_decisions:
            return None if row["worker"] in {None, caller} else "wrong_session"
    return "not_delegated"


def status(
    db: Connection, record: dict[str, object], caller: str, decision_id: str, *, now: datetime
) -> dict[str, object]:
    """Whether the caller may act on the decision; the answer is shown only when accepted.

    ``now`` is the lifetime reference for the caller's grant.
    """
    row = _row(db, decision_id)
    if row is None or (row["scope"], row["project_id"]) != (record["scope"], record["project_id"]):
        raise BusError("Decision not found")
    reason = _reason(db, record, row, caller, now)
    shown = _public(row) if reason is None or store.coordinator_enrolled(db, record, caller) else {}
    return {
        "decision_id": decision_id,
        **shown,
        "accepted": reason is None,
        **({} if reason is None else {"reason": reason}),
    }


def listing(db: Connection, record: dict[str, object], caller: str) -> dict[str, object]:
    """Every decision of the current generation, oldest first; coordinator only."""
    _require_coordinator(db, record, caller)
    cursor = db.execute(
        f"SELECT {COLUMNS} FROM coordination_decisions "  # noqa: S608 - fixed column list
        "WHERE scope=? AND project_id=? AND generation=? ORDER BY rowid",
        (record["scope"], record["project_id"], record["generation"]),
    )
    return {
        "decisions": [
            dict(zip((column[0] for column in cursor.description), row, strict=True))
            for row in cast("list[tuple[object, ...]]", cursor.fetchall())
        ]
    }
