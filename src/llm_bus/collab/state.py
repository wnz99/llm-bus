"""Collaboration state ownership."""

from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import closing, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

from llm_bus.collab import message_store, pairing_store, peer_store
from llm_bus.collab.models import (
    HOME_ENV,
    WINDOWS_DATA_ENV,
    ApprovedJoin,
    Grant,
    Message,
    NewMessage,
    NewPeer,
    Peer,
)
from llm_bus.collab.policy import github_user
from llm_bus.collab.schema import initialize

if TYPE_CHECKING:
    from collections.abc import Generator


def default_home() -> Path:
    """Keep collaboration identity outside repositories; one directory per identity."""
    configured = os.environ.get(HOME_ENV)
    if configured:
        return Path(configured).expanduser()
    if sys.platform != "win32":
        return Path.home() / ".config" / "llm-bus" / "collab"
    local_data = os.environ.get(WINDOWS_DATA_ENV) or str(Path.home() / "AppData" / "Local")
    return Path(local_data) / "llm-bus" / "collab"


class CollabState:  # pylint: disable=too-many-public-methods
    """SQLite state for one collaboration identity; only the owner edits grants."""

    home: Path
    path: Path

    def __init__(self, home: Path | None = None) -> None:
        self.home = home or default_home()
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.home / "collab.sqlite3"
        with closing(self._connect()) as db:
            initialize(db)
        self.path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA foreign_keys = ON")
        return db

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection]:
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.rollback()
                raise
            db.commit()

    def identity(self) -> tuple[str, str] | None:
        with closing(self._connect()) as db:
            return cast(
                "tuple[str, str] | None",
                db.execute("SELECT github_user, signing_key FROM identity").fetchone(),
            )

    def set_identity(self, user: str, signing_key: Path) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO identity(id, github_user, signing_key) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET github_user = excluded.github_user, "
                "signing_key = excluded.signing_key",
                (github_user(user), str(signing_key)),
            )

    def create_invite(
        self, peer_user: str, choice: Grant, repository: str, session: str | None
    ) -> tuple[str, str]:
        with self.transaction() as db:
            return pairing_store.create_invite(db, peer_user, choice, repository, session)

    def claim_invite(self, nonce: str, peer_user: str) -> tuple[Grant, str, str | None]:
        with self.transaction() as db:
            return pairing_store.claim_invite(db, nonce, peer_user)

    def invite_is_open(self, nonce: str, peer_user: str) -> bool:
        with closing(self._connect()) as db:
            return pairing_store.invite_is_open(db, nonce, peer_user)

    def add_join(self, invite: str, repository: str, session: str | None) -> int:
        with self.transaction() as db:
            return pairing_store.add_join(db, invite, repository, session)

    def join(self, join_id: int) -> tuple[str, str, str | None, str, str | None]:
        with closing(self._connect()) as db:
            return pairing_store.join(db, join_id)

    def approve_join(self, join_id: int, choice: Grant) -> None:
        with self.transaction() as db:
            return pairing_store.approve_join(db, join_id, choice)

    def approved_joins(self) -> list[ApprovedJoin]:
        with closing(self._connect()) as db:
            return pairing_store.approved_joins(db)

    def finish_join(self, join_id: int, status: str, detail: str) -> None:
        with self.transaction() as db:
            return pairing_store.finish_join(db, join_id, status, detail)

    def add_peer(self, facts: NewPeer, choice: Grant) -> Peer:
        with self.transaction() as db:
            peer_store.add_peer(db, facts, choice)
        peer = self.peer_by_pair(facts.pair)
        if peer is None:
            raise RuntimeError("Inserted peer could not be read")
        return peer

    def peers(self) -> list[Peer]:
        with closing(self._connect()) as db:
            return peer_store.peers(db)

    def peer(self, peer_id: int) -> Peer | None:
        with closing(self._connect()) as db:
            return peer_store.peer(db, peer_id)

    def peer_by_pair(self, pair: str) -> Peer | None:
        with closing(self._connect()) as db:
            return peer_store.peer_by_pair(db, pair)

    def set_mode(self, peer_id: int, choice: Grant) -> None:
        with self.transaction() as db:
            return peer_store.set_mode(db, peer_id, choice)

    def set_responder_context(self, peer_id: int, context: str, summary: str) -> None:
        with self.transaction() as db:
            return peer_store.set_responder_context(db, peer_id, context, summary)

    def set_session(self, peer_id: int, session: str) -> None:
        with self.transaction() as db:
            return peer_store.set_session(db, peer_id, session)

    def set_ticket(self, peer_id: int, ticket: str) -> None:
        with self.transaction() as db:
            return peer_store.set_ticket(db, peer_id, ticket)

    def revoke(self, peer_id: int) -> None:
        with self.transaction() as db:
            return peer_store.revoke(db, peer_id)

    def add_message(self, peer_id: int, new: NewMessage) -> Message | None:
        with self.transaction() as db:
            message_id = message_store.add_message(db, peer_id, new)
        return self.message(message_id) if message_id is not None else None

    def question_session(self, peer_id: int, uid: str) -> str | None:
        with closing(self._connect()) as db:
            return message_store.question_session(db, peer_id, uid)

    def message(self, message_id: int) -> Message:
        with closing(self._connect()) as db:
            return message_store.message(db, message_id)

    def messages(self, *, state: str | None = None, limit: int = 200) -> list[Message]:
        with closing(self._connect()) as db:
            return message_store.messages(db, state=state, limit=limit)

    def set_state(self, message_id: int, state: str, detail: str | None = None) -> None:
        with self.transaction() as db:
            return message_store.set_state(db, message_id, state, detail)

    def transition(self, message_id: int, expected: str, state: str) -> bool:
        with self.transaction() as db:
            return message_store.transition(db, message_id, expected, state)

    def first_answer(self, peer_id: int, message: Message) -> bool:
        with closing(self._connect()) as db:
            return message_store.first_answer(db, peer_id, message)

    def recent_auto_replies(self, peer_id: int) -> int:
        with closing(self._connect()) as db:
            return message_store.recent_auto_replies(db, peer_id)
