from __future__ import annotations

import os
import sqlite3
import sys
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from llm_bus.errors import BusError
from llm_bus.mailbox.schema import ensure_schema
from llm_bus.routing import routing_scope

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

DB_ENV = "LLM_BUS_DB"
WINDOWS_DATA_ENV = "LOCALAPPDATA"
SQLITE_TIMEOUT_SECONDS = 10
SQLITE_BUSY_RETRY_SECONDS = 0.01


def default_path() -> Path:
    """Place user-local bus state outside repositories."""
    configured = os.environ.get(DB_ENV)
    if configured:
        return Path(configured).expanduser()
    if sys.platform == "win32":
        local_data = os.environ.get(WINDOWS_DATA_ENV)
        base = Path(local_data) if local_data else Path.home() / "AppData" / "Local"
        return base / "llm-bus" / "bus.sqlite3"
    return Path.home() / ".local" / "share" / "llm-bus" / "bus.sqlite3"


def connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=10)
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 10000")
    deadline = time.monotonic() + SQLITE_TIMEOUT_SECONDS
    while True:
        try:
            db.execute("PRAGMA journal_mode = WAL")
            break
        except sqlite3.OperationalError as error:
            # Journal initialization can bypass SQLite's busy handler on a new file.
            if error.sqlite_errorcode != sqlite3.SQLITE_BUSY or time.monotonic() >= deadline:
                db.close()
                raise
            time.sleep(SQLITE_BUSY_RETRY_SECONDS)
    return db


@contextmanager
def transaction(open_connection: Callable[[], sqlite3.Connection]) -> Generator[sqlite3.Connection]:
    with closing(open_connection()) as db:
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise


def initialize(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with closing(connect(path)) as db:
        try:
            ensure_schema(db, routing_scope)
        except ValueError as error:
            raise BusError(str(error)) from error
    path.chmod(0o600)
