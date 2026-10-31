"""Credentials ownership."""

from __future__ import annotations

import os
import re
import stat
from typing import TYPE_CHECKING

from llm_bus.errors import BusError

if TYPE_CHECKING:
    from pathlib import Path

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43,128}")


def read_token(path: Path) -> str:
    if path.is_symlink():
        raise BusError("Credential file must not be a symlink")
    # Platform flag names are fixed OS APIs, not configurable policy.
    # pylint: disable-next=clean-code-business-policy-literal
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise BusError("Credential must be a regular file")
        if os.name == "posix" and (metadata.st_mode & 0o077 or metadata.st_uid != os.getuid()):
            raise BusError("Credential file must be owned by this user with mode 600")
        token = stream.read(129).strip()
    try:
        result = token.decode("ascii")
    except UnicodeDecodeError as error:
        raise BusError("Invalid machine credential format") from error
    if not TOKEN_PATTERN.fullmatch(result):
        raise BusError("Invalid machine credential format")
    return result
