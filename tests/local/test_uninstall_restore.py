"""installation."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING, cast

from llm_bus.cli.app import main
from llm_bus.hosts.settings import configure_hosts

from .support import settings_paths

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_failed_uninstall_restores_exact_host_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = configure_hosts(tmp_path, install=True)
    before = [path.read_bytes() for path in paths]
    monkeypatch.setattr("llm_bus.cli.installation.Path.home", lambda: tmp_path)

    def fake_which(_name: str) -> str:
        return "/usr/local/bin/uv"

    def fail_run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        assert check
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("llm_bus.cli.installation.shutil.which", fake_which)
    monkeypatch.setattr("llm_bus.cli.installation.subprocess.run", fail_run)
    assert main(["uninstall"]) == 1
    assert "returned non-zero" in capsys.readouterr().err
    assert [path.read_bytes() for path in paths] == before


def test_failed_uninstall_preserves_concurrent_host_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    codex, claude = configure_hosts(tmp_path, install=True)
    monkeypatch.setattr("llm_bus.cli.installation.Path.home", lambda: tmp_path)

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

    monkeypatch.setattr("llm_bus.cli.installation.shutil.which", fake_which)
    monkeypatch.setattr("llm_bus.cli.installation.subprocess.run", fail_after_host_change)
    assert main(["uninstall"]) == 1
    capsys.readouterr()
    assert "hook codex" in codex.read_text()
    assert "hook claude" in claude.read_text()
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
    monkeypatch.setattr("llm_bus.cli.installation.Path.home", lambda: tmp_path)

    def fake_which(_name: str) -> str:
        return "/usr/local/bin/uv"

    def fail_run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        assert check
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("llm_bus.cli.installation.shutil.which", fake_which)
    monkeypatch.setattr("llm_bus.cli.installation.subprocess.run", fail_run)
    assert main(["uninstall"]) == 1
    assert json.loads(codex.read_text()) == original


def test_failed_uninstall_restores_bus_entry_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex, claude = settings_paths(tmp_path)
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
    monkeypatch.setattr("llm_bus.cli.installation.Path.home", lambda: tmp_path)

    def fake_which(_name: str) -> str:
        return "/usr/local/bin/uv"

    monkeypatch.setattr("llm_bus.cli.installation.shutil.which", fake_which)

    def fail_run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        assert check
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("llm_bus.cli.installation.subprocess.run", fail_run)
    assert main(["uninstall"]) == 1
    assert json.loads(codex.read_text()) == codex_before
    assert json.loads(claude.read_text()) == claude_before


def test_failed_uninstall_keeps_bus_order_with_concurrent_insertions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex, claude = settings_paths(tmp_path)
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
    monkeypatch.setattr("llm_bus.cli.installation.Path.home", lambda: tmp_path)

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

    monkeypatch.setattr("llm_bus.cli.installation.shutil.which", fake_which)
    monkeypatch.setattr("llm_bus.cli.installation.subprocess.run", fail_after_host_change)
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
    monkeypatch.setattr("llm_bus.cli.installation.Path.home", lambda: tmp_path)

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

    monkeypatch.setattr("llm_bus.cli.installation.shutil.which", fake_which)
    monkeypatch.setattr("llm_bus.cli.installation.subprocess.run", fail_after_host_change)
    assert main(["uninstall"]) == 1
    assert json.loads(codex.read_text()) == {
        "hooks": {"SessionStart": [concurrent, standalone, shared, tail]}
    }
