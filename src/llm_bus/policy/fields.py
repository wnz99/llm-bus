from __future__ import annotations

import re
from typing import cast

from llm_bus.errors import BusError

IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}")


def object_fields(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise BusError("Remote data must be an object")
    return cast("dict[str, object]", value)


def required_text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise BusError("Remote field must be a nonempty string")
    return value


def identifier(value: object) -> str:
    result = required_text(value)
    if not IDENTIFIER.fullmatch(result):
        raise BusError("Invalid remote machine/repository ID")
    return result


def array_values(value: object) -> list[object]:
    if not isinstance(value, list):
        raise BusError("Remote field must be an array")
    return cast("list[object]", value)
