"""Best-effort host wake requests after durable bus delivery."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import NotRequired, TypedDict, cast

WAKE_TIMEOUT_SECONDS = 10
WAKE_FAILED = "failed"


class WakeResult(TypedDict):
    status: str
    via: str
    reason: NotRequired[str]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv only, no shell
        command, capture_output=True, text=True, timeout=WAKE_TIMEOUT_SECONDS, check=False
    )


def _failure(result: subprocess.CompletedProcess[str], via: str) -> WakeResult:
    detail = result.stderr.strip().splitlines()
    reason = detail[-1] if detail else f"{via} exited with status {result.returncode}"
    return {"status": WAKE_FAILED, "via": via, "reason": reason}


def _wake_codex(session_id: str, message_id: int) -> WakeResult:
    binary = shutil.which("codex")
    if binary is None:
        return {"status": WAKE_FAILED, "via": "codex queue", "reason": "codex is not on PATH"}
    command = [
        binary,
        "queue",
        "--thread",
        session_id,
        "--message",
        f"Read and handle llm-bus message #{message_id} in your inbox.",
    ]
    try:
        result = _run(command)
    except subprocess.TimeoutExpired:
        return {"status": "unknown", "via": "codex queue", "reason": "wake request timed out"}
    except OSError as exc:
        return {"status": WAKE_FAILED, "via": "codex queue", "reason": str(exc)}
    return (
        {"status": "requested", "via": "codex queue"}
        if result.returncode == 0
        else _failure(result, "codex queue")
    )


def _claude_pane(raw: str, session_id: str) -> str | None:
    try:
        response = cast("object", json.loads(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(response, dict):
        return None
    result = cast("dict[str, object]", response).get("result")
    if not isinstance(result, dict):
        return None
    agents = cast("dict[str, object]", result).get("agents")
    if not isinstance(agents, list):
        return None
    matches: list[str] = []
    for item in cast("list[object]", agents):
        if not isinstance(item, dict):
            continue
        agent = cast("dict[str, object]", item)
        agent_session = agent.get("agent_session")
        if agent.get("agent") != "claude" or not isinstance(agent_session, dict):
            continue
        identity = cast("dict[str, object]", agent_session)
        pane_id = agent.get("pane_id")
        if (
            identity.get("kind") == "id"
            and identity.get("value") == session_id
            and isinstance(pane_id, str)
        ):
            matches.append(pane_id)
    return matches[0] if len(matches) == 1 else None


def _find_claude_pane(binary: str, session_id: str) -> str | WakeResult:
    try:
        agents = _run([binary, "agent", "list"])
    except subprocess.TimeoutExpired:
        return {
            "status": WAKE_FAILED,
            "via": "herdr agent list",
            "reason": "agent lookup timed out",
        }
    except OSError as exc:
        return {"status": WAKE_FAILED, "via": "herdr agent list", "reason": str(exc)}
    if agents.returncode:
        return _failure(agents, "herdr agent list")
    pane_id = _claude_pane(agents.stdout, session_id)
    if pane_id is None:
        return {
            "status": WAKE_FAILED,
            "via": "herdr agent prompt",
            "reason": "Claude session is not uniquely reachable in Herdr",
        }
    return pane_id


def _wake_claude(session_id: str, message_id: int) -> WakeResult:
    binary = shutil.which("herdr")
    if binary is None:
        return {
            "status": "unsupported",
            "via": "herdr agent prompt",
            "reason": "Herdr is required to wake a Claude Code recipient from llm-bus",
        }
    pane = _find_claude_pane(binary, session_id)
    if isinstance(pane, dict):
        return pane
    try:
        result = _run(
            [
                binary,
                "agent",
                "prompt",
                pane,
                f"Read and handle llm-bus message #{message_id} in your inbox.",
            ]
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "unknown",
            "via": "herdr agent prompt",
            "reason": "wake request timed out",
        }
    except OSError as exc:
        return {"status": WAKE_FAILED, "via": "herdr agent prompt", "reason": str(exc)}
    return (
        {"status": "requested", "via": "herdr agent prompt"}
        if result.returncode == 0
        else _failure(result, "herdr agent prompt")
    )


def wake(recipient: str, message_id: int) -> WakeResult:
    """Request a host turn without risking the already-committed bus message."""
    kind, separator, session_id = recipient.partition(":")
    if not separator or not session_id:
        return {"status": "unsupported", "via": "none", "reason": "Invalid recipient address"}
    if kind == "codex":
        return _wake_codex(session_id, message_id)
    if kind == "claude":
        return _wake_claude(session_id, message_id)
    return {"status": "unsupported", "via": "none", "reason": "Unknown recipient kind"}
