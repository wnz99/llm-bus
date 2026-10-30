"""Repository scope identity shared by mailbox storage and remote policy."""

import os
import shutil
import subprocess
from pathlib import Path

GIT_ENV_PREFIX = "GIT_"  # pylint: disable=clean-code-business-policy-literal


def routing_scope(project: str) -> str:
    """Use one identity for linked Git worktrees, otherwise the folder path."""
    folder = Path(project).resolve()
    git = shutil.which("git")
    if git is None:
        return str(folder)
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(GIT_ENV_PREFIX)
    }
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [git, "-C", str(folder), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return str(folder)
    common_dir = result.stdout.strip()
    if result.returncode or not common_dir:
        return str(folder)
    return f"git:{(folder / common_dir).resolve()}"
