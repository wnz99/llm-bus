"""Concise output preserves actionable facts and expanded JSON compatibility."""

import json
from pathlib import Path
from typing import cast

import pytest

from llm_bus.cli.app import main
from llm_bus.cli.output import serialize
from llm_bus.cli.parser import build_parser
from llm_bus.mailbox.store import Store


def decoded(result: object, command: str) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(serialize(result, command, "agent")))


def test_send_receipt_preserves_delivery_and_authority() -> None:
    result = {
        "id": 12,
        "remote_id": "uuid",
        "status": "queued_local",
        "sender": "codex:self",
        "recipient": "server/repo/claude:worker",
        "body": "large payload " * 200,
        "sender_cwd": "/source",
        "recipient_cwd": "/target",
        "sent_at": "timestamp",
        "reply_to": 7,
        "coordination": {"verified": False, "reason": "revoked", "expires_at": None},
        "wake": {"status": "unknown", "reason": "timeout", "via": "native"},
    }
    agent = decoded(result, "send")
    assert agent == {
        key: value
        for key, value in result.items()
        if key
        not in {
            "sender",
            "body",
            "sender_cwd",
            "recipient_cwd",
            "sent_at",
        }
    }
    assert serialize(result, "send") == json.dumps(result)
    assert len(serialize(result, "send", "agent")) < len(json.dumps(result)) // 4


def test_inbox_shared_context_preserves_each_message() -> None:
    rows = [
        {
            "id": number,
            "sender": "codex:owner",
            "recipient": "claude:worker",
            "sender_cwd": "/owner",
            "recipient_cwd": "/worker",
            "body": "Do task\n完整",
            "reply_to": None,
            "coordination": {"verified": True, "grant_id": str(number)},
        }
        for number in (1, 2)
    ]
    agent = decoded(rows, "inbox")
    assert agent["shared"] == {
        "recipient": "claude:worker",
        "sender_cwd": "/owner",
        "recipient_cwd": "/worker",
    }
    restored = [
        {**cast("dict[str, object]", agent["shared"]), **row}
        for row in cast("list[dict[str, object]]", agent["items"])
    ]
    assert restored == [
        {key: value for key, value in row.items() if key != "reply_to"} for row in rows
    ]
    assert json.loads(serialize([], "inbox", "agent")) == []
    assert (
        json.loads(serialize(rows[:1], "inbox", "agent"))[0]["coordination"]
        == rows[0]["coordination"]
    )


def test_lock_catalog_keeps_scope_holder_and_notices() -> None:
    rows = [
        {
            "resource": name,
            "scope": "shared",
            "description": "Purpose",
            "created_by": "old",
            "last_used_at": "timestamp",
            "held_by": None,
            "check_at": None,
            "waiters": 0,
        }
        for name in ("runtime", "instructions")
    ]
    notice = {"message_id": 42, "wake": {"status": "failed"}}
    result = decoded({"resources": rows, "notices": [notice]}, "lock")
    resources = cast("dict[str, object]", result["resources"])
    assert resources["shared"] == {"scope": "shared"}
    assert all(
        row["held_by"] is None and row["waiters"] == 0
        for row in cast("list[dict[str, object]]", resources["items"])
    )
    assert result["notices"] == [notice]
    state = {
        "status": "waiting",
        "lock": {"holder": "other", "expires_at": None, "waiters": ["self"]},
        "notified": False,
    }
    assert decoded(state, "lock") == state


def test_unicode_is_readable_without_terminal_controls() -> None:
    result = {"body": "中文 café\n\x1b[31m\u202e\u0085\u2028"}
    encoded = serialize(result, "status", "agent")
    assert "中文 café" in encoded
    assert "\u202e" not in encoded
    assert "\u0085" not in encoded
    assert "\u2028" not in encoded
    assert json.loads(encoded) == result


def test_format_parser_is_opt_in() -> None:
    parser = build_parser()
    assert cast("dict[str, object]", vars(parser.parse_args(["inbox"])))["format"] == "json"
    assert (
        cast("dict[str, object]", vars(parser.parse_args(["--format", "agent", "inbox"])))["format"]
        == "agent"
    )
    assert (
        cast("dict[str, object]", vars(parser.parse_args(["--format", "json", "inbox"])))["format"]
        == "json"
    )


@pytest.mark.parametrize(
    "command", ["coordination", "coordinate", "join", "status", "remote-status", "report"]
)
def test_diagnostics_and_grants_are_not_pruned(command: str) -> None:
    result = {
        "verified": False,
        "reason": "revoked",
        "expires_at": None,
        "grants": [{"operations": ["assign"], "revoked_at": "timestamp"}],
        "synchronization": {"stale": True, "error_code": "unavailable"},
    }
    assert decoded(result, command) == result


def test_different_recipient_and_folder_context_is_not_shared() -> None:
    rows = [
        {"id": 1, "recipient": "claude:a", "sender_cwd": "/a", "body": "a"},
        {"id": 2, "recipient": "claude:b", "sender_cwd": "/b", "body": "b"},
    ]
    assert json.loads(serialize(rows, "history", "agent")) == rows


def test_agent_cli_send_and_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_BUS_DB", str(tmp_path / "bus.db"))
    monkeypatch.setenv("CODEX_THREAD_ID", "sender")
    store = Store(tmp_path / "bus.db")
    store.register("claude", "worker", str(tmp_path))
    # Avoid a real native wake; an ended recipient still accepts durable messages.
    store.end_session("claude", "worker")
    assert main(["--format", "agent", "send", "claude:worker", "--body", "payload"]) == 0
    sent = capsys.readouterr()
    receipt = cast("dict[str, object]", json.loads(sent.out))
    assert "body" not in receipt
    assert cast("dict[str, object]", receipt["wake"])["status"] != "requested"
    assert sent.err == ""
    assert main(["--format", "agent", "history"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["body"] == "payload"
    assert main(["--format", "json", "history"]) == 0
    assert "sender_cwd" in json.loads(capsys.readouterr().out)[0]
