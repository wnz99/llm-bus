"""Identify child sessions using machine-owned native host metadata."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

from llm_bus.errors import BusError

CODEX_HOME_ENV = "CODEX_HOME"


def is_subagent(agent_address: str) -> bool:
    """Claude children share their parent's session; Codex children have native spawn edges."""
    if not agent_address.startswith("codex:"):
        return False
    home = Path(os.environ.get(CODEX_HOME_ENV, str(Path.home() / ".codex"))).expanduser().resolve()
    databases = [p for p in home.glob("state_*.sqlite") if p.stem.removeprefix("state_").isdigit()]
    if not databases:
        return False
    path = max(databases, key=lambda p: int(p.stem.removeprefix("state_")))
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            return (
                db.execute(
                    "SELECT 1 FROM thread_spawn_edges WHERE child_thread_id=?",
                    (agent_address.removeprefix("codex:"),),
                ).fetchone()
                is not None
            )
    except sqlite3.Error as error:
        raise BusError("Cannot determine Codex session role from native metadata") from error


def require_main_agent(agent_address: str) -> None:
    if is_subagent(agent_address):
        raise BusError("Sub-agents must report to their parent; only main agents use the bus")
