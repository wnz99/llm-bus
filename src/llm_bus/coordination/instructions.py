"""Owner standing instructions per audience; stored for the current project, shown verbatim."""

import hashlib
import unicodedata
from datetime import UTC, datetime
from sqlite3 import Connection
from typing import cast
from uuid import uuid4

from llm_bus.errors import BusError

AUDIENCES = ("coordinator", "worker")
MAX_TEXT = 8192
DIGEST_LENGTH = 12
COLUMNS = "instruction_id,audience,kind,text,sha256,recorded_by,recorded_at"
ALLOWED_CONTROLS = {"\n", "\t"}
DELIMITERS = ("<<<", ">>>")  # bound the verbatim text in guidance

# Active rows start at the newest replacement entry.
ACTIVE_SQL = (
    f"SELECT {COLUMNS} FROM coordination_instructions "  # noqa: S608 - fixed column list
    "WHERE scope=? AND project_id=? AND audience=? AND rowid >= COALESCE("
    "(SELECT MAX(rowid) FROM coordination_instructions "
    "WHERE scope=? AND project_id=? AND audience=? AND kind='set'), 0) ORDER BY rowid"
)


def active(db: Connection, record: dict[str, object], audience: str) -> list[dict[str, object]]:
    """The latest `set` for the audience plus later `amend` entries, oldest first."""
    key = (record["scope"], record["project_id"], audience)
    cursor = db.execute(ACTIVE_SQL, (*key, *key))
    return [
        dict(zip((c[0] for c in cursor.description), row, strict=True))
        for row in cast("list[tuple[object, ...]]", cursor.fetchall())
    ]


def digest(rows: list[dict[str, object]]) -> str:
    """Short digest of the active rows' stored hashes (not a hash of the displayed text)."""
    combined = "\n".join(str(row["sha256"]) for row in rows)
    return hashlib.sha256(combined.encode()).hexdigest()[:DIGEST_LENGTH]


def _validate(text: str) -> None:
    if not text.strip() or len(text) > MAX_TEXT:
        raise BusError(f"Instruction text must be 1 to {MAX_TEXT} characters")
    if any(unicodedata.category(char) == "Cc" and char not in ALLOWED_CONTROLS for char in text):
        raise BusError("Control characters other than newline and tab are forbidden")
    if any(unicodedata.category(char) == "Cf" for char in text):
        raise BusError("Unicode format characters are forbidden in instruction text")
    if any(mark in text for mark in DELIMITERS):
        raise BusError("Instruction text must not contain '<<<' or '>>>'")


def add(  # noqa: PLR0913 - explicit stored fields
    db: Connection,
    record: dict[str, object],
    *,
    audience: str,
    kind: str,
    text: str,
    by: str,
) -> None:
    """Store one entry; the caller holds the transaction and has checked the coordinator."""
    # pylint: disable=too-many-arguments
    _validate(text)
    current = active(db, record, audience)
    if kind == "amend":
        if not current:
            raise BusError(f"No {audience} instructions to amend; run without --amend first")
        total = sum(len(str(row["text"])) for row in current) + len(text)
        if total > MAX_TEXT:
            raise BusError(
                f"{audience.capitalize()} instructions would exceed {MAX_TEXT} characters"
            )
    db.execute(
        "INSERT INTO coordination_instructions(instruction_id,scope,project_id,audience,kind,"
        "text,sha256,recorded_by,recorded_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            uuid4().hex,
            record["scope"],
            record["project_id"],
            audience,
            kind,
            text,
            hashlib.sha256(text.encode()).hexdigest(),
            by,
            datetime.now(UTC).isoformat(),
        ),
    )
