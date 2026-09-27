"""SQLite mailbox shared by local agent sessions."""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, TypedDict, cast

if TYPE_CHECKING:
    from collections.abc import Generator

SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MAX_MESSAGE_BYTES = 64 * 1024
MAX_BATCH = 100
DB_ENV = "LLM_BUS_DB"
SCHEMA = """
    CREATE TABLE IF NOT EXISTS agents (
        address TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        project TEXT NOT NULL,
        last_seen TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY,
        sender TEXT NOT NULL REFERENCES agents(address),
        recipient TEXT NOT NULL REFERENCES agents(address),
        body TEXT NOT NULL,
        sender_cwd TEXT,
        sent_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        acknowledged_at TEXT
    );
    CREATE INDEX IF NOT EXISTS messages_pending
        ON messages(recipient, acknowledged_at, id);
"""


class BusError(ValueError):
    """Invalid operation at the local bus boundary."""


class Agent(TypedDict):
    """Registered session address."""

    address: str
    kind: str
    project: str
    last_seen: str


class Message(TypedDict):
    """Unacknowledged mailbox item."""

    id: int
    sender: str
    recipient: str
    body: str
    sender_cwd: str | None
    sent_at: str


class SenderContext(NamedTuple):
    """Invocation identity and folder used together for an atomic send."""

    kind: str
    session_id: str
    project: str


