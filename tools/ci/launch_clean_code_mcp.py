#!/usr/bin/env python3
"""Launch the externally installed Clean Code Tools MCP runtime."""

from __future__ import annotations

import os
from pathlib import Path
from typing import NoReturn

RUNTIME_HOME_ENV = "CLEAN_CODE_TOOLS_HOME"
INDEX_BASE_ENV = "CLEAN_CODE_INDEX_BASE"
VECTOR_INDEX_ENV = "CLEAN_CODE_VECTOR_INDEX_PATH"
DEFAULT_RUNTIME_HOME = ".clean-code-tools"
SERVER_RELATIVE_PATH = Path("runtime/scripts/clean_code_mcp_server.py")


def runtime_home(environment: dict[str, str]) -> Path:
    configured_home = environment.get(RUNTIME_HOME_ENV)
    if configured_home:
        return Path(configured_home).expanduser().resolve()
    return (Path.home() / DEFAULT_RUNTIME_HOME).resolve()


def runtime_python(home: Path, *, platform_name: str) -> Path:
    if platform_name == "nt":
        return home / ".venv" / "Scripts" / "python.exe"
    return home / ".venv" / "bin" / "python"


def launch_command(home: Path, *, platform_name: str) -> tuple[Path, Path]:
    return runtime_python(home, platform_name=platform_name), home / SERVER_RELATIVE_PATH


def require_file(path: Path, *, description: str) -> None:
    if not path.is_file():
        raise SystemExit(missing_runtime_message(path, description=description))


def missing_runtime_message(path: Path, *, description: str) -> str:
    return (
        f"Clean Code Tools {description} not found at {path}. "
        "Install the external runtime at that path or set CLEAN_CODE_TOOLS_HOME "
        "to its installation directory before starting the host."
    )


def main() -> NoReturn:
    home = runtime_home(dict(os.environ))
    interpreter, server = launch_command(home, platform_name=os.name)
    require_file(interpreter, description="virtual-environment interpreter")
    require_file(server, description="MCP server")

    environment = dict(os.environ)
    environment[INDEX_BASE_ENV] = str(home)
    environment[VECTOR_INDEX_ENV] = str(home / "clean-code-index.sqlite")
    os.execve(interpreter, [str(interpreter), str(server)], environment)


if __name__ == "__main__":
    main()
