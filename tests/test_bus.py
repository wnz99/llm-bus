"""Focused local mailbox and host identity tests."""

from __future__ import annotations

import io
import json
import sqlite3
import stat
import subprocess
from typing import TYPE_CHECKING, cast

import pytest

from llm_bus import host_identity, main
from llm_bus_install import configure_hosts
from llm_bus_store import BusError, SenderContext, Store

if TYPE_CHECKING:
    from pathlib import Path


def test_messages_survive_reopen_until_recipient_acknowledges(tmp_path: Path) -> None:
    """Both providers share mailbox; reads never consume messages."""
    path = tmp_path / "bus.sqlite3"
    store = Store(path)
    codex = store.register("codex", "codex-session", "/project")
    claude = store.register("claude", "claude-session", "/project")

    sent = store.send(
        SenderContext("codex", "codex-session", "/project"), claude, "Review migration"
    )
    assert sent["sender_cwd"] == "/project"
    reopened = Store(path)
    assert reopened.inbox(claude) == [sent]
    assert reopened.inbox(claude) == [sent]
    assert reopened.inbox(codex) == []
    with pytest.raises(BusError, match="do not belong"):
        reopened.acknowledge(codex, [sent["id"]])
    assert reopened.acknowledge(claude, [sent["id"]]) == 1
    assert reopened.acknowledge(claude, [sent["id"]]) == 0
    assert Store(path).inbox(claude) == []


def test_unknown_recipient_and_oversized_message_are_rejected(tmp_path: Path) -> None:
    """Typos and unbounded payloads cannot silently fill mailbox."""
    store = Store(tmp_path / "bus.sqlite3")
    recipient = store.register("codex", "two", "/project")
    with pytest.raises(BusError, match="Unknown recipient"):
        store.send(SenderContext("claude", "one", "/project"), "codex:missing", "hello")
    with pytest.raises(BusError, match="64 KiB"):
        store.send(SenderContext("claude", "one", "/project"), recipient, "x" * 65537)
    assert store.inbox(recipient) == []


def test_folder_scope_requires_explicit_cross_folder_send(tmp_path: Path) -> None:
    store = Store(tmp_path / "bus.sqlite3")
    sender = store.register("codex", "one", "/one")
    same = store.register("claude", "two", "/one")
    other = store.register("claude", "three", "/other")
    assert {peer["address"] for peer in store.agents("/one")} == {sender, same}
    assert len(store.agents()) == 3
    # A concurrent hook can register the same sender in another folder before send begins.
    store.register("codex", "one", "/other")
    with pytest.raises(BusError, match="--cross-folder"):
        store.send(SenderContext("codex", "one", "/one"), other, "hello")
    sent = store.send(SenderContext("codex", "one", "/one"), other, "hello", cross_folder=True)
    assert sent["sender_cwd"] == "/one"
    store.register("codex", "one", "/new-location")
    assert store.inbox(other)[0]["sender_cwd"] == "/one"


def test_existing_database_gets_nullable_sender_folder(tmp_path: Path) -> None:
    path = tmp_path / "bus.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE agents (address TEXT PRIMARY KEY, kind TEXT, project TEXT, last_seen TEXT);"
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, sender TEXT, recipient TEXT, "
            "body TEXT, sent_at TEXT, acknowledged_at TEXT);"
            "INSERT INTO agents VALUES ('codex:one', 'codex', '/one', 'now');"
            "INSERT INTO agents VALUES ('claude:two', 'claude', '/one', 'now');"
            "INSERT INTO messages VALUES (1, 'codex:one', 'claude:two', 'old', 'now', NULL);"
        )
    assert Store(path).inbox("claude:two")[0]["sender_cwd"] is None


def test_mixed_acknowledgement_batch_rolls_back(tmp_path: Path) -> None:
    """A foreign message ID cannot consume an earlier valid item in the same batch."""
    store = Store(tmp_path / "bus.sqlite3")
    first = store.register("codex", "first", "/project")
    second = store.register("codex", "second", "/project")
    own_message = store.send(SenderContext("claude", "one", "/project"), first, "one")
    foreign_message = store.send(SenderContext("claude", "one", "/project"), second, "two")
    with pytest.raises(BusError, match="do not belong"):
        store.acknowledge(first, [own_message["id"], foreign_message["id"]])
    assert store.inbox(first) == [own_message]


