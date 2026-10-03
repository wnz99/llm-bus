"""Best-effort host wake requests after durable bus delivery."""

from __future__ import annotations

import shutil
import subprocess
from typing import NotRequired, TypedDict

WAKE_TIMEOUT_SECONDS = 10
WAKE_FAILED = "failed"
CLAUDE_WAKE_REASON = (
    "Claude wake is disabled to preserve prompt drafts; "
    "pending messages are reported by the next turn hook"
)


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


def wake(recipient: str, message_id: int) -> WakeResult:
    """Request a host turn without risking the already-committed bus message."""
    kind, separator, session_id = recipient.partition(":")
    if not separator or not session_id:
        return {"status": "unsupported", "via": "none", "reason": "Invalid recipient address"}
    if kind == "codex":
        return _wake_codex(session_id, message_id)
    if kind == "claude":
        return {
            "status": "unsupported",
            "via": "none",
            "reason": CLAUDE_WAKE_REASON,
        }
    return {"status": "unsupported", "via": "none", "reason": "Unknown recipient kind"}
