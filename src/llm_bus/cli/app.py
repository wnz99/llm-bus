"""Host identity and command dispatch."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

import llm_bus.collab.cli
from llm_bus.cli.commands import human_command, registered_command, send
from llm_bus.cli.hooks import claude_socket, hook
from llm_bus.cli.installation import configure_host_hooks
from llm_bus.cli.parser import build_parser
from llm_bus.collab.models import CollabError
from llm_bus.errors import BusError
from llm_bus.hosts.sessions import require_main_agent
from llm_bus.mailbox.connection import default_path
from llm_bus.mailbox.models import SenderContext
from llm_bus.mailbox.store import Store
from llm_bus.remote.bridge import run_bridge

if TYPE_CHECKING:
    from collections.abc import Sequence

CLAUDE_SESSION_ENV = "CLAUDE_CODE_SESSION_ID"


CODEX_THREAD_ENV = "CODEX_THREAD_ID"


CODEX_SESSION_ENV = "CODEX_SESSION_ID"


def host_identity(environment: dict[str, str]) -> tuple[str, str]:
    """Read native session identity from Claude or Codex shell tools."""
    claude_id = environment.get(CLAUDE_SESSION_ENV)
    codex_id = environment.get(CODEX_THREAD_ENV) or environment.get(CODEX_SESSION_ENV)
    if claude_id and codex_id:
        raise BusError("Both Claude and Codex session IDs are set; cannot choose sender")
    if claude_id:
        return "claude", claude_id
    if codex_id:
        return "codex", codex_id
    raise BusError("No host session ID found; run inside a Claude Code or Codex agent session")


def _optional_address() -> str | None:
    try:
        kind, session_id = host_identity(dict(os.environ))
    except BusError:
        return None
    agent_address = f"{kind}:{session_id}"
    require_main_agent(agent_address)
    return agent_address


def main(argv: Sequence[str] | None = None) -> int:
    """Run command, returning nonzero for input and storage errors."""
    args = cast("dict[str, object]", vars(build_parser().parse_args(argv)))
    command = args["command"]
    try:
        if command in {"install", "uninstall"}:
            print(json.dumps(configure_host_hooks(install=command == "install")))
            return 0
        store = Store.read_only(default_path()) if command == "status" else Store(default_path())
        if command in {"bridge", "hook"}:
            if command == "bridge":
                run_bridge(
                    store,
                    once=cast("bool", args["once"]),
                    refresh_status=cast("bool", args["refresh_status"]),
                )
            else:
                hook(store, cast("str", args["kind"]))
            return 0
        if human_command(store, command, args):
            return 0
        if command == "collab":
            result = llm_bus.collab.cli.run(args, _optional_address())
            if result is not None:
                print(json.dumps(result))
            return 0
        kind, session_id = host_identity(dict(os.environ))
        require_main_agent(f"{kind}:{session_id}")
        project = str(Path.cwd().resolve())
        if command == "send":
            result = send(store, args, SenderContext(kind, session_id, project))
        else:
            agent_address = store.register(
                kind, session_id, project, messaging_socket=claude_socket(kind)
            )
            result = registered_command(store, command, args, agent_address, project)
        print(json.dumps(result))
    except (
        BusError,
        CollabError,
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"llm-bus: {error}", file=sys.stderr)
        return 1
    return 0
