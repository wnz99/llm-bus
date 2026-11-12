"""Owner decision records: the coordinator stores the owner's answer, bound workers accept it.

The bus stores each answer verbatim with its session, turn, time and SHA-256. A record grants
nothing by itself: a worker acts on a decision only after ``status`` reports ``accepted``.
"""

from __future__ import annotations

import hashlib
import time
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from llm_bus.coordination import amendments, delegation, store
from llm_bus.coordination.models import ticket_text
from llm_bus.errors import BusError
from llm_bus.locks import notices
from llm_bus.locks.timer import TIMER_POLL_SECONDS, spawn
from llm_bus.mailbox.writes import send_in_transaction

if TYPE_CHECKING:
    from collections.abc import Callable
    from sqlite3 import Connection

    from llm_bus.mailbox.models import Message
    from llm_bus.mailbox.store import Store

CLASSES = ("scope", "security", "cost", "data", "irreversible", "objective")
OBJECTIVE = "objective"
DEFAULTABLE = "scope"
PENDING = "pending"
ANSWERED = "answered"
DEFAULTED = "defaulted"
DEFAULT_SOURCE = "default"
NOT_PENDING = "Decision not answered: not_pending"
WAKE_TURN = (
    "Decision answers must come from an owner turn, not a bus wake. "
    "Ask the owner to type the answer as a new prompt in this session, then record "
    "it in that turn; an answer given inside a wake-started turn does not count"
)
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
    """Bounded text safe to show verbatim between delimiters; newline and tab are allowed."""
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise BusError(f"Decision {name} must be 1 to {MAX_TEXT} characters")
    if any(unicodedata.category(char) in {"Cc", "Cf"} and char not in "\n\t" for char in value):
        raise BusError(f"Decision {name} must not contain control or format characters")
    if any(mark in value for mark in DELIMITERS):
        raise BusError(f"Decision {name} must not contain '<<<' or '>>>'")
    return value


def _require_coordinator(db: Connection, record: dict[str, object], caller: str) -> None:
    if not store.coordinator_enrolled(db, record, caller):
        raise BusError("Only the enrolled coordinator may record or list decisions")


def record_turn(mailbox: Store, address: str, *, wake: bool = False) -> None:
    """Record one prompt turn for an enrolled coordinator; any other address writes nothing.

    ``coordination_owner_turns.turn`` holds the owner-turn count N. A positive value means the
    latest prompt was an owner turn; a bus-wake prompt stores ``-N`` (``0`` before any owner
    turn), and the next owner turn stores ``N + 1``. ``at`` is the latest owner turn's time.
    """
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
        if wake:
            db.execute(
                "INSERT INTO coordination_owner_turns VALUES (?,0,?) "
                "ON CONFLICT(address) DO UPDATE SET turn=-abs(turn)",
                (address, _stamp(datetime.now(UTC))),
            )
            return
        db.execute(
            "INSERT INTO coordination_owner_turns VALUES (?,1,?) "
            "ON CONFLICT(address) DO UPDATE SET turn=abs(turn)+1,at=excluded.at",
            (address, _stamp(datetime.now(UTC))),
        )


def _source_turn(db: Connection, address: str) -> int | None:
    """The coordinator's latest recorded turn: negative or 0 after a bus wake, None if none."""
    row = cast(
        "tuple[int] | None",
        db.execute(
            "SELECT turn FROM coordination_owner_turns WHERE address=?", (address,)
        ).fetchone(),
    )
    return None if row is None else row[0]


def _require_owner_turn(db: Connection, caller: str) -> None:
    """Refuse an answer in a turn that a recognized bus wake started.

    This proves turn origin only, not that the answer repeats the owner's words. A caller
    without any turn record passes (no hook installed, or enrolled in this very turn).
    """
    turn = _source_turn(db, caller)
    if turn is not None and turn <= 0:
        raise BusError(WAKE_TURN)


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
        _require_owner_turn(db, caller)
        answer = _text(answer, "answer")
    decision_id = uuid4().hex
    deadline = (
        _deadline(db, record, now)
        if answer is None and _defaultable(decision_class, recommended)
        else None
    )
    db.execute(
        "INSERT INTO coordination_decisions(decision_id,scope,project_id,generation,class,"
        "ticket,worker,question,recommended,status,created_at,deadline_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
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
            deadline,
        ),
    )
    if answer is not None:
        _settle(
            db, record, caller, decision_id, decision_class=decision_class, text=answer, now=now
        )
    created: dict[str, object] = {
        "decision_id": decision_id,
        "status": ANSWERED if answer else PENDING,
    }
    if deadline is not None:
        created["deadline_at"] = deadline
    return created, []