def test_host_identity_uses_native_session_id() -> None:
    """No explicit name or launch argument is required."""
    assert host_identity({"CODEX_THREAD_ID": "thread-1"}) == ("codex", "thread-1")
    assert host_identity({"CLAUDE_CODE_SESSION_ID": "session-2"}) == ("claude", "session-2")
    with pytest.raises(BusError, match="No host session ID"):
        host_identity({})


def test_codex_hook_registers_and_reports_pending_without_message_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Hook gives Codex address and count without elevating message body into context."""
    path = tmp_path / "bus.sqlite3"
    monkeypatch.setenv("LLM_BUS_DB", str(path))
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            json.dumps(
                {"session_id": "thread-1", "hook_event_name": "SessionStart", "cwd": "/project"}
            )
        ),
    )
    assert main(["hook", "codex"]) == 0
    output = cast("dict[str, dict[str, str]]", json.loads(capsys.readouterr().out))
    assert "codex:thread-1" in output["hookSpecificOutput"]["additionalContext"]
    assert "llm-bus inbox" in output["hookSpecificOutput"]["additionalContext"]

    store = Store(path)
    store.send(
        SenderContext("claude", "session-2", "/project"),
        "codex:thread-1",
        "Untrusted message body",
    )
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            json.dumps(
                {"session_id": "thread-1", "hook_event_name": "UserPromptSubmit", "cwd": "/project"}
            )
        ),
    )
    assert main(["hook", "codex"]) == 0
    notice = cast("dict[str, dict[str, str]]", json.loads(capsys.readouterr().out))[
        "hookSpecificOutput"
    ]["additionalContext"]
    assert "1 pending message" in notice
    assert "Untrusted message body" not in notice


def test_claude_hook_uses_same_session_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Claude hook and shell environment resolve to one durable address."""
    monkeypatch.setenv("LLM_BUS_DB", str(tmp_path / "bus.sqlite3"))
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            json.dumps({"session_id": "claude-session", "hook_event_name": "SessionStart"})
        ),
    )
    assert main(["hook", "claude"]) == 0
    notice = capsys.readouterr().out
    assert "claude:claude-session" in notice
    assert "llm-bus inbox" in notice
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-session")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    assert main(["whoami"]) == 0
    assert json.loads(capsys.readouterr().out) == {"address": "claude:claude-session"}


def test_install_and_uninstall_preserve_other_host_settings(tmp_path: Path) -> None:
    codex = tmp_path / ".codex" / "hooks.json"
    claude = tmp_path / ".claude" / "settings.json"
    codex.parent.mkdir()
    claude.parent.mkdir()
    codex.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [{"command": "other"}]}]}}))
    claude.write_text(json.dumps({"model": "sonnet", "permissions": {"allow": ["Bash(git *)"]}}))
    configure_hosts(tmp_path, install=True)
    once = (codex.read_text(), claude.read_text())
    configure_hosts(tmp_path, install=True)
    assert once == (codex.read_text(), claude.read_text())
    assert "llm-bus hook codex" in codex.read_text()
    assert "llm-bus hook claude" in claude.read_text()
    assert "Bash(llm-bus *)" in claude.read_text()
    configure_hosts(tmp_path, install=False)
    assert json.loads(codex.read_text()) == {
        "hooks": {"SessionStart": [{"hooks": [{"command": "other"}]}]}
    }
    assert json.loads(claude.read_text()) == {
        "model": "sonnet",
        "permissions": {"allow": ["Bash(git *)"]},
    }


def test_uninstall_removes_existing_matching_bus_settings(tmp_path: Path) -> None:
    codex = tmp_path / ".codex" / "hooks.json"
    claude = tmp_path / ".claude" / "settings.json"
    codex.parent.mkdir()
    claude.parent.mkdir()
    codex.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "startup",
                            "hooks": [{"type": "command", "command": "llm-bus hook codex"}],
                        }
                    ]
                }
            }
        )
    )
    claude.write_text(json.dumps({"permissions": {"allow": ["Bash(llm-bus *)"]}}))
    configure_hosts(tmp_path, install=True)
    configure_hosts(tmp_path, install=False)
    assert json.loads(codex.read_text()) == {}
    assert json.loads(claude.read_text()) == {}


