"""Host lifecycle registration and wake notices."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from llm_bus.cli.guidance import GUIDANCE_VERSION, HOOK_COUNT_LIMIT, guidance
from llm_bus.errors import BusError
from llm_bus.wake.adapters import parse_wake_notice

if TYPE_CHECKING:
    from llm_bus.mailbox.store import Store

CLAUDE_SOCKET_ENV = "CLAUDE_CODE_MESSAGING_SOCKET"


def _acknowledged_wake(store: Store, recipient: str, prompt: object) -> bool:
    identity = parse_wake_notice(prompt)
    return identity is not None and store.message_acknowledged(recipient, *identity)


def hook(store: Store, kind: str) -> None:
    hook_input = cast("object", json.load(sys.stdin))
    if not isinstance(hook_input, dict):
        raise BusError("Hook input must be a JSON object")
    hook_data = cast("dict[str, object]", hook_input)
    session_id = hook_data.get("session_id")
    event = hook_data.get("hook_event_name")
    if not isinstance(session_id, str) or event not in {
        "SessionStart",
        "UserPromptSubmit",
        "SessionEnd",
    }:
        raise BusError("Hook input lacks a valid session_id or event")
    if event == "SessionEnd":
        store.end_session(kind, session_id)
        return
    project = hook_data.get("cwd")
    if not isinstance(project, str):
        project = str(Path.cwd())
    project = str(Path(project).resolve())
    agent_address = store.register(kind, session_id, project, messaging_socket=claude_socket(kind))
    count = store.pending_count(agent_address, limit=HOOK_COUNT_LIMIT)
    if (
        kind == "codex"
        and event == "UserPromptSubmit"
        and count == 0
        and _acknowledged_wake(store, agent_address, hook_data.get("prompt"))
    ):
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": event,
                        "additionalContext": (
                            "This bus wake refers to an already acknowledged message, "
                            "and the inbox is empty. No action is needed for this notice. "
                            "Do not retry or acknowledge it again."
                        ),
                    },
                }
            )
        )
        return
    full = store.guidance_needed(agent_address, GUIDANCE_VERSION, reset=event == "SessionStart")
    notice = guidance(store, agent_address, project, full=full, count=count)
    if kind == "codex":
        print(
            json.dumps(
                {"hookSpecificOutput": {"hookEventName": event, "additionalContext": notice}}
            )
        )
    else:
        print(notice)


def claude_socket(kind: str) -> str | None:
    return os.environ.get(CLAUDE_SOCKET_ENV) if kind == "claude" else None
