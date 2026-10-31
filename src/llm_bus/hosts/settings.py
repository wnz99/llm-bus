"""Install and remove user-level host hooks without replacing other settings."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from llm_bus.errors import BusError
from llm_bus.hosts import files
from llm_bus.hosts.hooks import update_claude_permission, update_hooks
from llm_bus.hosts.restore import restore_written

CODEX_HOME_ENV = "CODEX_HOME"
CLAUDE_CONFIG_DIR_ENV = "CLAUDE_CONFIG_DIR"


def codex_settings_path(home: Path) -> Path:
    codex_home = os.environ.get(CODEX_HOME_ENV)
    return (Path(codex_home).expanduser() if codex_home else home / ".codex") / "hooks.json"


def claude_settings_path(home: Path) -> Path:
    config_dir = os.environ.get(CLAUDE_CONFIG_DIR_ENV)
    return (Path(config_dir).expanduser() if config_dir else home / ".claude") / "settings.json"


def snapshot_host_settings(home: Path) -> files.SettingsSnapshot:
    """Capture both host settings from one validated read per file."""
    paths = (codex_settings_path(home), claude_settings_path(home))
    snapshots: files.SettingsSnapshot = []
    for path in paths:
        content = files.current_bytes(path)
        files.parse_settings(path, content)
        mode = stat.S_IMODE(path.stat().st_mode) if content is not None else 0o600
        snapshots.append((path, content, mode))
    return snapshots


def configure_hosts(
    home: Path, *, install: bool, snapshots: files.SettingsSnapshot | None = None
) -> list[Path]:
    """Merge or remove bus-owned hooks in both user settings files."""
    codex_path = codex_settings_path(home)
    claude_path = claude_settings_path(home)
    if snapshots is None:
        snapshots = snapshot_host_settings(home)
    codex_settings = files.parse_settings(codex_path, snapshots[0][1])
    claude_settings = files.parse_settings(claude_path, snapshots[1][1])
    codex_before = json.dumps(codex_settings, sort_keys=True)
    claude_before = json.dumps(claude_settings, sort_keys=True)
    codex_settings = update_hooks(codex_settings, codex_path, "codex", install=install)
    claude_settings = update_hooks(claude_settings, claude_path, "claude", install=install)
    claude_settings = update_claude_permission(claude_settings, claude_path, install=install)
    written: list[tuple[tuple[Path, bytes | None, int], bytes, str]] = []
    try:
        if json.dumps(codex_settings, sort_keys=True) != codex_before:
            content = files.write_settings(
                codex_path, codex_settings, snapshots[0][1], snapshots[0][2]
            )
            written.append((snapshots[0], content, "codex"))
        if json.dumps(claude_settings, sort_keys=True) != claude_before:
            content = files.write_settings(
                claude_path, claude_settings, snapshots[1][1], snapshots[1][2]
            )
            written.append((snapshots[1], content, "claude"))
    except (BusError, OSError):
        for snapshot, content, kind in reversed(written):
            restore_written(snapshot, content, kind)
        raise
    return [codex_path, claude_path]