def test_optional_bus_hook_fields_are_normalized_and_removed(tmp_path: Path) -> None:
    codex = tmp_path / ".codex" / "hooks.json"
    claude = tmp_path / ".claude" / "settings.json"
    codex.parent.mkdir()
    claude.parent.mkdir()
    codex.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "startup",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "llm-bus hook codex",
                                    "timeout": 30,
                                }
                            ],
                        }
                    ]
                }
            }
        )
    )
    claude.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "llm-bus hook claude",
                                    "async": True,
                                },
                                {"type": "command", "command": "other"},
                            ]
                        }
                    ]
                }
            }
        )
    )
    configure_hosts(tmp_path, install=True)
    assert codex.read_text().count("llm-bus hook codex") == len(
        ("SessionStart", "UserPromptSubmit")
    )
    assert claude.read_text().count("llm-bus hook claude") == len(
        ("SessionStart", "UserPromptSubmit")
    )
    configure_hosts(tmp_path, install=False)
    assert json.loads(codex.read_text()) == {}
    assert json.loads(claude.read_text()) == {
        "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "other"}]}]}
    }


def test_install_rejects_invalid_second_file_before_writing_first(tmp_path: Path) -> None:
    codex = tmp_path / ".codex" / "hooks.json"
    claude = tmp_path / ".claude" / "settings.json"
    codex.parent.mkdir()
    claude.parent.mkdir()
    codex.write_text("{}")
    claude.write_text('{"hooks": []}')
    with pytest.raises(BusError, match="hooks must be a JSON object"):
        configure_hosts(tmp_path, install=True)
    assert codex.read_text() == "{}"


def test_install_rejects_symlinked_settings_without_replacing_link(tmp_path: Path) -> None:
    target = tmp_path / "managed-hooks.json"
    target.write_text("{}")
    codex = tmp_path / ".codex" / "hooks.json"
    codex.parent.mkdir()
    codex.symlink_to(target)
    with pytest.raises(BusError, match="symlink is unsupported"):
        configure_hosts(tmp_path, install=True)
    assert codex.is_symlink()
    assert target.read_text() == "{}"


def test_failed_second_settings_write_restores_both_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = tmp_path / ".codex" / "hooks.json"
    claude = tmp_path / ".claude" / "settings.json"
    codex.parent.mkdir()
    claude.parent.mkdir()
    codex.write_text('{"hooks": {}}\n')
    claude.write_text('{"model": "sonnet"}\n')
    codex.chmod(0o640)
    before = [(path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) for path in (codex, claude)]
    writes = 0

    def fail_second_write(path: Path, content: bytes, mode: int, _expected: bytes | None) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("simulated second write failure")
        path.write_bytes(content)
        path.chmod(mode)

    monkeypatch.setattr("llm_bus_install._atomic_write", fail_second_write)
    with pytest.raises(OSError, match="second write failure"):
        configure_hosts(tmp_path, install=True)
    assert [
        (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) for path in (codex, claude)
    ] == before


def test_install_detects_concurrent_settings_change_without_losing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = tmp_path / ".codex" / "hooks.json"
    claude = tmp_path / ".claude" / "settings.json"
    codex.parent.mkdir()
    claude.parent.mkdir()
    codex.write_text("{}")
    claude.write_text("{}")

    def update_claude_after_codex_write(
        path: Path, content: bytes, mode: int, _expected: bytes | None
    ) -> None:
        path.write_bytes(content)
        path.chmod(mode)
        if path == codex:
            claude.write_text('{"concurrentHostSetting": "preserved"}')

    monkeypatch.setattr("llm_bus_install._atomic_write", update_claude_after_codex_write)
    with pytest.raises(BusError, match="Settings changed while updating"):
        configure_hosts(tmp_path, install=True)
    assert codex.read_text() == "{}"
    assert json.loads(claude.read_text()) == {"concurrentHostSetting": "preserved"}


