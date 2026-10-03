"""Host wake adapters never undo already-stored messages."""

import json
import shutil
import subprocess

import pytest

import llm_bus_wake


def test_claude_wake_never_sends_terminal_input(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def herdr_path(_name: str) -> str:
        return "/bin/herdr"

    def run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        agents = {
            "result": {
                "agents": [
                    {
                        "agent": "claude",
                        "pane_id": "w1:p1",
                        "agent_session": {"kind": "id", "value": "target"},
                    }
                ]
            }
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(agents), "")

    monkeypatch.setattr(shutil, "which", herdr_path)
    monkeypatch.setattr(llm_bus_wake, "_run", run)
    result = llm_bus_wake.wake("claude:target", 7)
    assert commands == []
    assert result["status"] == "unsupported"
    assert result["via"] == "none"
    assert "draft" in result.get("reason", "")


def test_codex_wake_queues_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def codex_path(_name: str) -> str:
        return "/bin/codex"

    monkeypatch.setattr(shutil, "which", codex_path)

    def run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "Queued", "")

    monkeypatch.setattr(llm_bus_wake, "_run", run)
    assert llm_bus_wake.wake("codex:session-123", 42) == {
        "status": "requested",
        "via": "codex queue",
    }
    assert commands == [
        [
            "/bin/codex",
            "queue",
            "--thread",
            "session-123",
            "--message",
            "Read and handle llm-bus message #42 in your inbox.",
        ]
    ]


def test_missing_host_and_timeout_report_wake_state(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_path(_name: str) -> None:
        return None

    monkeypatch.setattr(shutil, "which", missing_path)
    assert llm_bus_wake.wake("codex:one", 1)["status"] == "failed"
    assert llm_bus_wake.wake("claude:two", 2)["status"] == "unsupported"

    def codex_path(_name: str) -> str:
        return "/bin/codex"

    monkeypatch.setattr(shutil, "which", codex_path)

    def timeout(command: list[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 10)

    monkeypatch.setattr(llm_bus_wake, "_run", timeout)
    assert llm_bus_wake.wake("codex:one", 1)["status"] == "unknown"
