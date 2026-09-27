"""Install and remove user-level host hooks without replacing other settings."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import cast

from llm_bus_store import BusError

EVENTS = ("SessionStart", "UserPromptSubmit")
CLAUDE_ALLOW_RULE = "Bash(llm-bus *)"
type SettingsSnapshot = list[tuple[Path, bytes | None, int]]


def _hook_group(kind: str) -> dict[str, object]:
    return {"hooks": [{"type": "command", "command": f"llm-bus hook {kind}"}]}


def _without_bus_handler(group: object, kind: str) -> object | None:
    if not isinstance(group, dict):
        return group
    configured = cast("dict[str, object]", group)
    handlers = configured.get("hooks")
    if not isinstance(handlers, list):
        return configured
    own_handler = cast("list[object]", _hook_group(kind)["hooks"])[0]
    retained = [handler for handler in cast("list[object]", handlers) if handler != own_handler]
    if not retained:
        return None
    return {**configured, "hooks": retained}


def _read_settings(path: Path) -> dict[str, object]:
    if path.is_symlink():
        raise BusError(f"Settings symlink is unsupported; edit its target explicitly: {path}")
    if not path.exists():
        return {}
    try:
        data = cast("object", json.loads(path.read_text()))
    except json.JSONDecodeError as error:
        raise BusError(f"Invalid JSON in {path}: {error}") from error
    if not isinstance(data, dict):
        raise BusError(f"Settings file must contain a JSON object: {path}")
    return cast("dict[str, object]", data)


def _object_at(settings: dict[str, object], key: str, path: Path) -> dict[str, object]:
    value = settings.get(key, {})
    if not isinstance(value, dict):
        raise BusError(f"{key} must be a JSON object in {path}")
    return dict(cast("dict[str, object]", value))


def _update_hooks(
    settings: dict[str, object], path: Path, kind: str, *, install: bool
) -> dict[str, object]:
    updated = dict(settings)
    hooks = _object_at(settings, "hooks", path)
    for event in EVENTS:
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            raise BusError(f"hooks.{event} must be a JSON array in {path}")
        retained = [
            cleaned
            for group in cast("list[object]", groups)
            if (cleaned := _without_bus_handler(group, kind)) is not None
        ]
        if install:
            retained.append(_hook_group(kind))
        if retained:
            hooks[event] = retained
        else:
            hooks.pop(event, None)
    if not hooks:
        updated.pop("hooks", None)
    else:
        updated["hooks"] = hooks
    return updated


def _update_claude_permission(
    settings: dict[str, object], path: Path, *, install: bool
) -> dict[str, object]:
    updated = dict(settings)
    permissions = _object_at(settings, "permissions", path)
    allowed = permissions.get("allow", [])
    if not isinstance(allowed, list) or any(
        not isinstance(rule, str) for rule in cast("list[object]", allowed)
    ):
        raise BusError(f"permissions.allow must be an array of strings in {path}")
    rules = [rule for rule in cast("list[str]", allowed) if rule != CLAUDE_ALLOW_RULE]
    if install:
        rules.append(CLAUDE_ALLOW_RULE)
    if rules:
        permissions["allow"] = rules
    else:
        permissions.pop("allow", None)
    if not permissions:
        updated.pop("permissions", None)
    else:
        updated["permissions"] = permissions
    return updated


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
        Path(temporary).replace(path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


def _write_settings(path: Path, settings: dict[str, object]) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    content = (json.dumps(settings, indent=2) + "\n").encode()
    _atomic_write(path, content, mode)


def snapshot_host_settings(home: Path) -> SettingsSnapshot:
    """Capture exact settings bytes for uninstall rollback."""
    paths = (home / ".codex" / "hooks.json", home / ".claude" / "settings.json")
    snapshots: SettingsSnapshot = []
    for path in paths:
        _read_settings(path)
        if path.exists():
            snapshots.append((path, path.read_bytes(), stat.S_IMODE(path.stat().st_mode)))
        else:
            snapshots.append((path, None, 0o600))
    return snapshots


def restore_host_settings(snapshots: SettingsSnapshot) -> None:
    """Restore exact config files after failed tool removal."""
    for path, content, mode in snapshots:
        if content is None:
            path.unlink(missing_ok=True)
        else:
            _atomic_write(path, content, mode)


def configure_hosts(home: Path, *, install: bool) -> list[Path]:
    """Merge or remove bus-owned hooks in both user settings files."""
    codex_path = home / ".codex" / "hooks.json"
    claude_path = home / ".claude" / "settings.json"
    snapshots = snapshot_host_settings(home)
    codex_settings = _read_settings(codex_path)
    claude_settings = _read_settings(claude_path)
    codex_before = json.dumps(codex_settings, sort_keys=True)
    claude_before = json.dumps(claude_settings, sort_keys=True)
    codex_settings = _update_hooks(codex_settings, codex_path, "codex", install=install)
    claude_settings = _update_hooks(claude_settings, claude_path, "claude", install=install)
    claude_settings = _update_claude_permission(claude_settings, claude_path, install=install)
    try:
        if json.dumps(codex_settings, sort_keys=True) != codex_before:
            _write_settings(codex_path, codex_settings)
        if json.dumps(claude_settings, sort_keys=True) != claude_before:
            _write_settings(claude_path, claude_settings)
    except OSError:
        restore_host_settings(snapshots)
        raise
    return [codex_path, claude_path]