def default_path() -> Path:
    """Place user-local bus state outside repositories."""
    configured = os.environ.get(DB_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "share" / "llm-bus" / "bus.sqlite3"


def address(kind: str, session_id: str) -> str:
    """Build stable address from host session ID."""
    if kind not in {"claude", "codex"}:
        raise BusError("Agent kind must be claude or codex")
    if not SESSION_ID.fullmatch(session_id):
        raise BusError("Host session ID has invalid characters or length")
    return f"{kind}:{session_id}"


class Store:
    """Durable messages; inbox reads leave items pending until acknowledged."""

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.executescript(SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            columns = {
                row[1]
                for row in cast(
                    "list[tuple[int, str, str, int, object, int]]",
                    db.execute("PRAGMA table_info(messages)").fetchall(),
                )
            }
            if "sender_cwd" not in columns:
                db.execute("ALTER TABLE messages ADD COLUMN sender_cwd TEXT")
            db.commit()
        self.path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 10000")
        db.execute("PRAGMA journal_mode = WAL")
        return db

    @contextmanager
    def _transaction(self) -> Generator[sqlite3.Connection]:
        with closing(self._connect()) as db:
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def register(self, kind: str, session_id: str, project: str) -> str:
        """Upsert session address without deleting its pending messages."""
        with self._transaction() as db:
            return self._upsert_agent(db, kind, session_id, project)

    @staticmethod
    def _upsert_agent(db: sqlite3.Connection, kind: str, session_id: str, project: str) -> str:
        agent_address = address(kind, session_id)
        db.execute(
            "INSERT INTO agents(address, kind, project, last_seen) "
            "VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) "
            "ON CONFLICT(address) DO UPDATE SET project = excluded.project, "
            "last_seen = excluded.last_seen",
            (agent_address, kind, project),
        )
        return agent_address

    def agents(self, project: str | None = None) -> list[Agent]:
        """List known addresses; last_seen does not promise online presence."""
        with closing(self._connect()) as db:
            query = "SELECT address, kind, project, last_seen FROM agents"
            parameters: tuple[str, ...] = ()
            if project is not None:
                query += " WHERE project = ?"
                parameters = (project,)
            rows = cast(
                "list[tuple[str, str, str, str]]",
                db.execute(query + " ORDER BY last_seen DESC", parameters).fetchall(),
            )
        return [
            Agent(address=row[0], kind=row[1], project=row[2], last_seen=row[3]) for row in rows
        ]

    def send(
        self,
        sender_context: SenderContext,
        recipient: str,
        body: str,
        *,
        cross_folder: bool = False,
    ) -> Message:
        """Atomically register sender, check folder, and persist message."""
        if not body.strip():
            raise BusError("Message body cannot be empty")
        if len(body.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise BusError("Message body exceeds 64 KiB")
        with self._transaction() as db:
            sender = self._upsert_agent(
                db, sender_context.kind, sender_context.session_id, sender_context.project
            )
            recipient_row = cast(
                "tuple[str] | None",
                db.execute("SELECT project FROM agents WHERE address = ?", (recipient,)).fetchone(),
            )
            if recipient_row is None:
                raise BusError(f"Unknown recipient {recipient!r}; run `llm-bus list` first")
            if sender_context.project != recipient_row[0] and not cross_folder:
                raise BusError("Recipient is in another folder; use --cross-folder to send")
            cursor = db.execute(
                "INSERT INTO messages(sender, recipient, body, sender_cwd) VALUES (?, ?, ?, ?)",
                (sender, recipient, body, sender_context.project),
            )
            message_id = cursor.lastrowid
            if message_id is None:
                raise RuntimeError("SQLite did not return a message ID")
            row = cast(
                "tuple[int, str, str, str, str | None, str] | None",
                db.execute(
                    "SELECT id, sender, recipient, body, sender_cwd, sent_at "
                    "FROM messages WHERE id = ?",
                    (message_id,),
                ).fetchone(),
            )
        if row is None:
            raise RuntimeError("Inserted message could not be read")
        return Message(
            id=row[0],
            sender=row[1],
            recipient=row[2],
            body=row[3],
            sender_cwd=row[4],
            sent_at=row[5],
        )

    def inbox(self, recipient: str, limit: int = MAX_BATCH) -> list[Message]:
        """Read pending messages without consuming them."""
        if not 1 <= limit <= MAX_BATCH:
            raise BusError("Inbox limit must be between 1 and 100")
        with closing(self._connect()) as db:
            rows = cast(
                "list[tuple[int, str, str, str, str | None, str]]",
                db.execute(
                    "SELECT id, sender, recipient, body, sender_cwd, sent_at FROM messages "
                    "WHERE recipient = ? AND acknowledged_at IS NULL ORDER BY id LIMIT ?",
                    (recipient, limit),
                ).fetchall(),
            )
        return [
            Message(
                id=row[0],
                sender=row[1],
                recipient=row[2],
                body=row[3],
                sender_cwd=row[4],
                sent_at=row[5],
            )
            for row in rows
        ]

    def pending_count(self, recipient: str) -> int:
        """Count unread messages for low-context hook notification."""
        with closing(self._connect()) as db:
            row = cast(
                "tuple[int] | None",
                db.execute(
                    "SELECT count(*) FROM messages WHERE recipient = ? AND acknowledged_at IS NULL",
                    (recipient,),
                ).fetchone(),
            )
        if row is None:
            raise RuntimeError("SQLite count query returned no row")
        return int(row[0])

    def acknowledge(self, recipient: str, ids: list[int]) -> int:
        """Acknowledge only own messages; reject mixed or unknown batches atomically."""
        if (
            not ids
            or len(ids) > MAX_BATCH
            or any(isinstance(item, bool) or item < 1 for item in ids)
        ):
            raise BusError("Provide 1-100 positive integer message IDs")
        unique_ids = list(dict.fromkeys(ids))
        with self._transaction() as db:
            acknowledged = 0
            for message_id in unique_ids:
                exists = cast(
                    "tuple[int] | None",
                    db.execute(
                        "SELECT 1 FROM messages WHERE recipient = ? AND id = ?",
                        (recipient, message_id),
                    ).fetchone(),
                )
                if exists is None:
                    raise BusError("One or more message IDs do not belong to this agent")
                cursor = db.execute(
                    "UPDATE messages SET acknowledged_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE recipient = ? AND acknowledged_at IS NULL AND id = ?",
                    (recipient, message_id),
                )
                acknowledged += cursor.rowcount
            return acknowledged
