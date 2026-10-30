"""Collaboration frame classification vocabulary."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_bus.collab.transport import Frame

REJECTED: Frame = {"ok": False, "error": "not accepted"}
MESSAGE_KINDS = frozenset({"request", "reply"})
