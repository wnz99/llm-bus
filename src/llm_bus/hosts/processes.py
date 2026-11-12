"""Identify the host process behind a session so the bus can tell when it exits."""

from __future__ import annotations

import os
import subprocess  # nosec B404 - fixed ps arguments, no shell
from pathlib import Path
from typing import NamedTuple

PS_TIMEOUT_SECONDS = 2
# Fixed locale and zone keep start times comparable between the recording and checking runs.
PS_ENVIRONMENT = {"LC_ALL": "C", "TZ": "UTC", "PATH": "/bin:/usr/bin"}
ID_FIELDS = 2  # pid and ppid
START_FIELDS = 5  # lstart, such as "Thu Nov 12 01:00:00 2026"
HOST_KINDS = frozenset({"claude", "codex"})


class HostProcess(NamedTuple):
    pid: int
    started: str


def _ps(arguments: list[str]) -> str | None:
    try:
        result = subprocess.run(  # noqa: S603 - fixed ps arguments, no shell
            ["ps", *arguments],  # noqa: S607 - PATH is fixed in PS_ENVIRONMENT
            capture_output=True,
            text=True,
            timeout=PS_TIMEOUT_SECONDS,
            env=PS_ENVIRONMENT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout


def host_process(kind: str) -> HostProcess | None:
    """Find the nearest ancestor whose executable is the host CLI, such as `claude`."""
    ancestors = _ancestors()
    return next((process for name, process in ancestors or [] if name == kind), None)


def under_agent_host() -> bool:
    """Whether any ancestor is a Claude Code or Codex process; unknown counts as yes."""
    ancestors = _ancestors()
    return ancestors is None or any(name in HOST_KINDS for name, _process in ancestors)


def _ancestors() -> list[tuple[str, HostProcess]] | None:
    """This process's ancestors, nearest first, by executable name; None if ps failed."""
    output = _ps(["-A", "-o", "pid=,ppid=,lstart=,comm="])
    if output is None:
        return None
    table: dict[int, tuple[int, str, str]] = {}
    for line in output.splitlines():
        fields = line.split(maxsplit=ID_FIELDS + START_FIELDS)
        if len(fields) <= ID_FIELDS + START_FIELDS or not (
            fields[0].isdigit() and fields[1].isdigit()
        ):
            continue
        started = " ".join(fields[ID_FIELDS:-1])
        table[int(fields[0])] = (int(fields[1]), started, Path(fields[-1]).name)
    ancestors: list[tuple[str, HostProcess]] = []
    pid = os.getppid()
    seen: set[int] = set()
    while pid in table and pid not in seen:
        seen.add(pid)
        parent, started, name = table[pid]
        ancestors.append((name, HostProcess(pid, started)))
        pid = parent
    return ancestors


def exited(processes: list[HostProcess]) -> set[HostProcess] | None:
    """Recorded processes that are gone or whose PID now names a later process.

    Returns None when ps cannot be trusted: it failed, or it did not list this process.
    """
    own = os.getpid()
    pids = ",".join(str(pid) for pid in {own, *(item.pid for item in processes)})
    output = _ps(["-o", "pid=,lstart=", "-p", pids])
    if output is None:
        return None
    running: dict[int, str] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 1 + START_FIELDS and fields[0].isdigit():
            running[int(fields[0])] = " ".join(fields[1:])
    if own not in running:
        return None
    return {item for item in processes if running.get(item.pid) != item.started}
