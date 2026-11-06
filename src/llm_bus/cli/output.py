"""Agent-facing projections; expanded JSON remains the compatibility contract."""

import json
import unicodedata
from typing import cast


def serialize(result: object, command: str, output_format: str = "json") -> str:
    """Serialize output without exposing terminal formatting controls."""
    if output_format == "json":
        return json.dumps(result)
    content = json.dumps(project(result, command), ensure_ascii=False, separators=(",", ":"))
    return "".join(
        json.dumps(char)[1:-1] if unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} else char
        for char in content
    )


def project(result: object, command: str) -> object:
    if command == "send" and isinstance(result, dict):
        return _optional(
            _omit(
                cast("dict[str, object]", result),
                {"body", "sender", "sender_cwd", "recipient_cwd", "sent_at"},
            )
        )
    if command in {"inbox", "history", "list", "agents"} and isinstance(result, list):
        rows = cast("list[dict[str, object]]", result)
        omitted = {"kind"} if command in {"list", "agents"} else {"wake_checked_at"}
        cleaned = [_optional(_omit(row, omitted)) for row in rows]
        return _share(cleaned, {"project", "sender_cwd", "recipient_cwd", "recipient"})
    if command == "lock" and isinstance(result, dict):
        record = dict(cast("dict[str, object]", result))
        if "resources" in record:
            resources = cast("list[dict[str, object]]", record["resources"])
            record["resources"] = _share(
                [_omit(row, {"created_by", "last_used_at"}) for row in resources], {"scope"}
            )
        if record.get("notices") == []:
            del record["notices"]
        return record
    return result


def _omit(row: dict[str, object], fields: set[str]) -> dict[str, object]:
    return {key: value for key, value in row.items() if key not in fields}


def _optional(row: dict[str, object]) -> dict[str, object]:
    optional = {
        "reply_to",
        "ended_at",
        "acknowledged_at",
        "wake_status",
        "wake_via",
        "wake_reason",
        "sender_cwd",
        "recipient_cwd",
    }
    return {key: value for key, value in row.items() if value is not None or key not in optional}


def _share(rows: list[dict[str, object]], fields: set[str]) -> object:
    """Shared fields apply to every item, without aliases or message truncation."""
    if len(rows) <= 1:
        return rows
    common = {
        key: value
        for key, value in rows[0].items()
        if key in fields and all(key in row and row[key] == value for row in rows[1:])
    }
    if not common:
        return rows
    return {"shared": common, "items": [_omit(row, set(common)) for row in rows]}
