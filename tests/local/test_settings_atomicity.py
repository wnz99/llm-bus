"""installation."""

from __future__ import annotations

import json
import stat
from typing import TYPE_CHECKING, cast

import pytest

from llm_bus.errors import BusError
from llm_bus.hosts.settings import configure_hosts

from .support import settings_paths

if TYPE_CHECKING:
    from pathlib import Path


def test_install_rejects_invalid_second_file_before_writing_first(tmp_path: Path) -> None:
    codex, claude = settings_paths(tmp_path)
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
    codex, claude = settings_paths(tmp_path)
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

    monkeypatch.setattr("llm_bus.hosts.files.atomic_write", fail_second_write)
    with pytest.raises(OSError, match="second write failure"):
        configure_hosts(tmp_path, install=True)
    assert [
        (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) for path in (codex, claude)
    ] == before


def test_install_detects_concurrent_settings_change_without_losing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex, claude = settings_paths(tmp_path)
    codex.write_text("{}")
    claude.write_text("{}")

    def update_claude_after_codex_write(
        path: Path, content: bytes, mode: int, _expected: bytes | None
    ) -> None:
        path.write_bytes(content)
        path.chmod(mode)
        if path == codex:
            claude.write_text('{"concurrentHostSetting": "preserved"}')

    monkeypatch.setattr("llm_bus.hosts.files.atomic_write", update_claude_after_codex_write)
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

    monkeypatch.setattr("llm_bus.hosts.files.current_bytes", concurrent_read)
    with pytest.raises(BusError, match="Settings changed while updating"):
        configure_hosts(tmp_path, install=True)
    assert json.loads(codex.read_text()) == {"concurrentHostSetting": "preserved"}


def test_failed_activation_removes_own_hooks_after_concurrent_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex, claude = settings_paths(tmp_path)
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

    monkeypatch.setattr("llm_bus.hosts.files.atomic_write", fail_claude_after_codex_edit)
    with pytest.raises(OSError, match="Claude write failed"):
        configure_hosts(tmp_path, install=True)
    settings = cast("dict[str, object]", json.loads(codex.read_text()))
    assert settings == {"concurrentHostSetting": "preserved"}
    assert json.loads(claude.read_text()) == {}