def _defaultable(decision_class: str, recommended: str | None) -> bool:
    """Only an unanswered ``scope`` decision with a recommended option may take a default."""
    return decision_class == DEFAULTABLE and recommended is not None


def _deadline(db: Connection, record: dict[str, object], now: datetime) -> str | None:
    """When the project's decision defaults are on, the time a new decision defaults."""
    current = delegation.project_delegation(db, record)
    if current is None or not current.owner_decisions or current.decision_default_minutes is None:
        return None
    return _stamp(now + timedelta(minutes=current.decision_default_minutes))


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
    _require_owner_turn(db, caller)
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
    if row["status"] not in {ANSWERED, DEFAULTED}:
        return "not_answered"
    if store.coordinator_enrolled(db, record, caller):
        return None
    return _worker_reason(db, record, row, caller, now)


def _worker_reason(
    db: Connection,
    record: dict[str, object],
    row: dict[str, object],
    caller: str,
    now: datetime,
) -> str | None:
    """Whether a bound worker may act on an answered or defaulted decision."""
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
            if row["worker"] not in {None, caller}:
                return "wrong_session"
            if row["status"] == DEFAULTED and granted.decision_default_minutes is None:
                return "default_not_bound"
            return None
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


def decision_rows(
    db: Connection, record: dict[str, object], *, defaulted: bool = False
) -> list[dict[str, object]]:
    """Decisions of the current generation, oldest first; ``defaulted`` keeps defaults only."""
    cursor = db.execute(
        f"SELECT {COLUMNS} FROM coordination_decisions "  # noqa: S608 - fixed column list
        "WHERE scope=? AND project_id=? AND generation=?"
        + (" AND status='defaulted'" if defaulted else "")
        + " ORDER BY rowid",
        (record["scope"], record["project_id"], record["generation"]),
    )
    return [
        dict(zip((column[0] for column in cursor.description), row, strict=True))
        for row in cast("list[tuple[object, ...]]", cursor.fetchall())
    ]


def listing(
    db: Connection, record: dict[str, object], caller: str, *, defaulted: bool = False
) -> dict[str, object]:
    """Decisions of the current generation, oldest first; coordinator only."""
    _require_coordinator(db, record, caller)
    return {"decisions": decision_rows(db, record, defaulted=defaulted)}


def _known(db: Connection, address: str) -> bool:
    return db.execute("SELECT 1 FROM agents WHERE address=?", (address,)).fetchone() is not None


def _tell(db: Connection, record: dict[str, object], recipient: str, body: str) -> list[Message]:
    """A plain notice from the coordinator; it grants nothing. Unregistered recipients get none."""
    coordinator = str(record["coordinator"])
    if not (_known(db, coordinator) and _known(db, recipient)):
        return []
    return [send_in_transaction(db, coordinator, recipient, body, cross_folder=True)]


