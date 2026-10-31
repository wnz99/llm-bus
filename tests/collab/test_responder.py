"""responder."""

from __future__ import annotations

import pytest

from llm_bus.collab.models import CollabError
from llm_bus.collab.responder import command, secret_findings

from .support import responder_peer


def test_secret_scan_flags_credentials_but_not_ordinary_text() -> None:
    assert "GitHub token" in secret_findings("token ghp_" + "a1B2" * 10, {})
    assert "private key" in secret_findings("-----BEGIN OPENSSH PRIVATE KEY-----", {})
    assert "local environment secret" in secret_findings(
        "value is s3cr3t-value-123", {"DEPLOY_TOKEN": "s3cr3t-value-123"}
    )
    assert secret_findings("I'm editing src/api.py at commit " + "a" * 40, {}) == []


def test_responder_tool_limits_are_cli_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BUS_COLLAB_CLAUDE", "claude")
    readonly = command(responder_peer("claude:abc"), "readonly", "hi")
    assert readonly[readonly.index("--resume") + 1] == "abc"
    for flag in ("--fork-session", "--restricted", "--strict-mcp-config"):
        assert flag in readonly
    assert readonly[readonly.index("--tools") + 1] == "Read,Grep,Glob"
    assert readonly[readonly.index("--permission-mode") + 1] == "dontAsk"
    assert "--resume" not in command(responder_peer(None), "isolated", "hi")
    readwrite = command(responder_peer("claude:abc"), "readwrite", "hi")
    assert readwrite[readwrite.index("--tools") + 1] == "Read,Grep,Glob,Edit,Write"
    with pytest.raises(CollabError):
        command(responder_peer("codex:abc"), "readonly", "hi")


@pytest.mark.parametrize("mode", ["readonly", "readwrite", "isolated"])
def test_summary_responder_has_no_session_history(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv("LLM_BUS_COLLAB_CLAUDE", "claude")
    peer = responder_peer("codex:private-session")
    peer["responder_context"] = "summary"
    peer["status_summary"] = "Updating the API; coordinate changes to src/api.py."
    argv = command(peer, mode, "Which files are changing?")
    assert "--resume" not in argv
    assert "--fork-session" not in argv
    assert "private-session" not in " ".join(argv)
    assert peer["status_summary"] in argv[2]
    assert "no access to the owner's conversation" in argv[argv.index("--append-system-prompt") + 1]
    tools = "Read,Grep,Glob,Edit,Write" if mode == "readwrite" else "Read,Grep,Glob"
    assert argv[argv.index("--tools") + 1] == tools
