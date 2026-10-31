"""Collaboration exposure ownership."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from llm_bus.collab.models import (
    FORWARD_SENDER,
    FULL_MODE,
    NOTICE_SENDER,
    REPLY_SENDER_PREFIX,
    SENDER_PREFIX,
)
from llm_bus.collab.policy import effective_mode
from llm_bus.collab.state import CollabState, default_home

if TYPE_CHECKING:
    from pathlib import Path


def exposure_allowed(sender: str, recipient: str, home: Path | None = None) -> bool:
    """Recheck owner policy before a collaboration message is shown to a local agent.

    Daemon notices carry no peer text, and owner forwards are the owner's explicit choice.
    Answers to this side's own questions reach the asking session while the owner allows agent
    replies for that peer. Other peer text reaches an agent inbox only while that peer is in
    unexpired full mode for the same local session.
    """
    if sender in {NOTICE_SENDER, FORWARD_SENDER}:
        return True
    answer = sender.startswith(REPLY_SENDER_PREFIX)
    user, separator, pair = sender.removeprefix(
        REPLY_SENDER_PREFIX if answer else SENDER_PREFIX
    ).partition(":")
    if not separator or not pair:
        return False
    pair, _, question = pair.partition(":") if answer else (pair, "", "")
    state_home = home or default_home()
    if not (state_home / "collab.sqlite3").exists():
        return False
    try:
        state = CollabState(state_home)
        peers = state.peers()
    except (sqlite3.Error, OSError):
        return False
    return any(
        p["github_user"] == user
        and p["pair"] == pair
        and (
            (
                (p["agent_replies"] or effective_mode(p) == FULL_MODE)
                and effective_mode(p) is not None
                and state.question_session(p["id"], question) == recipient
            )
            if answer
            else p["session"] == recipient and effective_mode(p) == FULL_MODE
        )
        for p in peers
    )