def test_install_rechecks_target_before_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = tmp_path / ".codex" / "hooks.json"
    codex.parent.mkdir()
    codex.write_text("{}")
    codex_reads = 0

    def concurrent_read(path: Path) -> bytes | None:
        nonlocal codex_reads
        if path == codex:
            codex_reads += 1
            if codex_reads == 3:
                codex.write_text('{"concurrentHostSetting": "preserved"}')
        return path.read_bytes() if path.exists() else None

    monkeypatch.setattr("llm_bus_install._current_bytes", concurrent_read)
    with pytest.raises(BusError, match="Settings changed while updating"):
        configure_hosts(tmp_path, install=True)
    assert json.loads(codex.read_text()) == {"concurrentHostSetting": "preserved"}


def test_failed_activation_removes_own_hooks_after_concurrent_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = tmp_path / ".codex" / "hooks.json"
    claude = tmp_path / ".claude" / "settings.json"
    codex.parent.mkdir()
    claude.parent.mkdir()
    codex.write_text("{}")
    claude.write_text("{}")
    writes = 0

    def fail_claude_after_codex_edit(
        path: Path, content: bytes, mode: int, _expected: bytes | None
    ) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("Claude write failed")
        path.write_bytes(content)
        path.chmod(mode)
        if path == codex:
            settings = cast("dict[str, object]", json.loads(codex.read_text()))
            settings["concurrentHostSetting"] = "preserved"
            codex.write_text(json.dumps(settings))

    monkeypatch.setattr("llm_bus_install._atomic_write", fail_claude_after_codex_edit)
    with pytest.raises(OSError, match="Claude write failed"):
        configure_hosts(tmp_path, install=True)
    settings = cast("dict[str, object]", json.loads(codex.read_text()))
    assert settings == {"concurrentHostSetting": "preserved"}
    assert json.loads(claude.read_text()) == {}


def test_uninstall_removes_bus_handler_from_shared_group(tmp_path: Path) -> None:
    codex = tmp_path / ".codex" / "hooks.json"
    codex.parent.mkdir()
    codex.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {"type": "command", "command": "llm-bus hook codex"},
                                {"type": "command", "command": "other"},
                            ]
                        }
                    ]
                }
            }
        )
    )
    configure_hosts(tmp_path, install=False)
    assert json.loads(codex.read_text())["hooks"]["SessionStart"] == [
        {"hooks": [{"type": "command", "command": "other"}]}
    ]


def test_cli_filters_peers_and_requires_cross_folder_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("LLM_BUS_DB", str(tmp_path / "bus.sqlite3"))
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-one")
    monkeypatch.chdir(first)
    assert main(["whoami"]) == 0
    capsys.readouterr()
    monkeypatch.delenv("CODEX_THREAD_ID")
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "claude-two")
    monkeypatch.chdir(second)
    assert main(["whoami"]) == 0
    capsys.readouterr()
    assert main(["list"]) == 0
    assert len(cast("list[object]", json.loads(capsys.readouterr().out))) == 1
    assert main(["list", "--all"]) == 0
    assert len(cast("list[object]", json.loads(capsys.readouterr().out))) == 2
    assert main(["send", "codex:codex-one", "--body", "hello"]) == 1
    assert "--cross-folder" in capsys.readouterr().err
    assert main(["send", "codex:codex-one", "--body", "hello", "--cross-folder"]) == 0
    assert json.loads(capsys.readouterr().out)["sender_cwd"] == str(second)


def test_uninstall_removes_hooks_then_uv_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_hosts(tmp_path, install=True)
    monkeypatch.setattr("llm_bus.Path.home", lambda: tmp_path)

    def fake_which(_name: str) -> str:
        return "/usr/local/bin/uv"

    monkeypatch.setattr("llm_bus.shutil.which", fake_which)
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        assert check
        calls.append(command)
        assert "llm-bus hook codex" not in (tmp_path / ".codex/hooks.json").read_text()
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("llm_bus.subprocess.run", fake_run)
    assert main(["uninstall"]) == 0
    assert json.loads(capsys.readouterr().out)["installed"] is False
    assert calls == [["/usr/local/bin/uv", "tool", "uninstall", "llm-bus"]]


