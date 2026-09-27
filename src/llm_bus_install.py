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


def _current_bytes(path: Path) -> bytes | None:
    if path.is_symlink():
        raise BusError(f"Settings symlink is unsupported; edit its target explicitly: {path}")
    return path.read_bytes() if path.exists() else None


def _parse_settings(path: Path, content: bytes | None) -> dict[str, object]:
    if content is None:
        return {}
    try:
        data = cast("object", json.loads(content))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
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


def _atomic_write(path: Path, content: bytes, mode: int, expected: bytes | None) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
        if _current_bytes(path) != expected:
            raise BusError(f"Settings changed while updating {path}")
        Path(temporary).replace(path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


def _write_settings(
    path: Path, settings: dict[str, object], expected: bytes | None, mode: int
) -> bytes:
    if _current_bytes(path) != expected:
        raise BusError(f"Settings changed while updating {path}")
    content = (json.dumps(settings, indent=2) + "\n").encode()
    _atomic_write(path, content, mode, expected)
    return content


def snapshot_host_settings(home: Path) -> SettingsSnapshot:
    """Capture both host settings from one validated read per file."""
    paths = (home / ".codex" / "hooks.json", home / ".claude" / "settings.json")
    snapshots: SettingsSnapshot = []
    for path in paths:
        content = _current_bytes(path)
        _parse_settings(path, content)
        mode = stat.S_IMODE(path.stat().st_mode) if content is not None else 0o600
        snapshots.append((path, content, mode))
    return snapshots


def _had_bus_hook(settings: dict[str, object], kind: str, event: str) -> bool:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False
    groups = cast("dict[str, object]", hooks).get(event)
    if not isinstance(groups, list):
        return False
    own_handler = cast("list[object]", _hook_group(kind)["hooks"])[0]
    return any(_group_has_handler(group, own_handler) for group in cast("list[object]", groups))


def _group_has_handler(group: object, handler: object) -> bool:
    if not isinstance(group, dict):
        return False
    handlers = cast("dict[str, object]", group).get("hooks")
    return isinstance(handlers, list) and handler in cast("list[object]", handlers)


def _restore_bus_entries(
    current: dict[str, object], original: dict[str, object], path: Path, kind: str
) -> dict[str, object]:
    restored = _update_hooks(current, path, kind, install=False)
    hooks = _object_at(restored, "hooks", path)
    for event in EVENTS:
        if _had_bus_hook(original, kind, event):
            groups = cast("list[object]", hooks.get(event, []))
            hooks[event] = [*groups, _hook_group(kind)]
    if hooks:
        restored["hooks"] = hooks
    if kind == "claude":
        permissions = original.get("permissions")
        allowed: object = (
            cast("dict[str, object]", permissions).get("allow", [])
            if isinstance(permissions, dict)
            else []
        )
        restored = _update_claude_permission(
            restored,
            path,
            install=isinstance(allowed, list)
            and CLAUDE_ALLOW_RULE in cast("list[object]", allowed),
        )
    return restored


def _restore_written(snapshot: tuple[Path, bytes | None, int], written: bytes, kind: str) -> None:
    path, content, mode = snapshot
    current = _current_bytes(path)
    if current != written:
        restored = _restore_bus_entries(
            _parse_settings(path, current), _parse_settings(path, content), path, kind
        )
        if restored != _parse_settings(path, current):
            current_mode = stat.S_IMODE(path.stat().st_mode) if current is not None else 0o600
            _write_settings(path, restored, current, current_mode)
        return
    if content is None:
        path.unlink(missing_ok=True)
    else:
        _atomic_write(path, content, mode, written)


def configure_hosts(home: Path, *, install: bool) -> list[Path]:
    """Merge or remove bus-owned hooks in both user settings files."""
    codex_path = home / ".codex" / "hooks.json"
    claude_path = home / ".claude" / "settings.json"
    snapshots = snapshot_host_settings(home)
    codex_settings = _parse_settings(codex_path, snapshots[0][1])
    claude_settings = _parse_settings(claude_path, snapshots[1][1])
    codex_before = json.dumps(codex_settings, sort_keys=True)
    claude_before = json.dumps(claude_settings, sort_keys=True)
    codex_settings = _update_hooks(codex_settings, codex_path, "codex", install=install)
    claude_settings = _update_hooks(claude_settings, claude_path, "claude", install=install)
    claude_settings = _update_claude_permission(claude_settings, claude_path, install=install)
    written: list[tuple[tuple[Path, bytes | None, int], bytes, str]] = []
    try:
        if json.dumps(codex_settings, sort_keys=True) != codex_before:
            content = _write_settings(codex_path, codex_settings, snapshots[0][1], snapshots[0][2])
            written.append((snapshots[0], content, "codex"))
        if json.dumps(claude_settings, sort_keys=True) != claude_before:
            content = _write_settings(
                claude_path, claude_settings, snapshots[1][1], snapshots[1][2]
            )
            written.append((snapshots[1], content, "claude"))
    except (BusError, OSError):
        for snapshot, content, kind in reversed(written):
            _restore_written(snapshot, content, kind)
        raise
    return [codex_path, claude_path]