def apply_due(
    db: Connection, record: dict[str, object], *, now: datetime | None = None
) -> list[Message]:
    """Default every due, still eligible pending decision of the current generation.

    Eligibility is rechecked on every call (M1): nothing is applied after revoke, and a
    project without owner decisions or decision defaults loses its deadlines. The caller
    holds ``BEGIN IMMEDIATE``, commits, then passes the notices to ``notices.deliver``.
    """
    if record["revoked_at"] is not None:
        return []
    key = (record["scope"], record["project_id"], record["generation"])
    current = delegation.project_delegation(db, record)
    if current is None or not current.owner_decisions or current.decision_default_minutes is None:
        db.execute(
            "UPDATE coordination_decisions SET deadline_at=NULL WHERE scope=? AND project_id=? "
            "AND generation=? AND status='pending' AND deadline_at IS NOT NULL",
            key,
        )
        return []
    moment = now or datetime.now(UTC)
    due = cast(
        "list[tuple[str, str, str | None]]",
        db.execute(
            "SELECT decision_id,recommended,worker FROM coordination_decisions "
            "WHERE scope=? AND project_id=? AND generation=? AND status='pending' "
            "AND class=? AND recommended IS NOT NULL AND deadline_at<=? ORDER BY rowid",
            (*key, DEFAULTABLE, _stamp(moment)),
        ).fetchall(),
    )
    messages: list[Message] = []
    for decision_id, recommended, worker in due:
        db.execute(
            "UPDATE coordination_decisions SET status=?,answer=?,source_session=?,resolved_at=? "
            "WHERE decision_id=? AND status='pending'",
            (DEFAULTED, recommended, DEFAULT_SOURCE, _stamp(moment), decision_id),
        )
        messages += _tell(
            db,
            record,
            str(record["coordinator"]),
            f"Decision {decision_id} took its recommended option by default: {recommended}. "
            "Report it to the owner.",
        )
        if worker is not None and _enrolled_worker(db, record, worker):
            messages += _tell(
                db,
                record,
                worker,
                f"Decision {decision_id} was answered by default: {recommended}. "
                f"Run coordination decision-show {decision_id}; act on it only if it reports "
                "accepted: true.",
            )
    return messages


def apply_for_coordinator(mailbox: Store, address: str) -> None:
    """Hook-time lazy application for an enrolled coordinator, then wakes after commit.

    Worker and unenrolled sessions never reach the write; only a due row opens it.
    """
    moment = datetime.now(UTC)
    with mailbox.transaction() as db:
        found = db.execute(
            "SELECT p.* FROM coordination_enrollments e JOIN coordination_projects p "
            "ON p.scope=e.scope AND p.project_id=e.project_id "
            "WHERE e.address=? AND e.role='coordinator'",
            (address,),
        )
        row = cast("tuple[object, ...] | None", found.fetchone())
        if row is None:
            return
        record = dict(zip((column[0] for column in found.description), row, strict=True))
        if record["coordinator"] != address or not _due_exists(db, record, moment):
            return
        db.execute("BEGIN IMMEDIATE")
        messages = apply_due(db, store.project(db, str(record["scope"]), str(record["label"])))
    notices.deliver(mailbox, messages)


def _due_exists(db: Connection, record: dict[str, object], moment: datetime) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM coordination_decisions WHERE scope=? AND project_id=? "
            "AND generation=? AND status='pending' AND deadline_at<=? LIMIT 1",
            (record["scope"], record["project_id"], record["generation"], _stamp(moment)),
        ).fetchone()
        is not None
    )


def defaulted_count(db: Connection, record: dict[str, object]) -> int:
    return cast(
        "tuple[int]",
        db.execute(
            "SELECT COUNT(*) FROM coordination_decisions WHERE scope=? AND project_id=? "
            "AND generation=? AND status='defaulted'",
            (record["scope"], record["project_id"], record["generation"]),
        ).fetchone(),
    )[0]


def schedule_default(label: str, decision_id: str) -> None:
    """Start the detached default timer; without it the next coordination action or hook applies."""
    spawn("coordination", "decision-timer", label, "--decision-id", decision_id)


def run_decision_timer(
    mailbox: Store, decision_id: str, *, sleep: Callable[[float], None] = time.sleep
) -> None:
    """Sleep to the decision's deadline, then make one application attempt and wake both sides."""
    while True:
        with mailbox.transaction() as db:
            row = cast(
                "tuple[str, str, str] | None",
                db.execute(
                    "SELECT deadline_at,scope,project_id FROM coordination_decisions "
                    "WHERE decision_id=? AND status='pending' AND deadline_at IS NOT NULL",
                    (decision_id,),
                ).fetchone(),
            )
        if row is None:
            return
        remaining = (datetime.fromisoformat(row[0]) - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            break
        sleep(min(remaining, TIMER_POLL_SECONDS))
    with mailbox.transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        found = db.execute(
            "SELECT * FROM coordination_projects WHERE scope=? AND project_id=?", row[1:]
        )
        project = cast("tuple[object, ...] | None", found.fetchone())
        if project is None:
            return
        record = dict(zip((column[0] for column in found.description), project, strict=True))
        messages = apply_due(db, record)
    notices.deliver(mailbox, messages)