def test_install_reports_codex_hook_trust_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("llm_bus.Path.home", lambda: tmp_path)
    assert main(["install"]) == 0
    result = cast("dict[str, object]", json.loads(capsys.readouterr().out))
    assert result["installed"] is True
    assert "/hooks" in cast("str", result["codex_next_step"])


def test_failed_uninstall_restores_exact_host_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = configure_hosts(tmp_path, install=True)
    before = [path.read_bytes() for path in paths]
    monkeypatch.setattr("llm_bus.Path.home", lambda: tmp_path)

    def fake_which(_name: str) -> str:
        return "/usr/local/bin/uv"

    def fail_run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        assert check
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("llm_bus.shutil.which", fake_which)
    monkeypatch.setattr("llm_bus.subprocess.run", fail_run)
    assert main(["uninstall"]) == 1
    assert "returned non-zero" in capsys.readouterr().err
    assert [path.read_bytes() for path in paths] == before


def test_uninstall_without_uv_keeps_host_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = configure_hosts(tmp_path, install=True)
    before = [path.read_bytes() for path in paths]
    monkeypatch.setattr("llm_bus.Path.home", lambda: tmp_path)

    def missing_uv(_name: str) -> None:
        return None

    monkeypatch.setattr("llm_bus.shutil.which", missing_uv)
    assert main(["uninstall"]) == 1
    assert "uv is required" in capsys.readouterr().err
    assert [path.read_bytes() for path in paths] == before


def test_failed_uninstall_preserves_concurrent_host_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    codex, claude = configure_hosts(tmp_path, install=True)
    monkeypatch.setattr("llm_bus.Path.home", lambda: tmp_path)

    def fake_which(_name: str) -> str:
        return "/usr/local/bin/uv"

    def fail_after_host_change(
        command: list[str], *, check: bool
    ) -> subprocess.CompletedProcess[str]:
        assert check
        settings = cast("dict[str, object]", json.loads(claude.read_text()))
        settings["concurrentHostSetting"] = "preserved"
        claude.write_text(json.dumps(settings))
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("llm_bus.shutil.which", fake_which)
    monkeypatch.setattr("llm_bus.subprocess.run", fail_after_host_change)
    assert main(["uninstall"]) == 1
    capsys.readouterr()
    assert "llm-bus hook codex" in codex.read_text()
    assert "llm-bus hook claude" in claude.read_text()
    assert json.loads(claude.read_text())["concurrentHostSetting"] == "preserved"


def test_failed_uninstall_restores_original_matcher_and_event_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = tmp_path / ".codex" / "hooks.json"
    codex.parent.mkdir()
    original = {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "startup",
                    "hooks": [
                        {"type": "command", "command": "llm-bus hook codex", "timeout": 30},
                        {"type": "command", "command": "other"},
                    ],
                }
            ]
        }
    }
    codex.write_text(json.dumps(original))
    monkeypatch.setattr("llm_bus.Path.home", lambda: tmp_path)

    def fake_which(_name: str) -> str:
        return "/usr/local/bin/uv"

    def fail_run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        assert check
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("llm_bus.shutil.which", fake_which)
    monkeypatch.setattr("llm_bus.subprocess.run", fail_run)
    assert main(["uninstall"]) == 1
    assert json.loads(codex.read_text()) == original


def test_failed_uninstall_restores_bus_entry_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = tmp_path / ".codex" / "hooks.json"
    claude = tmp_path / ".claude" / "settings.json"
    codex.parent.mkdir()
    claude.parent.mkdir()
    codex_before = {
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": "llm-bus hook codex"}]},
                {"hooks": [{"type": "command", "command": "other"}]},
            ]
        }
    }
    claude_before = {
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": "llm-bus hook claude"}]},
                {"hooks": [{"type": "command", "command": "other"}]},
            ]
        },
        "permissions": {"allow": ["Bash(llm-bus *)", "Bash(git *)"]},
    }
    codex.write_text(json.dumps(codex_before))
    claude.write_text(json.dumps(claude_before))
    monkeypatch.setattr("llm_bus.Path.home", lambda: tmp_path)

    def fake_which(_name: str) -> str:
        return "/usr/local/bin/uv"

    monkeypatch.setattr("llm_bus.shutil.which", fake_which)

    def fail_run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        assert check
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("llm_bus.subprocess.run", fail_run)
    assert main(["uninstall"]) == 1
    assert json.loads(codex.read_text()) == codex_before
    assert json.loads(claude.read_text()) == claude_before


