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


def test_install_and_uninstall_preserve_other_host_settings(tmp_path: Path) -> None:
    codex, claude = settings_paths(tmp_path)
    codex.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [{"command": "other"}]}]}}))
    claude.write_text(json.dumps({"model": "sonnet", "permissions": {"allow": ["Bash(git *)"]}}))
    configure_hosts(tmp_path, install=True)
    once = (codex.read_text(), claude.read_text())
    configure_hosts(tmp_path, install=True)
    assert once == (codex.read_text(), claude.read_text())
    assert "hook codex" in codex.read_text()
    assert "hook claude" in claude.read_text()
    assert "SessionEnd" in json.loads(codex.read_text())["hooks"]
    assert "SessionEnd" in json.loads(claude.read_text())["hooks"]
    assert "Bash(llm-bus *)" in claude.read_text()
    configure_hosts(tmp_path, install=False)
    assert json.loads(codex.read_text()) == {
        "hooks": {"SessionStart": [{"hooks": [{"command": "other"}]}]}
    }
    assert json.loads(claude.read_text()) == {
        "model": "sonnet",
        "permissions": {"allow": ["Bash(git *)"]},
    }


def test_install_respects_codex_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    codex_home = tmp_path / "custom-codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    configure_hosts(tmp_path, install=True)
    assert "hook codex" in (codex_home / "hooks.json").read_text()
    assert not (tmp_path / ".codex" / "hooks.json").exists()
    configure_hosts(tmp_path, install=False)


def test_uninstall_removes_existing_matching_bus_settings(tmp_path: Path) -> None:
    codex, claude = settings_paths(tmp_path)
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


def test_uninstall_removes_hooks_then_uv_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_hosts(tmp_path, install=True)
    monkeypatch.setattr("llm_bus.cli.installation.Path.home", lambda: tmp_path)

    def fake_which(_name: str) -> str:
        return "/usr/local/bin/uv"

    monkeypatch.setattr("llm_bus.cli.installation.shutil.which", fake_which)
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        assert check
        calls.append(command)
        assert "llm-bus hook codex" not in (tmp_path / ".codex/hooks.json").read_text()
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("llm_bus.cli.installation.subprocess.run", fake_run)
    assert main(["uninstall"]) == 0
    assert json.loads(capsys.readouterr().out)["installed"] is False
    assert calls == [["/usr/local/bin/uv", "tool", "uninstall", "llm-bus"]]


def test_install_reports_codex_hook_trust_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("llm_bus.cli.installation.Path.home", lambda: tmp_path)
    assert main(["install"]) == 0
    result = cast("dict[str, object]", json.loads(capsys.readouterr().out))
    assert result["installed"] is True
    assert "/hooks" in cast("str", result["codex_next_step"])


def test_uninstall_without_uv_keeps_host_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = configure_hosts(tmp_path, install=True)
    before = [path.read_bytes() for path in paths]
    monkeypatch.setattr("llm_bus.cli.installation.Path.home", lambda: tmp_path)

    def missing_uv(_name: str) -> None:
        return None

    monkeypatch.setattr("llm_bus.cli.installation.shutil.which", missing_uv)
    assert main(["uninstall"]) == 1
    assert "uv is required" in capsys.readouterr().err
    assert [path.read_bytes() for path in paths] == before


def test_optional_bus_hook_fields_are_normalized_and_removed(tmp_path: Path) -> None:
    codex, claude = settings_paths(tmp_path)
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
    assert codex.read_text().count("hook codex") == len(
        ("SessionStart", "UserPromptSubmit", "SessionEnd")
    )
    assert claude.read_text().count("hook claude") == len(
        ("SessionStart", "UserPromptSubmit", "SessionEnd")
    )
    configure_hosts(tmp_path, install=False)
    assert json.loads(codex.read_text()) == {}
    assert json.loads(claude.read_text()) == {
        "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "other"}]}]}
    }
