"""Host wake adapters never undo already-stored messages."""

import json
import shutil
import subprocess

import pytest

import llm_bus_wake


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


def test_claude_wake_targets_matching_herdr_pane(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def herdr_path(_name: str) -> str:
        return "/bin/herdr"

    monkeypatch.setattr(shutil, "which", herdr_path)
    agents = {
        "result": {
            "agents": [
                {
                    "agent": "claude",
                    "pane_id": "w1:p1",
                    "agent_session": {"kind": "id", "value": "other"},
                },
                {
                    "agent": "claude",
                    "pane_id": "w2:p3",
                    "agent_session": {"kind": "id", "value": "target"},
                },
            ]
        }
    }

    def run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        output = json.dumps(agents) if command[-2:] == ["agent", "list"] else "{}"
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(llm_bus_wake, "_run", run)
    assert llm_bus_wake.wake("claude:target", 7) == {
        "status": "requested",
        "via": "herdr agent prompt",
    }
    assert commands == [
        ["/bin/herdr", "agent", "list"],
        [
            "/bin/herdr",
            "agent",
            "prompt",
            "w2:p3",
            "Read and handle llm-bus message #7 in your inbox.",
        ],
    ]


def test_unreachable_claude_is_not_prompted(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def herdr_path(_name: str) -> str:
        return "/bin/herdr"

    monkeypatch.setattr(shutil, "which", herdr_path)

    def run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps({"result": {"agents": []}}), "")

    monkeypatch.setattr(llm_bus_wake, "_run", run)
    assert llm_bus_wake.wake("claude:missing", 7)["status"] == "failed"
    assert commands == [["/bin/herdr", "agent", "list"]]


def test_ambiguous_claude_session_is_not_prompted(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def herdr_path(_name: str) -> str:
        return "/bin/herdr"

    monkeypatch.setattr(shutil, "which", herdr_path)
    agents = {
        "result": {
            "agents": [
                {
                    "agent": "claude",
                    "pane_id": "w1:p1",
                    "agent_session": {"kind": "id", "value": "same"},
                },
                {
                    "agent": "claude",
                    "pane_id": "w2:p2",
                    "agent_session": {"kind": "id", "value": "same"},
                },
            ]
        }
    }

    def run(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(agents), "")

    monkeypatch.setattr(llm_bus_wake, "_run", run)
    assert llm_bus_wake.wake("claude:same", 7)["status"] == "failed"
    assert commands == [["/bin/herdr", "agent", "list"]]


def test_claude_lookup_timeout_is_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def herdr_path(_name: str) -> str:
        return "/bin/herdr"

    monkeypatch.setattr(shutil, "which", herdr_path)

    def timeout(command: list[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 10)

    monkeypatch.setattr(llm_bus_wake, "_run", timeout)
    assert llm_bus_wake.wake("claude:one", 1) == {
        "status": "failed",
        "via": "herdr agent list",
        "reason": "agent lookup timed out",
    }


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