def test_failed_uninstall_keeps_bus_order_with_concurrent_insertions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = tmp_path / ".codex" / "hooks.json"
    claude = tmp_path / ".claude" / "settings.json"
    codex.parent.mkdir()
    claude.parent.mkdir()
    codex_bus = {"hooks": [{"type": "command", "command": "llm-bus hook codex"}]}
    claude_bus = {"hooks": [{"type": "command", "command": "llm-bus hook claude"}]}
    first = {"hooks": [{"type": "command", "command": "first"}]}
    last = {"hooks": [{"type": "command", "command": "last"}]}
    concurrent = {"hooks": [{"type": "command", "command": "concurrent"}]}
    codex.write_text(json.dumps({"hooks": {"SessionStart": [first, codex_bus, last]}}))
    claude.write_text(
        json.dumps(
            {
                "hooks": {"SessionStart": [first, claude_bus, last]},
                "permissions": {"allow": ["Bash(git *)", "Bash(llm-bus *)", "Bash(uv *)"]},
            }
        )
    )
    monkeypatch.setattr("llm_bus.Path.home", lambda: tmp_path)

    def fake_which(_name: str) -> str:
        return "/usr/local/bin/uv"

    def fail_after_host_change(
        command: list[str], *, check: bool
    ) -> subprocess.CompletedProcess[str]:
        assert check
        codex.write_text(json.dumps({"hooks": {"SessionStart": [concurrent, first, last]}}))
        claude.write_text(
            json.dumps(
                {
                    "hooks": {"SessionStart": [concurrent, first, last]},
                    "permissions": {"allow": ["Bash(ls *)", "Bash(git *)", "Bash(uv *)"]},
                }
            )
        )
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("llm_bus.shutil.which", fake_which)
    monkeypatch.setattr("llm_bus.subprocess.run", fail_after_host_change)
    assert main(["uninstall"]) == 1
    assert json.loads(codex.read_text()) == {
        "hooks": {"SessionStart": [concurrent, first, codex_bus, last]}
    }
    assert json.loads(claude.read_text()) == {
        "hooks": {"SessionStart": [concurrent, first, claude_bus, last]},
        "permissions": {"allow": ["Bash(ls *)", "Bash(git *)", "Bash(llm-bus *)", "Bash(uv *)"]},
    }


def test_failed_uninstall_restores_standalone_before_shared_bus_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = tmp_path / ".codex" / "hooks.json"
    codex.parent.mkdir()
    standalone = {"hooks": [{"type": "command", "command": "llm-bus hook codex", "timeout": 1}]}
    shared = {
        "hooks": [
            {"type": "command", "command": "llm-bus hook codex", "timeout": 2},
            {"type": "command", "command": "other"},
        ]
    }
    shared_without_bus = {"hooks": [{"type": "command", "command": "other"}]}
    tail = {"hooks": [{"type": "command", "command": "tail"}]}
    concurrent = {"hooks": [{"type": "command", "command": "concurrent"}]}
    codex.write_text(json.dumps({"hooks": {"SessionStart": [standalone, shared, tail]}}))
    monkeypatch.setattr("llm_bus.Path.home", lambda: tmp_path)

    def fake_which(_name: str) -> str:
        return "/usr/local/bin/uv"

    def fail_after_host_change(
        command: list[str], *, check: bool
    ) -> subprocess.CompletedProcess[str]:
        assert check
        codex.write_text(
            json.dumps({"hooks": {"SessionStart": [concurrent, shared_without_bus, tail]}})
        )
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("llm_bus.shutil.which", fake_which)
    monkeypatch.setattr("llm_bus.subprocess.run", fail_after_host_change)
    assert main(["uninstall"]) == 1
    assert json.loads(codex.read_text()) == {
        "hooks": {"SessionStart": [concurrent, standalone, shared, tail]}
    }
