"""Identify and merge bus-owned hook and permission entries."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import cast

from llm_bus.errors import BusError

EVENTS = ("SessionStart", "UserPromptSubmit", "SessionEnd")


CLAUDE_ALLOW_RULE = "Bash(llm-bus *)"


MISSING_EXECUTABLE_MESSAGE = (
    "Cannot find llm-bus on PATH; run uv tool update-shell and retry installation"
)


def hook_group(kind: str) -> dict[str, object]:
    executable = shutil.which("llm-bus")
    if executable is None:
        raise BusError(MISSING_EXECUTABLE_MESSAGE)
    arguments = [str(Path(executable).absolute()), "hook", kind]
    command = subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)
    return {"hooks": [{"type": "command", "command": command}]}


def is_bus_handler(handler: object, kind: str) -> bool:
    if not isinstance(handler, dict):
        return False
    configured = cast("dict[str, object]", handler)
    command = configured.get("command")
    if configured.get("type") != "command" or not isinstance(command, str):
        return False
    try:
        arguments = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return False
    return (
        bool(arguments)
        and Path(arguments[0].strip('"')).name.lower() in {"llm-bus", "llm-bus.exe"}
        and arguments[1:] == ["hook", kind]
    )


def without_bus_handler(group: object, kind: str) -> object | None:
    if not isinstance(group, dict):
        return group
    configured = cast("dict[str, object]", group)
    handlers = configured.get("hooks")
    if not isinstance(handlers, list):
        return configured
    retained = [
        handler for handler in cast("list[object]", handlers) if not is_bus_handler(handler, kind)
    ]
    if not retained:
        return None
    return {**configured, "hooks": retained}


def object_at(settings: dict[str, object], key: str, path: Path) -> dict[str, object]:
    value = settings.get(key, {})
    if not isinstance(value, dict):
        raise BusError(f"{key} must be a JSON object in {path}")
    return dict(cast("dict[str, object]", value))


def update_hooks(
    settings: dict[str, object], path: Path, kind: str, *, install: bool
) -> dict[str, object]:
    updated = dict(settings)
    hooks = object_at(settings, "hooks", path)
    for event in EVENTS:
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            raise BusError(f"hooks.{event} must be a JSON array in {path}")
        retained = [
            cleaned
            for group in cast("list[object]", groups)
            if (cleaned := without_bus_handler(group, kind)) is not None
        ]
        if install:
            retained.append(hook_group(kind))
        if retained:
            hooks[event] = retained
        else:
            hooks.pop(event, None)
    if not hooks:
        updated.pop("hooks", None)
    else:
        updated["hooks"] = hooks
    return updated


def update_claude_permission(
    settings: dict[str, object], path: Path, *, install: bool
) -> dict[str, object]:
    updated = dict(settings)
    permissions = object_at(settings, "permissions", path)
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
