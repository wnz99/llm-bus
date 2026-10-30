"""Collaboration runtime ownership."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from pathlib import Path

    from llm_bus.collab.state import CollabState


def daemon_file(state: CollabState) -> Path:
    return state.home / "daemon.json"


def running_daemon(state: CollabState) -> dict[str, object] | None:
    """Return daemon details when its recorded process is still alive."""
    path = daemon_file(state)
    try:
        data = cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))
        pid = data["pid"]
        if not isinstance(pid, int):
            return None
        os.kill(pid, 0)
    except (OSError, ValueError, KeyError):
        return None
    return data
