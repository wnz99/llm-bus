"""Collaboration invites ownership."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import cast

from llm_bus.collab.models import (
    INVITE_PREFIX,
    MAX_INVITE_CHARS,
    MAX_REPOSITORY_NAME,
    MODES,
    TOKEN,
    CollabError,
    Invite,
)
from llm_bus.collab.policy import github_user, now, parse_stamp


def pair_id(nonce: str) -> str:
    return hashlib.sha256(f"llm-bus-collab-pair|{nonce}".encode()).hexdigest()[:32]


def encode_invite(invite: Invite) -> str:
    raw = json.dumps(invite, sort_keys=True, separators=(",", ":")).encode()
    return INVITE_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _invite_fields(text: str) -> dict[str, object]:
    text = text.strip()
    if len(text) > MAX_INVITE_CHARS or not text.startswith(INVITE_PREFIX):
        raise CollabError("Not an llm-bus invite")
    payload = text.removeprefix(INVITE_PREFIX)
    try:
        data = cast(
            "object", json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        )
    except (ValueError, binascii.Error) as exc:
        raise CollabError("Invite is malformed") from exc
    if not isinstance(data, dict):
        raise CollabError("Invite is malformed")
    fields = cast("dict[str, object]", data)
    expected = set(Invite.__annotations__)
    if set(fields) != expected or fields.get("v") != 0:
        raise CollabError("Invite has an unsupported format")
    if not all(isinstance(fields[key], str) for key in expected - {"v"}):
        raise CollabError("Invite is malformed")
    return fields


def decode_invite(text: str) -> Invite:
    """Parse an invite as untrusted data; nothing in it grants local permission."""
    invite = cast("Invite", cast("object", _invite_fields(text)))
    github_user(invite["host_user"])
    github_user(invite["joiner_user"])
    if (
        not TOKEN.fullmatch(invite["nonce"])
        or invite["grant"] not in MODES
        or len(invite["repository_name"]) > MAX_REPOSITORY_NAME
    ):
        raise CollabError("Invite is malformed")
    try:
        expires = parse_stamp(invite["expires_at"])
    except ValueError as exc:
        raise CollabError("Invite is malformed") from exc
    if expires <= now():
        raise CollabError("Invite has expired")
    return invite
